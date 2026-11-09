"""Запись в хранилище (раздел 8.5): upsert в core, журнал загрузок, отклоненные записи,
водяные знаки, сводки /live-status и снимки справочников.

Все запросы параметризованы. Имена таблиц и столбцов берутся только из констант
этого модуля и контракта, а не из данных платформ.
"""

from datetime import datetime

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from sma import contract
from sma.contract import ARRAY, DATETIME, OBJECT
from sma.loader.validate import Problem, remove_forbidden_values
from sma.normalize import normalize_answer
from sma.timeutil import parse_datetime

# Адрес → таблица core.
TABLES = {
    "sessions": "sessions",
    "answers": "answers",
    "expert-reviews": "expert_reviews",
    "proctoring-events": "proctoring_events",
    "dictionaries/kim": "kim_tasks",
    "dictionaries/scales": "scales",
    "dictionaries/regions": "regions",
    "dictionaries/schools": "schools",
    "dictionaries/subjects": "subjects",
}

# Справочники, общие для всех платформ: ключ — код, а не (exam_type, record_id).
MERGED_DICTIONARY_KEYS = {
    "dictionaries/regions": ["region_code"],
    "dictionaries/schools": ["school_code"],
    "dictionaries/subjects": ["exam_type", "subject_code"],
}
RECORD_KEY = ["exam_type", "record_id"]


# --- Строки core -----------------------------------------------------------------------------


def table_key(endpoint: str) -> list[str]:
    """Столбцы первичного ключа таблицы адреса."""
    return MERGED_DICTIONARY_KEYS.get(endpoint, RECORD_KEY)


def column_value(field: contract.Field, value: object) -> object:
    """Значение поля контракта → значение для столбца PostgreSQL."""
    if value is None:
        return None
    if field.type == DATETIME:
        return parse_datetime(value)
    if field.type == OBJECT or (field.type == ARRAY and field.item_type == OBJECT):
        return Jsonb(value)
    return value  # строки, числа, bool, массивы строк (text[]) psycopg передает как есть


def make_row(endpoint: str, record: dict, load_id: int) -> dict:
    """Строка таблицы core из проверенной записи (cleaned из validate_record)."""
    row = {
        field.name: column_value(field, record[field.name])
        for field in contract.ENDPOINT_FIELDS[endpoint]
    }
    row["updated_at"] = parse_datetime(record["updated_at"])
    row["load_id"] = load_id
    if endpoint in MERGED_DICTIONARY_KEYS:
        return row
    row["record_id"] = record["record_id"]
    row["deleted"] = record["deleted"]
    row["payload"] = Jsonb(record)
    if endpoint == "sessions":
        block_key = contract.EXAM_BLOCK_KEYS[record["exam_type"]]
        row["exam_block"] = Jsonb(record[block_key])
    if endpoint == "answers":
        row["answer_norm"] = normalize_answer(record["answer"])
    return row


def upsert_query(table: str, columns: list[str], key: list[str]) -> sql.Composed:
    """INSERT ... ON CONFLICT DO UPDATE, который не перетирает новую версию старой.

    RETURNING возвращает строку только если запись вставлена или обновлена:
    так считаются пропущенные устаревшие версии (skipped_stale).
    """
    target = sql.SQL("core.{}").format(sql.Identifier(table))
    updates = [
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
        for c in columns
        if c not in key
    ]
    updates.append(sql.SQL("loaded_at = now()"))
    return sql.SQL(
        "INSERT INTO {target} ({columns}) VALUES ({values}) "
        "ON CONFLICT ({key}) DO UPDATE SET {updates} "
        "WHERE EXCLUDED.updated_at >= {target}.updated_at "
        "RETURNING 1"
    ).format(
        target=target,
        columns=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        values=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        key=sql.SQL(", ").join(sql.Identifier(c) for c in key),
        updates=sql.SQL(", ").join(updates),
    )


def newest_per_key(rows: list[dict], key: list[str]) -> list[dict]:
    """Одна строка на ключ, отсортированные по ключу.

    Если в пакете одна запись встречается несколько раз — остается версия с наибольшим
    updated_at (один INSERT ... ON CONFLICT не может изменить строку дважды).

    Сортировка по ключу нужна против взаимных блокировок (deadlock): потоки разных платформ
    одновременно пишут одни и те же строки общих справочников, и если каждый блокирует их
    в своем порядке, они ждут друг друга вечно. В одном порядке второй поток просто ждет первого.
    """
    newest: dict[tuple, dict] = {}
    for row in rows:
        row_key = tuple(row[column] for column in key)
        if row_key not in newest or row["updated_at"] >= newest[row_key]["updated_at"]:
            newest[row_key] = row
    return [newest[row_key] for row_key in sorted(newest)]


def upsert_rows(conn: Connection, endpoint: str, rows: list[dict]) -> tuple[int, int]:
    """Пишет строки в core. Возвращает (сохранено, пропущено как устаревшие).

    Транзакцию не завершает: commit делает вызывающий код (одна страница — одна транзакция).
    """
    if not rows:
        return 0, 0
    key = table_key(endpoint)
    unique = newest_per_key(rows, key)
    columns = list(unique[0])
    query = upsert_query(TABLES[endpoint], columns, key)
    saved = 0
    with conn.cursor() as cur:
        cur.executemany(query, [[row[c] for c in columns] for row in unique], returning=True)
        while True:
            saved += len(cur.fetchall())
            if not cur.nextset():
                break
    return saved, len(rows) - saved


# --- Журнал загрузок ---------------------------------------------------------------------------

START_LOAD = """
INSERT INTO ops.load_log (platform, endpoint, since_value)
VALUES (%s, %s, %s)
RETURNING id
"""

FINISH_LOAD = """
UPDATE ops.load_log SET
    finished_at = now(), status = %s, http_status = %s, pages = %s, received = %s,
    saved = %s, rejected = %s, skipped_stale = %s, max_updated = %s,
    error_reason = %s, message = %s
WHERE id = %s
"""


def start_load(conn: Connection, platform: str, endpoint: str, since: datetime | None) -> int:
    """Создает строку журнала со статусом running. Возвращает load_id."""
    return conn.execute(START_LOAD, (platform, endpoint, since)).fetchone()[0]


def finish_load(
    conn: Connection,
    load_id: int,
    status: str,
    *,
    http_status: int | None = None,
    pages: int = 0,
    received: int = 0,
    saved: int = 0,
    rejected: int = 0,
    skipped_stale: int = 0,
    max_updated: datetime | None = None,
    error_reason: str | None = None,
    message: str | None = None,
) -> None:
    """Записывает итог запроса: ok, partial (были отклонения) или error."""
    conn.execute(
        FINISH_LOAD,
        (
            status,
            http_status,
            pages,
            received,
            saved,
            rejected,
            skipped_stale,
            max_updated,
            error_reason,
            message,
            load_id,
        ),
    )


# --- Отклоненные записи ------------------------------------------------------------------------

SAVE_REJECTED = """
INSERT INTO ops.rejected_records
    (load_id, platform, endpoint, record_id, reason_code, field, message, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def save_rejected(
    conn: Connection,
    load_id: int,
    platform: str,
    endpoint: str,
    record: object,
    problems: list[Problem],
) -> None:
    """Одна строка на отклоненную запись: причина — первая проблема, в message — все.

    Значения запрещенных полей заменяются на "<removed>" (NFR-12).
    """
    allow_name = endpoint in contract.NAME_ALLOWED_ENDPOINTS
    payload = remove_forbidden_values(record, allow_name)
    record_id = record.get("record_id") if isinstance(record, dict) else None
    if not isinstance(record_id, str):
        record_id = None
    first = problems[0]
    message = "; ".join(problem.message for problem in problems)
    conn.execute(
        SAVE_REJECTED,
        (
            load_id,
            platform,
            endpoint,
            record_id,
            first.reason_code,
            first.field,
            message,
            Jsonb(payload),
        ),
    )


# --- Водяные знаки -----------------------------------------------------------------------------

GET_WATERMARK = "SELECT watermark FROM ops.watermarks WHERE platform = %s AND endpoint = %s"

SET_WATERMARK = """
INSERT INTO ops.watermarks (platform, endpoint, watermark)
VALUES (%s, %s, %s)
ON CONFLICT (platform, endpoint) DO UPDATE SET
    watermark = GREATEST(ops.watermarks.watermark, EXCLUDED.watermark),
    updated_at = now()
"""


def get_watermark(conn: Connection, platform: str, endpoint: str) -> datetime | None:
    """До какого updated_at адрес уже загружен (None — еще ни разу)."""
    row = conn.execute(GET_WATERMARK, (platform, endpoint)).fetchone()
    return row[0] if row else None


def set_watermark(conn: Connection, platform: str, endpoint: str, value: datetime) -> None:
    """Сдвигает водяной знак вперед; назад он не двигается никогда."""
    conn.execute(SET_WATERMARK, (platform, endpoint, value))


# --- /live-status и справочники ------------------------------------------------------------

SAVE_LIVE_STATUS = """
INSERT INTO core.live_status
    (exam_type, in_progress, finished, annulled, absent, generated_at, load_id)
VALUES (%(exam_type)s, %(in_progress)s, %(finished)s, %(annulled)s, %(absent)s,
        %(generated_at)s, %(load_id)s)
ON CONFLICT (exam_type) DO UPDATE SET
    in_progress = EXCLUDED.in_progress, finished = EXCLUDED.finished,
    annulled = EXCLUDED.annulled, absent = EXCLUDED.absent,
    generated_at = EXCLUDED.generated_at, load_id = EXCLUDED.load_id, loaded_at = now()
WHERE EXCLUDED.generated_at >= core.live_status.generated_at
"""

SAVE_LIVE_STATUS_HISTORY = """
INSERT INTO ops.live_status_history (exam_type, generated_at, payload)
VALUES (%s, %s, %s)
"""

SAVE_DICTIONARY_SNAPSHOT = """
INSERT INTO ops.dictionary_snapshots (platform, endpoint, payload)
VALUES (%s, %s, %s)
"""


def save_live_status(conn: Connection, status: dict, load_id: int) -> None:
    """Последняя сводка — в core.live_status, каждая полученная — в ops.live_status_history."""
    generated_at = parse_datetime(status["generated_at"])
    conn.execute(SAVE_LIVE_STATUS, {**status, "generated_at": generated_at, "load_id": load_id})
    conn.execute(SAVE_LIVE_STATUS_HISTORY, (status["exam_type"], generated_at, Jsonb(status)))


def save_dictionary_snapshot(
    conn: Connection, platform: str, endpoint: str, items: list[dict]
) -> None:
    """Снимок справочника платформы — для сверки между платформами (Q-CONS-08)."""
    conn.execute(SAVE_DICTIONARY_SNAPSHOT, (platform, endpoint, Jsonb(items)))
