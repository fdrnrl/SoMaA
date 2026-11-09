"""Проверки качества данных и консистентности витрин (раздел 13).

Каждая проверка — функция (conn) → список Issue. Нарушения группируются: одна строка
на (проверка, экзамен, что нарушено) с числом случаев и примерами ключей, чтобы
ops.quality_issues не разрастался до тысяч строк. Проверки ничего не меняют в данных
и не делают commit.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from psycopg import Connection

from sma import contract
from sma.contract import DATETIME

EXAMPLES = 10
LEVEL_ORDER = {"below_basic": 0, "basic": 1, "above_basic": 2}

CURRENT_ANSWERS = """
    SELECT DISTINCT ON (exam_type, participant_id, task_number) *
    FROM core.answers WHERE NOT deleted
    ORDER BY exam_type, participant_id, task_number, updated_at DESC, record_id DESC
"""
CURRENT_REVIEWS = """
    SELECT DISTINCT ON (exam_type, participant_id, task_number) *
    FROM core.expert_reviews WHERE NOT deleted
    ORDER BY exam_type, participant_id, task_number, updated_at DESC, record_id DESC
"""
DONE = "status = 'finished' AND result_status <> 'pending'"


@dataclass
class Issue:
    """Нарушение одной проверки."""

    check_code: str
    severity: str  # error, warning, info
    exam_type: str | None
    entity: str
    entity_key: str | None
    details: dict = field(default_factory=dict)


def grouped(code: str, severity: str, entity: str, rows, message: str) -> list[Issue]:
    """Строки (exam_type, ключ) → одно Issue на экзамен: число случаев и примеры ключей."""
    keys: dict[str | None, list[str]] = defaultdict(list)
    for exam_type, key in rows:
        keys[exam_type].append(str(key))
    return [
        Issue(code, severity, exam_type, entity, examples[0],
              {"message": message, "count": len(examples), "examples": examples[:EXAMPLES]})
        for exam_type, examples in sorted(keys.items(), key=lambda pair: str(pair[0]))
    ]  # fmt: skip


def fetch(conn: Connection, query: str, params=()) -> list[tuple]:
    return conn.execute(query, params).fetchall()


# --- Полнота ---------------------------------------------------------------------------------


def q_compl_01(conn: Connection) -> list[Issue]:
    """Доля отклоненных записей по платформе и адресу за последний час."""
    rows = fetch(
        conn,
        """
        SELECT platform, endpoint, sum(received), sum(rejected)
        FROM ops.load_log
        WHERE started_at > now() - interval '1 hour'
        GROUP BY platform, endpoint
        """,
    )
    issues = []
    for platform, endpoint, received, rejected in rows:
        if not received:
            continue
        share = rejected / received
        severity = "error" if share > 0.05 else "warning" if share > 0.01 else None
        if severity:
            details = {"message": "доля отклоненных записей за час", "share": round(share, 4),
                       "received": int(received), "rejected": int(rejected)}  # fmt: skip
            issues.append(Issue("Q-COMPL-01", severity, platform, endpoint, endpoint, details))
    return issues


def q_compl_02(conn: Connection) -> list[Issue]:
    """Пустые поля, которые по состоянию записи (6.12) должны быть заполнены."""
    rows = fetch(
        conn,
        f"""
        SELECT exam_type, participant_id || ': ' ||
               CASE WHEN status <> 'absent' AND started_at IS NULL THEN 'started_at'
                    WHEN status = 'finished' AND finished_at IS NULL THEN 'finished_at'
                    WHEN primary_score IS NULL THEN 'primary_score'
                    ELSE 'итог по шкале' END
        FROM mart.sessions_current
        WHERE (status <> 'absent' AND started_at IS NULL)
           OR (status = 'finished' AND finished_at IS NULL)
           OR ({DONE} AND (primary_score IS NULL
                           OR coalesce(grade::text, test_score::text, result_level) IS NULL))
        """,
    )
    return grouped("Q-COMPL-02", "error", "sessions", rows, "не заполнено обязательное поле")


# --- Уникальность ----------------------------------------------------------------------------

NATURAL_KEYS = {
    "sessions": ["participant_id"],
    "answers": ["participant_id", "task_number"],
    "expert_reviews": ["participant_id", "task_number"],
    "kim_tasks": ["subject_code", "exam_year", "variant_id", "task_number"],
    "scales": ["subject_code", "exam_year", "scale_version"],
}


def q_uniq_01(conn: Connection) -> list[Issue]:
    """Дубли естественных ключей с разными record_id."""
    issues = []
    for table, columns in NATURAL_KEYS.items():
        key = ", ".join(columns)
        rows = fetch(
            conn,
            f"""
            SELECT exam_type, concat_ws('/', {key})
            FROM core.{table} WHERE NOT deleted
            GROUP BY exam_type, {key}
            HAVING count(DISTINCT record_id) > 1
            """,
        )
        issues += grouped("Q-UNIQ-01", "error", table, rows, f"дубль ключа ({key})")
    return issues


# --- Согласованность ---------------------------------------------------------------------------


def q_cons_01(conn: Connection) -> list[Issue]:
    """Каждый ответ, оценка и событие имеет сессию."""
    issues = []
    for table in ("answers", "expert_reviews", "proctoring_events"):
        rows = fetch(
            conn,
            f"""
            SELECT DISTINCT x.exam_type, x.participant_id
            FROM core.{table} x
            WHERE NOT x.deleted AND NOT EXISTS (
                SELECT 1 FROM core.sessions s
                WHERE NOT s.deleted AND s.exam_type = x.exam_type
                  AND s.participant_id = x.participant_id)
            """,
        )
        issues += grouped("Q-CONS-01", "error", table, rows, "нет сессии участника")
    return issues


def q_cons_02(conn: Connection) -> list[Issue]:
    """У участника не больше одной сессии (одного предмета) на платформе."""
    rows = fetch(
        conn,
        """
        SELECT exam_type, participant_id FROM core.sessions WHERE NOT deleted
        GROUP BY exam_type, participant_id HAVING count(DISTINCT subject_code) > 1
        """,
    )
    return grouped("Q-CONS-02", "error", "sessions", rows, "несколько сессий участника")


def q_cons_03(conn: Connection) -> list[Issue]:
    """Первичный балл = Σ баллов за ответы + Σ проверенных оценок экспертов."""
    rows = fetch(
        conn,
        f"""
        WITH a AS (SELECT exam_type, participant_id, sum(score) AS total
                   FROM ({CURRENT_ANSWERS}) x GROUP BY 1, 2),
             r AS (SELECT exam_type, participant_id, sum(score) AS total
                   FROM ({CURRENT_REVIEWS}) x WHERE check_status = 'checked' GROUP BY 1, 2)
        SELECT s.exam_type,
               s.participant_id || ': ' || s.primary_score || ' ≠ '
                   || (coalesce(a.total, 0) + coalesce(r.total, 0))
        FROM mart.sessions_current s
        LEFT JOIN a USING (exam_type, participant_id)
        LEFT JOIN r USING (exam_type, participant_id)
        WHERE s.status = 'finished' AND s.result_status <> 'pending'
          AND s.primary_score IS DISTINCT FROM coalesce(a.total, 0) + coalesce(r.total, 0)
        """,
    )
    return grouped("Q-CONS-03", "error", "sessions", rows, "первичный балл не равен сумме баллов")


def scale_result(scale: dict, primary: int, block: dict) -> tuple[object, bool]:
    """Результат по шкале и «не выполнено условие по блоку»."""
    if scale["scale_type"] == "primary_to_test":
        tests = {point["primary"]: point["test"] for point in scale["points"]}
        return tests.get(primary), False
    result = next((p["result"] for p in scale["points"] if p["min"] <= primary <= p["max"]), None)
    failed = False
    for condition in scale["conditions"]:
        score = (block.get("block_scores") or {}).get(condition["block"])
        if score is not None and score < condition["min_score"]:
            result, failed = condition["result_if_not_met"], True
    return result, failed


RESULT_FIELD = {"primary_to_test": "test_score", "primary_to_grade": "grade",
                "primary_to_level": "result_level"}  # fmt: skip


def q_cons_04(conn: Connection) -> list[Issue]:
    """Блок экзамена соответствует шкале, действующей на момент updated_at сессии."""
    scales = fetch(
        conn,
        "SELECT exam_type, subject_code, scale_type, points, conditions, scale_version, "
        "valid_from FROM core.scales WHERE NOT deleted",
    )
    by_subject: dict[tuple, list[dict]] = defaultdict(list)
    for exam_type, subject, scale_type, points, conditions, version, valid_from in scales:
        by_subject[(exam_type, subject)].append(
            {"scale_type": scale_type, "points": points, "conditions": conditions,
             "version": version, "valid_from": valid_from}
        )  # fmt: skip
    sessions = fetch(
        conn,
        f"SELECT exam_type, subject_code, participant_id, primary_score, exam_block, updated_at "
        f"FROM mart.sessions_current WHERE {DONE} AND primary_score IS NOT NULL",
    )
    rows = []
    for exam_type, subject, participant, primary, block, updated_at in sessions:
        active = [s for s in by_subject[(exam_type, subject)] if s["valid_from"] <= updated_at]
        if not active:
            continue
        scale = max(active, key=lambda s: s["version"])
        expected, failed = scale_result(scale, primary, block or {})
        actual = (block or {}).get(RESULT_FIELD[scale["scale_type"]])
        if actual != expected:
            rows.append((exam_type, f"{participant}: {actual} ≠ {expected}"))
        elif "block_failed" in (block or {}) and block["block_failed"] != failed:
            rows.append((exam_type, f"{participant}: block_failed ≠ {failed}"))
    return grouped("Q-CONS-04", "error", "sessions", rows, "итог не соответствует шкале")


def q_cons_05(conn: Connection) -> list[Issue]:
    """finished_at ≥ started_at; ответ — в пределах [started_at, finished_at + 5 мин]."""
    rows = fetch(
        conn,
        """
        SELECT exam_type, participant_id FROM mart.sessions_current
        WHERE finished_at < started_at
        UNION ALL
        SELECT a.exam_type, a.participant_id || '/' || a.task_number
        FROM mart.answers_flat a
        JOIN mart.sessions_current s USING (exam_type, participant_id)
        WHERE s.started_at IS NOT NULL
          AND (a.answered_at < s.started_at
               OR (s.finished_at IS NOT NULL
                   AND a.answered_at > s.finished_at + interval '5 minutes'))
        """,
    )
    return grouped("Q-CONS-05", "warning", "sessions", rows, "время вне хода работы")


def q_cons_06(conn: Connection) -> list[Issue]:
    """Состояния 6.12: absent без ответов и баллов; pending без балла; checked с баллом."""
    rows = fetch(
        conn,
        f"""
        SELECT s.exam_type, s.participant_id || ': absent с ответами или оценками'
        FROM mart.sessions_current s
        WHERE s.status = 'absent' AND (
            EXISTS (SELECT 1 FROM core.answers a WHERE NOT a.deleted
                    AND a.exam_type = s.exam_type AND a.participant_id = s.participant_id)
            OR EXISTS (SELECT 1 FROM core.expert_reviews r WHERE NOT r.deleted
                       AND r.exam_type = s.exam_type AND r.participant_id = s.participant_id))
        UNION ALL
        SELECT exam_type, participant_id || ': балл при ' || status || '/' || result_status
        FROM mart.sessions_current
        WHERE primary_score IS NOT NULL
          AND (status IN ('absent', 'annulled', 'started', 'in_progress')
               OR result_status = 'pending')
        UNION ALL
        SELECT exam_type, participant_id || '/' || task_number || ': ' || check_status
        FROM ({CURRENT_REVIEWS}) r
        WHERE (check_status = 'checked' AND score IS NULL)
           OR (check_status = 'pending' AND score IS NOT NULL)
        """,
    )
    return grouped("Q-CONS-06", "error", "sessions", rows, "состояние записи противоречиво")


def q_cons_07(conn: Connection) -> list[Issue]:
    """Коды регионов, школ и предметов есть в справочниках."""
    rows = fetch(
        conn,
        """
        SELECT DISTINCT exam_type, 'регион ' || region_code FROM mart.sessions_current s
        WHERE NOT EXISTS (SELECT 1 FROM core.regions r WHERE r.region_code = s.region_code)
        UNION ALL
        SELECT DISTINCT exam_type, 'школа ' || school_code FROM mart.sessions_current s
        WHERE NOT EXISTS (SELECT 1 FROM core.schools x WHERE x.school_code = s.school_code)
        UNION ALL
        SELECT DISTINCT exam_type, 'предмет ' || subject_code FROM mart.sessions_current s
        WHERE NOT EXISTS (SELECT 1 FROM core.subjects x
                          WHERE x.exam_type = s.exam_type AND x.subject_code = s.subject_code)
        UNION ALL
        SELECT NULL, 'школа ' || school_code || ' → регион ' || region_code FROM core.schools x
        WHERE NOT EXISTS (SELECT 1 FROM core.regions r WHERE r.region_code = x.region_code)
        """,
    )
    return grouped("Q-CONS-07", "error", "dictionaries", rows, "кода нет в справочнике")


def q_cons_08(conn: Connection) -> list[Issue]:
    """Справочники regions и schools совпадают между платформами (последние снимки)."""
    issues = []
    for endpoint, key in (("dictionaries/regions", "region_code"),
                          ("dictionaries/schools", "school_code")):  # fmt: skip
        rows = fetch(
            conn,
            """
            SELECT DISTINCT ON (platform) platform, payload FROM ops.dictionary_snapshots
            WHERE endpoint = %s ORDER BY platform, received_at DESC, id DESC
            """,
            (endpoint,),
        )
        contents = {
            platform: {(item[key], item.get("name"), item.get("region_code")) for item in payload}
            for platform, payload in rows
        }
        if len(contents) < 2:
            continue
        reference_platform = sorted(contents)[0]
        for platform, items in sorted(contents.items()):
            difference = items ^ contents[reference_platform]
            if difference:
                codes = sorted({str(item[0]) for item in difference})
                details = {"message": f"справочник отличается от {reference_platform}",
                           "count": len(codes), "examples": codes[:EXAMPLES]}  # fmt: skip
                issues.append(Issue("Q-CONS-08", "warning", platform, endpoint, codes[0], details))
    return issues


def q_cons_09(conn: Connection) -> list[Issue]:
    """Набор заданий в ответах и оценках совпадает с КИМ (если КИМ уже получен)."""
    rows = fetch(
        conn,
        """
        WITH kim AS (SELECT DISTINCT exam_type, subject_code, variant_id, task_number
                     FROM core.kim_tasks WHERE NOT deleted),
             used AS (SELECT DISTINCT exam_type, subject_code, variant_id, task_number
                      FROM mart.answers_flat
                      UNION
                      SELECT DISTINCT r.exam_type, s.subject_code, s.variant_id, r.task_number
                      FROM core.expert_reviews r
                      JOIN mart.sessions_current s USING (exam_type, participant_id)
                      WHERE NOT r.deleted)
        SELECT u.exam_type, u.variant_id || '/' || u.task_number || ': нет в КИМ'
        FROM used u
        WHERE u.exam_type IN (SELECT exam_type FROM kim)
          AND NOT EXISTS (
              SELECT 1 FROM kim k
              WHERE (k.exam_type, k.subject_code, k.variant_id, k.task_number)
                  = (u.exam_type, u.subject_code, u.variant_id, u.task_number))
        UNION ALL
        SELECT k.exam_type, k.variant_id || '/' || k.task_number || ': нет ответов'
        FROM kim k
        WHERE EXISTS (SELECT 1 FROM used u WHERE (u.exam_type, u.subject_code, u.variant_id)
                      = (k.exam_type, k.subject_code, k.variant_id))
          AND NOT EXISTS (
              SELECT 1 FROM used u
              WHERE (u.exam_type, u.subject_code, u.variant_id, u.task_number)
                  = (k.exam_type, k.subject_code, k.variant_id, k.task_number))
        """,
    )
    return grouped("Q-CONS-09", "warning", "kim_tasks", rows, "задания не совпадают с КИМ")


def q_cons_10(conn: Connection) -> list[Issue]:
    """Шкалы: точки монотонны и покрывают 0..max без пропусков и пересечений."""
    maximum = dict(
        ((exam_type, subject), value)
        for exam_type, subject, value in fetch(
            conn, "SELECT exam_type, subject_code, max_primary_score FROM core.subjects"
        )
    )
    rows = []
    for exam_type, subject, scale_type, points, version in fetch(
        conn,
        "SELECT exam_type, subject_code, scale_type, points, scale_version "
        "FROM core.scales WHERE NOT deleted",
    ):
        top = maximum.get((exam_type, subject))
        key = f"{subject} v{version}"
        if scale_type == "primary_to_test":
            primaries = [p["primary"] for p in points]
            tests = [p["test"] for p in points]
            covered = top is None or primaries == list(range(top + 1))
            if not covered or primaries != sorted(primaries) or tests != sorted(tests):
                rows.append((exam_type, key))
            continue
        ordered = sorted(points, key=lambda p: p["min"])
        results = [LEVEL_ORDER.get(p["result"], p["result"]) for p in ordered]
        contiguous = all(
            b["min"] == a["max"] + 1 for a, b in zip(ordered, ordered[1:], strict=False)
        )
        bounds = ordered and ordered[0]["min"] == 0 and (top is None or ordered[-1]["max"] == top)
        if not (contiguous and bounds and results == sorted(results)):
            rows.append((exam_type, key))
    return grouped(
        "Q-CONS-10", "error", "scales", rows, "шкала с пропуском, пересечением или не монотонна"
    )


# --- Своевременность и точность ----------------------------------------------------------


def q_time_01(conn: Connection) -> list[Issue]:
    """Во время экзамена: задержка от updated_at записи до loaded_at (95-й процентиль)
    за последние 10 минут. Если экзамен не идет (нет in_progress) — проверка не нужна.
    """
    running = fetch(conn, "SELECT exam_type FROM core.live_status WHERE in_progress > 0")
    issues = []
    for (exam_type,) in running:
        for table in ("sessions", "answers", "expert_reviews", "proctoring_events"):
            (delay,) = conn.execute(
                f"""
                SELECT percentile_cont(0.95) WITHIN GROUP (
                           ORDER BY extract(epoch FROM loaded_at - updated_at))
                FROM core.{table}
                WHERE exam_type = %s AND loaded_at > now() - interval '10 minutes'
                  AND updated_at <= loaded_at
                """,
                (exam_type,),
            ).fetchone()
            if delay is None:
                continue
            severity = "error" if delay > 120 else "warning" if delay > 60 else None
            if severity:
                details = {"message": "задержка загрузки (p95), с", "delay_sec": round(delay)}
                issues.append(Issue("Q-TIME-01", severity, exam_type, table, table, details))
    return issues


ACCURACY_TABLES = {
    "sessions": "sessions",
    "answers": "answers",
    "expert-reviews": "expert_reviews",
    "proctoring-events": "proctoring_events",
    "dictionaries/kim": "kim_tasks",
    "dictionaries/scales": "scales",
}


def same_value(field: contract.Field, column: object, original: object) -> bool:
    if field.type == DATETIME and original is not None:
        return column == datetime.fromisoformat(original)
    return column == original


def q_acc_01(conn: Connection) -> list[Issue]:
    """Выборочно 20 записей каждой таблицы: исходная запись (payload) совпадает со столбцами."""
    rows = []
    for endpoint, table in ACCURACY_TABLES.items():
        cursor = conn.execute(
            f"SELECT * FROM core.{table} ORDER BY md5(exam_type || record_id) LIMIT 20"
        )
        names = [column.name for column in cursor.description]
        for values in cursor.fetchall():
            row = dict(zip(names, values, strict=True))
            for item in contract.ENDPOINT_FIELDS[endpoint]:
                if not same_value(item, row[item.name], row["payload"].get(item.name)):
                    rows.append((row["exam_type"], f"{table}/{row['record_id']}.{item.name}"))
    return grouped("Q-ACC-01", "error", "core", rows, "столбец не совпадает с исходной записью")


# --- Консистентность витрин ----------------------------------------------------------------


def q_mart_01(conn: Connection) -> list[Issue]:
    """Σ participants в mart.school_results = число актуальных сданных сессий с итогом."""
    rows = fetch(
        conn,
        f"""
        SELECT s.exam_type, coalesce(m.total, 0) || ' ≠ ' || s.total
        FROM (SELECT exam_type, count(*) AS total FROM mart.sessions_current
              WHERE {DONE} GROUP BY exam_type) s
        LEFT JOIN (SELECT exam_type, sum(participants) AS total FROM mart.school_results
                   GROUP BY exam_type) m USING (exam_type)
        WHERE m.total IS DISTINCT FROM s.total
        """,
    )
    return grouped("Q-MART-01", "error", "mart.school_results", rows, "участники не сходятся")


def q_mart_02(conn: Connection) -> list[Issue]:
    """Средние в school_results и region_results совпадают с пересчетом в pandas по core."""
    cursor = conn.execute(
        "SELECT exam_type, record_id, participant_id, subject_code, region_code, school_code, "
        "status, result_status, primary_score, max_primary_score, updated_at "
        "FROM core.sessions WHERE NOT deleted"
    )
    sessions = pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])
    if sessions.empty:
        return []
    sessions = sessions.sort_values(["updated_at", "record_id"])
    sessions = sessions.drop_duplicates(["exam_type", "participant_id"], keep="last")
    done = sessions[(sessions.status == "finished") & (sessions.result_status != "pending")]
    done = done.assign(primary_score=done.primary_score.astype(float))
    rows = []
    for level, mart in (("school_code", "school_results"), ("region_code", "region_results")):
        key = ["exam_type", "subject_code", level]
        expected = done.groupby(key)["primary_score"].mean()
        actual = pd.Series(
            {
                tuple(r[:3]): float(r[3])
                for r in fetch(
                    conn,
                    f"SELECT exam_type, subject_code, {level}, mean_score FROM mart.{mart} "
                    f"WHERE mean_score IS NOT NULL",
                )
            }  # fmt: skip
        )
        for index, value in expected.items():
            if index not in actual or abs(actual[index] - value) > 0.01:
                rows.append((index[0], f"{mart} {index[2]}: {actual.get(index)} ≠ {value:.2f}"))
    return grouped("Q-MART-02", "error", "mart", rows, "среднее витрины не совпадает с core")


def q_mart_03(conn: Connection) -> list[Issue]:
    """mart.task_stats.n по заданию = число ответов сданных работ в mart.answers_flat."""
    rows = fetch(
        conn,
        """
        SELECT coalesce(t.exam_type, a.exam_type), coalesce(t.variant_id, a.variant_id)
               || '/' || coalesce(t.task_number, a.task_number)
        FROM mart.task_stats t
        FULL JOIN (SELECT exam_type, subject_code, variant_id, task_number, count(*) AS n
                   FROM mart.answers_flat WHERE status = 'finished'
                   GROUP BY 1, 2, 3, 4) a
          USING (exam_type, subject_code, variant_id, task_number)
        WHERE t.n IS DISTINCT FROM a.n
        """,
    )
    return grouped("Q-MART-03", "error", "mart.task_stats", rows, "n не совпадает с answers_flat")


# --- Пропуски и выбросы ------------------------------------------------------------------

OPTIONAL_FIELDS = [
    ("sessions", "mart.sessions_current", "room_code IS NULL", "TRUE"),
    ("answers", "mart.answers_flat", "time_spent_sec IS NULL", "TRUE"),
    ("term_mark", "mart.sessions_current", "exam_block -> 'term_mark' = 'null'::jsonb",
     f"exam_type = 'VPR' AND {DONE}"),
]  # fmt: skip


def q_miss_01(conn: Connection) -> list[Issue]:
    """Неслучайные пропуски: доля null у школы в 3 раза выше средней по платформе."""
    issues = []
    for entity, source, is_null, where in OPTIONAL_FIELDS:
        rows = fetch(
            conn,
            f"""
            WITH by_school AS (
                SELECT exam_type, school_code, count(*) AS n,
                       avg(CASE WHEN {is_null} THEN 1.0 ELSE 0 END) AS share
                FROM {source} WHERE {where} GROUP BY exam_type, school_code),
            platform AS (
                SELECT exam_type, avg(CASE WHEN {is_null} THEN 1.0 ELSE 0 END) AS share
                FROM {source} WHERE {where} GROUP BY exam_type)
            SELECT b.exam_type, b.school_code || ': ' || round(b.share, 3) || ' при '
                   || round(p.share, 3)
            FROM by_school b JOIN platform p USING (exam_type)
            WHERE p.share > 0 AND p.share < 1 AND b.n >= 5 AND b.share > 3 * p.share
            """,
        )
        issues += grouped("Q-MISS-01", "info", entity, rows, "пропусков у школы в 3 раза больше")
    return issues


def q_out_01(conn: Connection) -> list[Issue]:
    """Неправдоподобные значения (записываются, но не удаляются): время на задание
    больше времени всей работы.
    """
    rows = fetch(
        conn,
        """
        SELECT a.exam_type, a.participant_id || '/' || a.task_number || ': ' || a.time_spent_sec
        FROM mart.answers_flat a
        JOIN mart.sessions_current s USING (exam_type, participant_id)
        WHERE a.time_spent_sec > coalesce(
            extract(epoch FROM s.finished_at - s.started_at), 240 * 60)
        """,
    )
    return grouped("Q-OUT-01", "warning", "answers", rows, "время на задание больше работы")


CHECKS = [
    ("Q-COMPL-01", q_compl_01),
    ("Q-COMPL-02", q_compl_02),
    ("Q-UNIQ-01", q_uniq_01),
    ("Q-CONS-01", q_cons_01),
    ("Q-CONS-02", q_cons_02),
    ("Q-CONS-03", q_cons_03),
    ("Q-CONS-04", q_cons_04),
    ("Q-CONS-05", q_cons_05),
    ("Q-CONS-06", q_cons_06),
    ("Q-CONS-07", q_cons_07),
    ("Q-CONS-08", q_cons_08),
    ("Q-CONS-09", q_cons_09),
    ("Q-CONS-10", q_cons_10),
    ("Q-TIME-01", q_time_01),
    ("Q-ACC-01", q_acc_01),
    ("Q-MART-01", q_mart_01),
    ("Q-MART-02", q_mart_02),
    ("Q-MART-03", q_mart_03),
    ("Q-MISS-01", q_miss_01),
    ("Q-OUT-01", q_out_01),
]


def run_checks(conn: Connection) -> list[Issue]:
    """Все проверки по порядку. Перед проверками витрин они обновляются."""
    issues = []
    for code, check in CHECKS:
        if code == "Q-MART-01":
            conn.execute("SELECT mart.refresh_all()")
        issues += check(conn)
    return issues
