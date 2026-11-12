"""Чтение mart и отдельного синтетического снимка; SQL находится только здесь."""

import copy
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock

EXAMS = ("EGE", "OGE", "VPR", "NIKO")
MSK = timezone(timedelta(hours=3), "Europe/Moscow")
DEFAULT_DEMO = Path(__file__).resolve().parents[1] / "tests/dashboard/fixtures/small.zip"
ANOMALY_COLUMNS = (
    "anomaly_id", "rule", "exam_type", "subject_code", "object_type", "object_id",
    "region_code", "school_code", "value", "threshold", "detected_at",
)
SCHOOL_COLUMNS = (
    "run_id", "calculated_at", "exam_type", "subject_code", "region_code", "school_code",
    "participants", "p1", "p2", "p3", "p4a", "p4b", "z1", "z2", "z3", "z4a", "z4b",
    "index", "in_list",
)
GROUP_COLUMNS = (
    "group_id", "exam_type", "subject_code", "school_code", "room_code", "variant_id",
    "participant_ids", "size", "min_p_value", "run_id",
)
SOURCE_ENV = {
    "anomalies": "SMA_DASHBOARD_ANOMALIES_VIEW",
    "runs": "SMA_DASHBOARD_RUNS_VIEW",
    "schools": "SMA_DASHBOARD_SCHOOLS_VIEW",
    "p1_groups": "SMA_DASHBOARD_P1_GROUPS_VIEW",
}


def database_is_synthetic():
    return os.environ.get("SMA_DASHBOARD_SYNTHETIC", "").lower() == "true"


class DataUnavailable(RuntimeError):
    """Безопасное сообщение для страницы без деталей подключения."""

    def __init__(self, message, code="database_unavailable"):
        super().__init__(message)
        self.code = code


def display_value(value):
    """Сохраняет пропуски и коды; числа и время готовит для отображения."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("Время должно содержать часовой пояс")
        return value.astimezone(MSK)
    if isinstance(value, dict):
        return {key: display_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [display_value(item) for item in value]
    return value


def filter_options(rows):
    regions = {row["region_code"]: row.get("region_name") for row in rows}
    return {
        "exam_types": sorted({row["exam_type"] for row in rows}),
        "subject_codes": sorted({row["subject_code"] for row in rows}),
        "regions": [
            {"region_code": code, "region_name": regions[code]}
            for code in sorted(regions)
        ],
    }


def _conditions(exam_type=None, subject_code=None, region_code=None):
    if exam_type is not None and exam_type not in EXAMS:
        raise ValueError("Неизвестный экзамен")
    for value in (subject_code, region_code):
        if value is not None and (
            not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}", value)
        ):
            raise ValueError("Код должен быть строкой из двух цифр")
    pairs = (("exam_type", exam_type), ("subject_code", subject_code),
             ("region_code", region_code))
    parts = [column + " = %s" for column, value in pairs if value is not None]
    values = tuple(value for _, value in pairs if value is not None)
    return " AND ".join(parts) or "TRUE", values


def _columns(names):
    return ", ".join('"index"' if name == "index" else name for name in names)


def _connect_reader():
    try:
        import psycopg

        from sma.db import connection_settings
    except ImportError:
        raise DataUnavailable(
            "Не установлены зависимости подключения к БД.", "missing_dependencies"
        ) from None
    settings = connection_settings(user="sma_reader", password="")
    password = os.environ.get("SMA_READER_PASSWORD")
    if not password:
        raise DataUnavailable("Не настроен доступ sma_reader.", "missing_credentials")
    settings["password"] = password
    return psycopg.connect(**settings, connect_timeout=3)


class MartData:
    """Запросы в рамках одного соединения и одного снимка БД."""

    def __init__(self, connection, sources=None):
        self.connection = connection
        self.sources = dict(sources) if sources is not None else {
            name: os.environ.get(variable) for name, variable in SOURCE_ENV.items()
        }
        self.metadata = {
            "mode": "database", "synthetic": None,
            "note": "Происхождение данных БД требуется подтвердить у команды.",
            "topic_policy": "Отбор определяется витриной; пункт 09 еще не согласован.",
        }
        if database_is_synthetic():
            self.metadata.update(
                synthetic=True,
                note="Данные в БД синтетические; выводы ограничены свойствами генератора.",
            )

    def _read(self, sql, params=()):
        try:
            cursor = self.connection.execute(sql, params)
            names = [column.name for column in cursor.description]
            return display_value([dict(zip(names, row, strict=True)) for row in cursor.fetchall()])
        except Exception:
            raise DataUnavailable("Не удалось прочитать согласованную витрину.") from None

    def _source(self, name):
        source = self.sources.get(name)
        if not source:
            raise DataUnavailable(
                "Источник аналитики еще не согласован: " + name, "source_not_configured"
            )
        if not re.fullmatch(r"mart\.[a-z_][a-z0-9_]*", source):
            raise DataUnavailable("Недопустимое имя источника аналитики.", "invalid_source")
        return source

    def filters(self, exam_type=None, subject_code=None):
        where, params = _conditions(exam_type, subject_code)
        rows = self._read(
            "SELECT DISTINCT exam_type, subject_code, region_code, region_name "
            "FROM mart.sessions_current WHERE " + where
            + " ORDER BY exam_type, subject_code, region_code", params,
        )
        return filter_options(rows)

    def distribution(self, exam_type, subject_code, region_code=None):
        where, params = _conditions(exam_type, subject_code, region_code)
        return self._read(
            "SELECT primary_score, count(*) AS participants, "
            "max(max_primary_score) AS max_primary_score FROM mart.sessions_current WHERE "
            + where + " AND status = 'finished' "
            "AND result_status IN ('final', 'appeal_changed') "
            "GROUP BY primary_score ORDER BY primary_score", params,
        )

    def region_results(self, exam_types=None, subject_code=None):
        where, params = _conditions(subject_code=subject_code)
        if exam_types is not None:
            if isinstance(exam_types, str) or any(exam not in EXAMS for exam in exam_types):
                raise ValueError("Нужен список известных экзаменов")
            if not exam_types:
                return []
            where += " AND exam_type = ANY(%s)"
            params += (list(exam_types),)
        return self._read(
            "SELECT exam_type, subject_code, region_code, region_name, participants, schools, "
            "mean_score, mean_pct, share_grade2, share_absent, share_annulled "
            "FROM mart.region_results WHERE " + where
            + " ORDER BY exam_type, subject_code, region_code", params,
        )

    def live(self):
        return self._read(
            "SELECT exam_type, in_progress, finished, annulled, absent, generated_at, "
            "last_successful_load_at, anomalies_by_rule FROM mart.live ORDER BY exam_type"
        )

    def freshness(self):
        return self._read(
            "SELECT platform, endpoint, last_ok_at, last_error_at, last_error_reason, lag_sec "
            "FROM mart.data_freshness ORDER BY platform, endpoint"
        )

    def topics(self, exam_type, subject_code):
        where, params = _conditions(exam_type, subject_code)
        return self._read(
            "SELECT exam_type, subject_code, exam_year, section, content_code, level, "
            "tasks, n_answers, mean_share_score FROM mart.kim_stats WHERE " + where
            + " ORDER BY section, content_code, level", params,
        )

    def variants(self, exam_type, subject_code):
        where, params = _conditions(exam_type, subject_code)
        return self._read(
            "SELECT exam_type, subject_code, task_number, variant_id, participants, "
            "share_score, diff_from_others FROM mart.variant_comparison WHERE " + where
            + " ORDER BY task_number, variant_id", params,
        )

    def anomalies(self, exam_type=None, subject_code=None):
        source = self._source("anomalies")
        where, params = _conditions(exam_type, subject_code)
        return self._read(
            "SELECT " + _columns(ANOMALY_COLUMNS) + " FROM " + source + " WHERE " + where
            + " ORDER BY detected_at DESC, anomaly_id DESC", params,
        )

    def analysis_runs(self):
        return self._read(
            "SELECT run_id, started_at, finished_at, params FROM " + self._source("runs")
            + " WHERE finished_at IS NOT NULL ORDER BY finished_at DESC, run_id DESC"
        )

    def completed_run(self, run_id):
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            raise ValueError("Нужен идентификатор выбранного запуска")
        rows = self._read(
            "SELECT run_id, started_at, finished_at, params FROM " + self._source("runs")
            + " WHERE run_id = %s AND finished_at IS NOT NULL", (run_id,),
        )
        if not rows:
            raise DataUnavailable(
                "Выбранный расчет не завершен или отсутствует.", "analysis_missing"
            )
        return rows[0]

    def school_list(self, run_id, exam_type=None, subject_code=None):
        self.completed_run(run_id)
        where, params = _conditions(exam_type, subject_code)
        return self._read(
            "SELECT " + _columns(SCHOOL_COLUMNS) + " FROM " + self._source("schools")
            + " WHERE run_id = %s AND in_list IS TRUE AND " + where
            + ' ORDER BY "index" DESC NULLS LAST, school_code', (run_id, *params),
        )

    def p1_groups(self, run_id, exam_type, subject_code, school_code):
        self.completed_run(run_id)
        where, params = _conditions(exam_type, subject_code)
        return self._read(
            "SELECT " + _columns(GROUP_COLUMNS) + " FROM " + self._source("p1_groups")
            + " WHERE run_id = %s AND " + where
            + " AND school_code = %s ORDER BY size DESC, group_id",
            (run_id, *params, school_code),
        )

    def school_counts(self, run_id):
        self.completed_run(run_id)
        return self._read(
            "SELECT exam_type, count(DISTINCT school_code) AS schools FROM "
            + self._source("schools") + " WHERE run_id = %s AND in_list IS TRUE "
            "GROUP BY exam_type ORDER BY exam_type", (run_id,),
        )


@contextmanager
def open_snapshot(mode="database", fixture_path=None, sources=None, connection_factory=None):
    """Режим выбирается явно; при отказе БД перехода на демо нет."""
    if mode == "demo":
        from dashboard.demo import DemoData

        yield DemoData(fixture_path or DEFAULT_DEMO)
        return
    if mode != "database":
        raise ValueError("Неизвестный режим данных")
    try:
        connection = (connection_factory or _connect_reader)()
    except DataUnavailable:
        raise
    except Exception:
        raise DataUnavailable("База данных недоступна. Проверьте настройки подключения.") from None
    try:
        with connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            yield MartData(connection, sources)
    except (DataUnavailable, ValueError):
        raise
    except Exception:
        raise DataUnavailable("База данных недоступна. Проверьте настройки подключения.") from None


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


class PageCache:
    """Кэш целого результата страницы, чтобы не смешивать разные снимки."""

    def __init__(self, ttl=30, clock=time.monotonic):
        if not 0 < ttl <= 30:
            raise ValueError("Срок кэша должен быть от 0 до 30 секунд")
        self.ttl, self.clock = ttl, clock
        self.entries = {}
        self.lock = Lock()

    def clear(self):
        with self.lock:
            self.entries.clear()

    def load(self, key, loader):
        with self.lock:
            saved = self.entries.get(key)
            if saved and self.clock() < saved[0]:
                return copy.deepcopy(saved[1])
        value = loader()
        with self.lock:
            if len(self.entries) >= 128:
                self.entries.pop(next(iter(self.entries)))
            self.entries[key] = (self.clock() + self.ttl, copy.deepcopy(value))
        return value


PAGE_CACHE = PageCache()


def read_page(key, loader, *, mode="database", fixture_path=None, sources=None,
              connection_factory=None, cache=PAGE_CACHE):
    """key включает имя страницы и все фильтры; loader читает связанные данные вместе."""
    fixture = Path(fixture_path or DEFAULT_DEMO)
    source_identity = sources if sources is not None else {
        name: os.environ.get(variable) for name, variable in SOURCE_ENV.items()
    }
    identity = (
        os.environ.get("SMA_DB_HOST"), os.environ.get("SMA_DB_PORT"),
        os.environ.get("POSTGRES_DB"), _freeze(source_identity), connection_factory,
        database_is_synthetic(),
    )
    if mode == "demo":
        try:
            stat = fixture.stat()
            identity = (str(fixture.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            raise DataUnavailable("Не найден демонстрационный набор.", "demo_unavailable") from None

    def load():
        with open_snapshot(mode, fixture, sources, connection_factory) as reader:
            return {"data": loader(reader), "source": reader.metadata}

    return cache.load((mode, identity, _freeze(key)), load)
