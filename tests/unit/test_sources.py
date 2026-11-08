"""FileSource: файлы генератора как API — since, сортировка, страницы, ошибки (раздел 8.2)."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sma.generator.writer import write_endpoint, write_json
from sma.loader.sources import FetchError, FileSource

START = datetime(2026, 12, 5, 7, 0, tzinfo=UTC)
EKATERINBURG = timezone(timedelta(hours=5))


def make_items(count: int) -> list[dict]:
    """count записей с updated_at через секунду; у каждой второй — смещение +05:00."""
    items = []
    for k in range(count):
        moment = START + timedelta(seconds=k)
        if k % 2:
            moment = moment.astimezone(EKATERINBURG)
        items.append({"record_id": f"r{k:05d}", "updated_at": moment.isoformat()})
    return items


def test_file_source_pages(tmp_path):
    """FileSource собирает page-1..N и сам режет на страницы по 1000."""
    items = make_items(2500)
    write_endpoint(tmp_path, "answers", items)
    source = FileSource(tmp_path)
    first = source.fetch("answers", None, 1)
    assert first.total_pages == 3
    pages = [first] + [source.fetch("answers", None, page) for page in (2, 3)]
    assert [len(page.items) for page in pages] == [1000, 1000, 500]
    received = [item["record_id"] for page in pages for item in page.items]
    assert received == [item["record_id"] for item in items]


def test_file_source_since_filter(tmp_path):
    """Возвращаются только записи с updated_at строго больше since."""
    write_endpoint(tmp_path, "answers", make_items(2500))
    source = FileSource(tmp_path)
    since = START + timedelta(seconds=1999)
    result = source.fetch("answers", since, 1)
    assert result.total_pages == 1
    assert [item["record_id"] for item in result.items][:2] == ["r02000", "r02001"]
    assert len(result.items) == 500


def test_file_source_sorted_by_moment_not_string(tmp_path):
    """10:00+03:00 раньше 08:00+00:00, хотя как строка «больше»."""
    items = [
        {"record_id": "b", "updated_at": "2026-12-05T08:00:00+00:00"},
        {"record_id": "a", "updated_at": "2026-12-05T10:00:00+03:00"},
    ]
    write_json(tmp_path / "answers.json", {"page": 1, "total_pages": 1, "items": items})
    result = FileSource(tmp_path).fetch("answers", None, 1)
    assert [item["record_id"] for item in result.items] == ["a", "b"]


def test_file_source_empty_list(tmp_path):
    write_endpoint(tmp_path, "proctoring-events", [])
    result = FileSource(tmp_path).fetch("proctoring-events", None, 1)
    assert result.items == [] and result.total_pages == 1


def test_file_source_page_after_last_is_empty(tmp_path):
    write_endpoint(tmp_path, "answers", make_items(10))
    assert FileSource(tmp_path).fetch("answers", None, 5).items == []


def test_file_source_live_status(tmp_path):
    status = {"exam_type": "OGE", "in_progress": 1}
    write_json(tmp_path / "live-status.json", status)
    result = FileSource(tmp_path).fetch("live-status", None, 1)
    assert result.items == status


def test_file_source_dictionary_file_name(tmp_path):
    write_endpoint(tmp_path, "dictionaries/kim", make_items(3))
    assert (tmp_path / "dictionaries-kim.json").exists()
    assert len(FileSource(tmp_path).fetch("dictionaries/kim", None, 1).items) == 3


@pytest.mark.parametrize(
    "content, reason",
    [
        (None, "file_not_found"),
        ("{not json", "bad_json"),
        (json.dumps([1, 2, 3]), "no_wrapper"),
        (json.dumps({"page": 1}), "no_wrapper"),
    ],
)
def test_file_source_errors(tmp_path, content, reason):
    if content is not None:
        (tmp_path / "answers.json").write_text(content, encoding="utf-8")
    with pytest.raises(FetchError) as error:
        FileSource(tmp_path).fetch("answers", None, 1)
    assert error.value.reason_code == reason
