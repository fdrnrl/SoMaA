"""Запись файлов набора в формате API (раздел 10.2).

Каждый файл — одна страница ответа API: {"page", "total_pages", "items"}.
Если записей больше PAGE_SIZE — файлы name.page-1.json, name.page-2.json, ...
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

PAGE_SIZE = 1000


def endpoint_file_stem(endpoint: str) -> str:
    """Имя файла адреса без расширения: "dictionaries/kim" → "dictionaries-kim"."""
    return endpoint.replace("/", "-")


def write_json(path: Path, data: object) -> None:
    """JSON с отступом 2, кириллица как есть, в конце перевод строки."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def api_order(record: dict) -> tuple[datetime, str]:
    """Порядок записей как в API: по (updated_at, record_id) по возрастанию."""
    return datetime.fromisoformat(record["updated_at"]), record["record_id"]


def write_endpoint(
    folder: Path, endpoint: str, items: list[dict], page_size: int = PAGE_SIZE
) -> list[Path]:
    """Пишет записи адреса страницами. Возвращает пути созданных файлов."""
    stem = endpoint_file_stem(endpoint)
    ordered = sorted(items, key=api_order)
    if len(ordered) <= page_size:
        path = folder / f"{stem}.json"
        write_json(path, {"page": 1, "total_pages": 1, "items": ordered})
        return [path]
    total_pages = (len(ordered) + page_size - 1) // page_size
    paths = []
    for page in range(1, total_pages + 1):
        chunk = ordered[(page - 1) * page_size : page * page_size]
        path = folder / f"{stem}.page-{page}.json"
        write_json(path, {"page": page, "total_pages": total_pages, "items": chunk})
        paths.append(path)
    return paths


def file_checksums(root: Path) -> dict[str, str]:
    """SHA-256 всех файлов набора (кроме manifest.json): относительный путь → хеш."""
    checksums = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            relative = path.relative_to(root).as_posix()
            checksums[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return checksums
