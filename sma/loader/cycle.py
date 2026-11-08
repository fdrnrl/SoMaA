"""Один цикл загрузки одного адреса платформы (раздел 8.3).

1. since = водяной знак − перекрытие (или initial_since, если знака еще нет).
2. Страницы 1..total_pages с одним since.
3. Каждая запись проверяется; отклоненные — в ops.rejected_records.
4. Годные пишутся upsert-ом; одна страница — одна транзакция.
5. Водяной знак сдвигается на max(updated_at), только если ошибок получения не было.
6. Итог — в ops.load_log: ok или partial (были отклонения).
7. При ошибке получения — ops.load_log со статусом error; водяной знак не трогается.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import Connection

from sma.loader import store
from sma.loader.sources import FetchError, Source
from sma.loader.validate import validate_live_status, validate_record
from sma.timeutil import try_parse_datetime

logger = logging.getLogger(__name__)

FULL_DICTIONARIES = ("dictionaries/regions", "dictionaries/schools", "dictionaries/subjects")
FACT_ENDPOINTS = ("sessions", "answers", "expert-reviews", "proctoring-events")
KIM_SCALES = ("dictionaries/kim", "dictionaries/scales")
LIVE_STATUS = "live-status"
# Порядок в цикле: сначала справочники, потом факты, КИМ и шкалы, последней — сводка.
ALL_ENDPOINTS = FULL_DICTIONARIES + FACT_ENDPOINTS + KIM_SCALES + (LIVE_STATUS,)


@dataclass
class LoaderSettings:
    """Настройки загрузчика из config/platforms.yaml."""

    since_overlap_sec: int = 5
    initial_since: datetime = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class EndpointResult:
    """Итог загрузки одного адреса (то же, что попадает в ops.load_log)."""

    platform: str
    endpoint: str
    status: str = "ok"
    pages: int = 0
    received: int = 0
    saved: int = 0
    rejected: int = 0
    skipped_stale: int = 0
    max_updated: datetime | None = None
    http_status: int | None = None
    error_reason: str | None = None
    message: str | None = None


def since_for(watermark: datetime | None, settings: LoaderSettings) -> datetime:
    """since для запроса: водяной знак минус перекрытие, чтобы не потерять записи
    с одинаковым временем; дубли безопасны благодаря upsert.
    """
    if watermark is None:
        return settings.initial_since
    return watermark - timedelta(seconds=settings.since_overlap_sec)


def finish(conn: Connection, load_id: int, result: EndpointResult) -> EndpointResult:
    """Записывает итог в ops.load_log и фиксирует транзакцию."""
    store.finish_load(
        conn,
        load_id,
        result.status,
        http_status=result.http_status,
        pages=result.pages,
        received=result.received,
        saved=result.saved,
        rejected=result.rejected,
        skipped_stale=result.skipped_stale,
        max_updated=result.max_updated,
        error_reason=result.error_reason,
        message=result.message,
    )
    conn.commit()
    return result


def fail(
    conn: Connection, load_id: int, result: EndpointResult, reason: str, message: str
) -> EndpointResult:
    """Ошибка: откатить незавершенную страницу и записать error в журнал."""
    conn.rollback()
    result.status = "error"
    result.error_reason = reason
    result.message = message
    logger.warning("%s %s: ошибка %s — %s", result.platform, result.endpoint, reason, message)
    return finish(conn, load_id, result)


def load_list_endpoint(
    conn: Connection, source: Source, platform: str, endpoint: str, settings: LoaderSettings
) -> EndpointResult:
    """Загружает списочный адрес: все страницы, проверка, upsert, журнал, водяной знак."""
    full = endpoint in FULL_DICTIONARIES
    since = None if full else since_for(store.get_watermark(conn, platform, endpoint), settings)
    load_id = store.start_load(conn, platform, endpoint, since)
    conn.commit()

    result = EndpointResult(platform, endpoint)
    snapshot: list[dict] = []
    dropped: set[str] = set()
    try:
        page, total_pages = 1, 1
        while page <= total_pages:
            fetched = source.fetch(endpoint, since, page)
            total_pages = fetched.total_pages
            result.http_status = fetched.http_status
            result.pages += 1
            result.received += len(fetched.items)

            rows = []
            for record in fetched.items:
                moment = (
                    try_parse_datetime(record.get("updated_at"))
                    if isinstance(record, dict)
                    else None
                )
                if moment and (result.max_updated is None or moment > result.max_updated):
                    result.max_updated = moment
                check = validate_record(record, endpoint, platform)
                dropped.update(check.dropped)
                if not check.ok:
                    store.save_rejected(conn, load_id, platform, endpoint, record, check.problems)
                    result.rejected += 1
                elif full and check.cleaned["deleted"]:
                    result.skipped_stale += 1  # в общих справочниках удаления не применяются
                else:
                    rows.append(store.make_row(endpoint, check.cleaned, load_id))
                    if full:
                        snapshot.append(check.cleaned)
            saved, skipped = store.upsert_rows(conn, endpoint, rows)
            result.saved += saved
            result.skipped_stale += skipped
            conn.commit()
            page += 1

        if full:
            store.save_dictionary_snapshot(conn, platform, endpoint, snapshot)
        elif result.max_updated is not None:
            store.set_watermark(conn, platform, endpoint, result.max_updated)
    except FetchError as error:
        return fail(conn, load_id, result, error.reason_code, error.message)
    except psycopg.Error as error:
        return fail(conn, load_id, result, "db_error", str(error))

    if dropped:
        logger.warning("%s %s: удалены неизвестные поля %s", platform, endpoint, sorted(dropped))
    result.status = "partial" if result.rejected else "ok"
    return finish(conn, load_id, result)


def load_live_status(conn: Connection, source: Source, platform: str) -> EndpointResult:
    """Загружает /live-status: объект без since и страниц."""
    load_id = store.start_load(conn, platform, LIVE_STATUS, None)
    conn.commit()
    result = EndpointResult(platform, LIVE_STATUS)
    try:
        fetched = source.fetch(LIVE_STATUS, None, 1)
        result.http_status = fetched.http_status
        result.pages = 1
        result.received = 1
        check = validate_live_status(fetched.items, platform)
        if check.ok:
            store.save_live_status(conn, check.cleaned, load_id)
            result.saved = 1
        else:
            store.save_rejected(conn, load_id, platform, LIVE_STATUS, fetched.items, check.problems)
            result.rejected = 1
        conn.commit()
    except FetchError as error:
        return fail(conn, load_id, result, error.reason_code, error.message)
    except psycopg.Error as error:
        return fail(conn, load_id, result, "db_error", str(error))
    result.status = "partial" if result.rejected else "ok"
    return finish(conn, load_id, result)


def load_endpoint(
    conn: Connection, source: Source, platform: str, endpoint: str, settings: LoaderSettings
) -> EndpointResult:
    """Загружает один адрес платформы."""
    if endpoint == LIVE_STATUS:
        return load_live_status(conn, source, platform)
    return load_list_endpoint(conn, source, platform, endpoint, settings)


def run_cycle(
    conn: Connection,
    source: Source,
    platform: str,
    settings: LoaderSettings,
    endpoints: tuple[str, ...] = ALL_ENDPOINTS,
) -> list[EndpointResult]:
    """Один цикл по адресам одной платформы. Ошибка одного адреса не останавливает остальные."""
    results = []
    for endpoint in endpoints:
        result = load_endpoint(conn, source, platform, endpoint, settings)
        logger.info(
            "%s %-22s %-7s получено %d, сохранено %d, отклонено %d, устаревших %d",
            platform,
            endpoint,
            result.status,
            result.received,
            result.saved,
            result.rejected,
            result.skipped_stale,
        )
        results.append(result)
    return results
