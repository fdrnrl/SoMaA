"""Источники данных загрузчика (раздел 8.2).

У всех источников один метод fetch(endpoint, since, page) → FetchResult.
FileSource читает файлы генератора и ведет себя как API: фильтрует по since,
сортирует по (updated_at, record_id) и режет на страницы сам.
ApiSource запрашивает API платформы по HTTP.
"""

import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import requests

from sma.generator.writer import endpoint_file_stem
from sma.timeutil import try_parse_datetime

PAGE_SIZE = 1000
LIVE_STATUS = "live-status"
API_PREFIX = "/api/v1/analytics/"
EARLIEST = datetime.min.replace(tzinfo=UTC)


class FetchError(Exception):
    """Не удалось получить страницу: сеть, таймаут, код ответа, неверный JSON, нет файла."""

    def __init__(self, reason_code: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message
        self.http_status = http_status


@dataclass
class FetchResult:
    """Одна страница ответа. Для /live-status items — объект, а не список."""

    items: list[dict] | dict
    page: int
    total_pages: int
    http_status: int | None
    elapsed_sec: float


class Source(Protocol):
    def fetch(self, endpoint: str, since: datetime | None, page: int) -> FetchResult: ...


def api_order(record: object) -> tuple[datetime, str]:
    """Порядок записей как в API. Запись с неразборчивым updated_at — в начало
    (ее все равно отклонит проверка).
    """
    if not isinstance(record, dict):
        return EARLIEST, ""
    moment = try_parse_datetime(record.get("updated_at")) or EARLIEST
    return moment, str(record.get("record_id"))


def is_after(record: object, since: datetime) -> bool:
    """updated_at > since. Если время не разобрать — запись пропускается дальше, на проверку."""
    if not isinstance(record, dict):
        return True
    moment = try_parse_datetime(record.get("updated_at"))
    return moment is None or moment > since


def unwrap_page(data: object, endpoint: str, where: str) -> tuple[list | dict, int, int]:
    """Проверяет обертку ответа и возвращает (items, page, total_pages).

    /live-status — объект без обертки; список — {"page", "total_pages", "items"}.
    """
    if endpoint == LIVE_STATUS:
        if not isinstance(data, dict):
            raise FetchError("no_wrapper", f"{where}: сводка /live-status не объект")
        return data, 1, 1
    ok = (
        isinstance(data, dict)
        and isinstance(data.get("items"), list)
        and isinstance(data.get("page"), int)
        and isinstance(data.get("total_pages"), int)
    )
    if not ok:
        raise FetchError("no_wrapper", f"{where}: нет обертки page/total_pages/items")
    return data["items"], data["page"], data["total_pages"]


class FileSource:
    """Файлы набора генератора одной платформы, например data/generated/small/oge."""

    def __init__(self, folder: Path, page_size: int = PAGE_SIZE):
        self.folder = Path(folder)
        self.page_size = page_size
        self._cache_key: tuple[str, datetime | None] | None = None
        self._cache: list = []

    def read_json(self, path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FetchError("bad_json", f"{path}: {error}") from error

    def page_files(self, endpoint: str) -> list[Path]:
        """Файлы адреса: name.json или name.page-1.json, name.page-2.json, ... по номеру."""
        stem = endpoint_file_stem(endpoint)
        pages = list(self.folder.glob(f"{stem}.page-*.json"))
        if pages:
            return sorted(pages, key=lambda path: int(re.search(r"page-(\d+)", path.name)[1]))
        single = self.folder / f"{stem}.json"
        if not single.exists():
            raise FetchError("file_not_found", f"нет файла {single}")
        return [single]

    def read_items(self, endpoint: str) -> list:
        """Все записи адреса из всех его файлов."""
        items = []
        for path in self.page_files(endpoint):
            page_items, _, _ = unwrap_page(self.read_json(path), endpoint, str(path))
            items += page_items
        return items

    def read_live_status(self) -> object:
        path = self.folder / "live-status.json"
        if not path.exists():
            raise FetchError("file_not_found", f"нет файла {path}")
        return self.read_json(path)

    def selected(self, endpoint: str, since: datetime | None) -> list:
        """Записи после since в порядке API. Последний результат запоминается,
        чтобы не перечитывать файлы для каждой страницы.
        """
        key = (endpoint, since)
        if self._cache_key != key:
            items = self.read_items(endpoint)
            if since is not None:
                items = [item for item in items if is_after(item, since)]
            self._cache = sorted(items, key=api_order)
            self._cache_key = key
        return self._cache

    def fetch(self, endpoint: str, since: datetime | None, page: int) -> FetchResult:
        started = time.monotonic()
        if page < 1:
            raise FetchError("bad_request", f"номер страницы {page} < 1")
        if endpoint == LIVE_STATUS:
            return FetchResult(self.read_live_status(), 1, 1, None, time.monotonic() - started)
        items = self.selected(endpoint, since)
        total_pages = max(1, math.ceil(len(items) / self.page_size))
        chunk = items[(page - 1) * self.page_size : page * self.page_size]
        return FetchResult(chunk, page, total_pages, None, time.monotonic() - started)


class ApiSource:
    """API платформы: GET {base_url}/api/v1/analytics/{endpoint} с токеном (раздел 6.1).

    base_url — адрес платформы, например http://mock-api:8000/oge.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_sec: float = 30,
        page_size: int = PAGE_SIZE,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.page_size = page_size
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def url(self, endpoint: str) -> str:
        return f"{self.base_url}{API_PREFIX}{endpoint}"

    def fetch(self, endpoint: str, since: datetime | None, page: int) -> FetchResult:
        params: dict[str, object] = {}
        if endpoint != LIVE_STATUS:
            params = {"page": page, "limit": self.page_size}
            if since is not None:
                params["since"] = since.isoformat()
        url = self.url(endpoint)
        started = time.monotonic()
        try:
            response = self.session.get(url, params=params, timeout=self.timeout_sec)
        except requests.Timeout as error:
            raise FetchError("timeout", f"{url}: нет ответа за {self.timeout_sec} с") from error
        except requests.RequestException as error:
            raise FetchError("network", f"{url}: {error}") from error
        elapsed = time.monotonic() - started

        if response.status_code != 200:
            message = f"{url}: код {response.status_code} {response.text[:200]}"
            raise FetchError(f"http_{response.status_code}", message, response.status_code)
        try:
            data = response.json()
        except ValueError as error:
            raise FetchError("bad_json", f"{url}: ответ не JSON", 200) from error
        try:
            items, got_page, total_pages = unwrap_page(data, endpoint, url)
        except FetchError as error:
            error.http_status = 200
            raise
        return FetchResult(items, got_page, total_pages, 200, elapsed)
