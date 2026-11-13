"""Расчет аналитики аномалий по данным хранилища и сохранение результатов.

    python -m sma.analytics_job              один расчет и выход (make rules)

Данные — из витрин mart, расчет — модуль аналитика sma.sma_anomalies (его формулы
и пороги здесь не меняются), результаты — в схему analytics. Запуск и все его
результаты пишутся одной транзакцией вместе с отметкой status = 'ok': дашборд
не увидит половину расчета, а пустой результат отличается от отсутствия расчета.
По расписанию (FR-30: каждые 10 минут во время экзамена и один раз после него)
расчет запускает загрузчик — класс AnalysisRunner.

Подключение — под ролью sma_app (пароль SMA_APP_PASSWORD): читает mart, пишет analytics.
"""

import argparse
import logging
import math
import os
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from psycopg import Connection
from psycopg.types.json import Jsonb

from sma.db import connect
from sma.sma_anomalies import Params, analyze_platform
from sma.sma_anomalies.loader import ExamData
from sma.sma_anomalies.runlog import Scheduler
from sma.timeutil import utc_now

logger = logging.getLogger(__name__)

EXAMS = ("EGE", "OGE", "VPR", "NIKO")
DETAIL_COLUMNS = ("z", "region_mean", "sigma", "schools_in_region", "variant_id")

SESSIONS = """
SELECT record_id, participant_id, exam_type, subject_code, variant_id, region_code,
       school_code, room_code, status, result_status, primary_score, max_primary_score,
       updated_at, false AS deleted
FROM mart.sessions_current WHERE exam_type = %s
"""
ANSWERS = """
SELECT participant_id, exam_type, task_number, answer, is_correct, false AS deleted
FROM mart.answers_flat WHERE exam_type = %s
"""
EVENTS = """
SELECT participant_id, exam_type, event_type, occurred_at, false AS deleted
FROM mart.proctoring_events WHERE exam_type = %s
"""
SUBJECTS = """
SELECT exam_type, subject_code, threshold_score, max_primary_score, false AS deleted
FROM mart.subjects WHERE exam_type = %s
"""
SCHOOLS = "SELECT school_code, region_code FROM mart.schools"


# --- Входные данные ---------------------------------------------------------------------------


def read_frame(conn: Connection, query: str, params=()) -> pd.DataFrame:
    cursor = conn.execute(query, params)
    return pd.DataFrame(cursor.fetchall(), columns=[column.name for column in cursor.description])


def read_inputs(conn: Connection) -> dict[str, ExamData]:
    """Таблицы для модуля аналитика по каждой платформе, где есть сессии."""
    schools = read_frame(conn, SCHOOLS)
    datasets = {}
    for exam_type in EXAMS:
        sessions = read_frame(conn, SESSIONS, (exam_type,))
        if sessions.empty:
            continue
        datasets[exam_type] = ExamData(
            exam_type,
            sessions,
            read_frame(conn, ANSWERS, (exam_type,)),
            read_frame(conn, EVENTS, (exam_type,)),
            read_frame(conn, SUBJECTS, (exam_type,)),
            schools,
        )
    return datasets


def snapshot_moment(datasets: dict[str, ExamData]) -> datetime | None:
    """Момент снимка данных — наибольший updated_at сессий (так считает и модуль аналитика)."""
    moments = [data.sessions["updated_at"].max() for data in datasets.values()]
    return max(moments) if moments else None


# --- Сохранение -------------------------------------------------------------------------------


def plain(value: object) -> object:
    """numpy и pandas → обычные типы Python; NaN → None."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def id_list(value: object) -> list[str] | None:
    """Строка "p_1,p_2" из модуля аналитика → список."""
    value = plain(value)
    return [item for item in str(value).split(",") if item] if value else None


SAVE_ANOMALY = """
INSERT INTO analytics.anomalies
    (rule, exam_type, subject_code, object_type, object_id, region_code, school_code,
     value, threshold, detected_at, run_id, last_run_id, details)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (rule, exam_type, object_type, object_id)
DO UPDATE SET last_run_id = EXCLUDED.last_run_id
"""

SAVE_SCHOOL = """
INSERT INTO analytics.school_index
    (run_id, exam_type, subject_code, region_code, school_code, participants,
     p1, p2, p3, p4a, p4b, z1, z2, z3, z4a, z4b, index, in_list)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

SAVE_GROUP = """
INSERT INTO analytics.p1_groups
    (run_id, group_id, exam_type, subject_code, region_code, school_code, room_code,
     variant_id, participant_ids, size, min_p_value, min_p_raw)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

SCHOOL_FIELDS = ("exam_type", "subject_code", "region_code", "school_code", "participants",
                 "p1", "p2", "p3", "p4a", "p4b", "z1", "z2", "z3", "z4a", "z4b", "index",
                 "in_list")  # fmt: skip


def anomaly_rows(frame: pd.DataFrame, run_id: int) -> list[tuple]:
    rows = []
    for record in frame.to_dict("records"):
        details = {key: plain(record[key]) for key in DETAIL_COLUMNS if key in record}
        details = {key: value for key, value in details.items() if value is not None}
        members = id_list(record.get("participant_ids"))
        if members:
            details["participant_ids"] = members
        rows.append(
            (
                record["rule"], record["exam_type"], plain(record["subject_code"]),
                record["object_type"], str(record["object_id"]), plain(record["region_code"]),
                plain(record["school_code"]), plain(record["value"]), plain(record["threshold"]),
                datetime.fromisoformat(record["detected_at"]), run_id, run_id, Jsonb(details),
            )
        )  # fmt: skip
    return rows


def save_results(conn: Connection, run_id: int, results: dict[str, dict]) -> dict:
    """Аномалии (с заменой по ключу), показатели школ и группы П1 одного запуска."""
    anomalies, schools, groups = [], [], []
    for result in results.values():
        if not result["anomalies"].empty:
            anomalies += anomaly_rows(result["anomalies"], run_id)
        for record in result["schools"].to_dict("records"):
            schools.append((run_id, *(plain(record[name]) for name in SCHOOL_FIELDS)))
        for record in result["groups"].to_dict("records"):
            groups.append(record)
    with conn.cursor() as cur:
        if anomalies:
            cur.executemany(SAVE_ANOMALY, anomalies)
        if schools:
            cur.executemany(SAVE_SCHOOL, schools)
        group_rows = [
            (run_id, number, g["exam_type"], plain(g["subject_code"]), plain(g.get("region_code")),
             g["school_code"], plain(g["room_code"]), plain(g["variant_id"]),
             id_list(g["participant_ids"]), plain(g["size"]), plain(g["min_p_value"]),
             plain(g.get("min_p_raw")))
            for number, g in enumerate(groups, 1)
        ]  # fmt: skip
        if group_rows:
            cur.executemany(SAVE_GROUP, group_rows)
    return {
        "anomalies": len(anomalies),
        "schools": len(schools),
        "schools_in_list": sum(bool(row[-1]) for row in schools),
        "groups": len(groups),
    }


# --- Запуск ------------------------------------------------------------------------------------


def run_analysis(conn: Connection, reason: str = "manual", params: Params | None = None) -> int:
    """Один расчет по всем платформам с данными. Возвращает run_id.

    Ошибка расчета или сохранения: результаты откатываются, запуск помечается error
    (в витринах его нет), исключение пробрасывается дальше.
    """
    params = params or Params()
    run_id = conn.execute(
        "INSERT INTO analytics.analysis_runs (started_at, reason, params) "
        "VALUES (%s, %s, %s) RETURNING run_id",
        (utc_now(), reason, Jsonb(params.as_dict())),
    ).fetchone()[0]
    conn.commit()
    try:
        datasets = read_inputs(conn)
        moment = snapshot_moment(datasets)
        as_of = (moment or utc_now()).isoformat()
        results = {
            exam_type: analyze_platform(data, params, as_of=as_of)
            for exam_type, data in datasets.items()
        }
        scope = [
            {"exam_type": exam_type, "subject_code": subject}
            for exam_type, data in datasets.items()
            for subject in sorted(data.sessions["subject_code"].unique())
        ]
        summary = save_results(conn, run_id, results)
        conn.execute(
            "UPDATE analytics.analysis_runs SET status = 'ok', finished_at = %s, "
            "data_as_of = %s, scope = %s, summary = %s WHERE run_id = %s",
            (utc_now(), moment, Jsonb(scope), Jsonb(summary), run_id),
        )
        conn.commit()
    except Exception as problem:
        conn.rollback()
        conn.execute(
            "UPDATE analytics.analysis_runs SET status = 'error', finished_at = %s, error = %s "
            "WHERE run_id = %s",
            (utc_now(), str(problem)[:2000], run_id),
        )
        conn.commit()
        raise
    logger.info("Аналитика: запуск %d (%s) — %s", run_id, reason, summary)
    return run_id


def app_connection() -> Connection:
    """Соединение под ролью sma_app."""
    return connect(user="sma_app", password=os.environ.get("SMA_APP_PASSWORD", ""))


class AnalysisRunner:
    """Расчет по расписанию FR-30 для загрузчика: tick() вызывается раз в секунду,
    проверка расписания (планировщик модуля аналитика) — раз в минуту.
    """

    CHECK_SEC = 60

    def __init__(self) -> None:
        self.scheduler = Scheduler()
        self.next_check = 0.0

    def tick(self, monotonic_now: float) -> None:
        if monotonic_now < self.next_check:
            return
        self.next_check = monotonic_now + self.CHECK_SEC
        try:
            with app_connection() as conn:
                sessions = read_frame(
                    conn,
                    "SELECT status, result_status, false AS deleted FROM mart.sessions_current",
                )
                reason = self.scheduler.tick(utc_now(), sessions)
                if reason:
                    run_analysis(conn, reason)
        except Exception:  # аналитика не должна останавливать загрузку
            logger.exception("Расчет аналитики не выполнен")


def main() -> None:
    parser = argparse.ArgumentParser(description="Расчет аналитики аномалий СМА")
    parser.add_argument("--reason", default="manual", help="причина запуска для журнала")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()
    with app_connection() as conn:
        run_analysis(conn, args.reason)


if __name__ == "__main__":
    main()
