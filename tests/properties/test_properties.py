"""Свойства (property-based, Hypothesis) из раздела 16.2.

Hypothesis сам придумывает сотни входных данных и ищет такие, на которых свойство
нарушается; найденный пример он упрощает до самого маленького.
"""

import json
import tempfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sma import contract
from sma.contract import ARRAY, BOOL, DATETIME, INT, OBJECT, STRING, TWO_DIGITS
from sma.generator.exams import ege_test_score, oge_grade, vpr_grade
from sma.generator.generate import generate
from sma.loader import store
from sma.loader.validate import validate_record
from sma.normalize import normalize_answer

# Любые символы, кроме NUL и одиночных суррогатов (Cs): такие строки проверка отклоняет —
# их нельзя сохранить в базе.
TEXT = st.characters(exclude_characters="\x00", exclude_categories=["Cs"])
ZONES = [timezone(timedelta(hours=hours)) for hours in (0, 3, 5, 7, 10)]
DB_SETTINGS = settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


# --- Стратегии по контракту --------------------------------------------------------------


def value_of(field: contract.Field) -> st.SearchStrategy:
    """Любое допустимое значение поля по его описанию в sma/contract.py."""
    if field.enum is not None:
        base = st.sampled_from(field.enum)
    elif field.pattern == TWO_DIGITS:
        base = st.from_regex(r"[0-9]{2}", fullmatch=True)
    elif field.type == STRING:
        base = st.text(TEXT, min_size=field.min_length or 0, max_size=field.max_length or 20)
    elif field.type == INT:
        low = field.min_value if field.min_value is not None else -1000
        high = field.max_value if field.max_value is not None else 1000
        base = st.integers(low, high)
    elif field.type == BOOL:
        base = st.booleans()
    elif field.type == DATETIME:
        moments = st.datetimes(
            min_value=datetime(2020, 1, 1), max_value=datetime(2030, 1, 1),  # noqa: DTZ001
            timezones=st.sampled_from(ZONES),
        )  # fmt: skip
        base = moments.map(lambda moment: moment.isoformat())
    elif field.type == OBJECT:
        base = st.dictionaries(st.sampled_from(["algebra", "geometry"]), st.integers(0, 20))
    elif field.type == ARRAY and field.item_type == OBJECT:
        base = st.lists(st.fixed_dictionaries({"min": st.integers(0, 9), "max": st.integers(0, 9)}))
    elif field.type == ARRAY:
        base = st.lists(st.text(TEXT, max_size=5), max_size=3)
    else:
        raise ValueError(field)
    return st.one_of(st.none(), base) if field.nullable else base


def records(endpoint: str, exam_type: str | None = None) -> st.SearchStrategy:
    """Любая корректная запись адреса (для sessions — с блоком своего экзамена)."""
    exam = st.just(exam_type) if exam_type else st.sampled_from(contract.EXAM_TYPES)

    def build(exam_value: str) -> st.SearchStrategy:
        parts = {"record_id": st.text("abcdef0123456789-", min_size=1, max_size=64)}
        for field in contract.ENDPOINT_FIELDS[endpoint]:
            parts[field.name] = (
                st.just(exam_value) if field.name == "exam_type" else value_of(field)
            )
        if endpoint == "sessions":
            key = contract.EXAM_BLOCK_KEYS[exam_value]
            block = {f.name: value_of(f) for f in contract.EXAM_BLOCK_FIELDS[key]}
            parts[key] = st.fixed_dictionaries(block)
        for field in contract.SERVICE_TAIL:
            parts[field.name] = value_of(field)
        strategy = st.fixed_dictionaries(parts)
        if endpoint in ("answers", "expert-reviews"):
            strategy = strategy.filter(lambda r: r["score"] is None or r["score"] <= r["max_score"])
        return strategy

    return exam.flatmap(build)


def platform_of(record: dict) -> str:
    return record.get("exam_type") or "OGE"


# --- Свойства ---------------------------------------------------------------------------------


@given(st.text())
def test_normalize_idempotent(text):
    """normalize_answer идемпотентна: n(n(x)) == n(x) для любых строк."""
    once = normalize_answer(text)
    assert normalize_answer(once) == once


@given(
    st.sampled_from(list(contract.ENDPOINT_FIELDS)).flatmap(
        lambda e: st.tuples(st.just(e), records(e))
    )
)
def test_valid_record_passes_validate(case):
    """Любая корректная запись, построенная по contract.py, проходит проверку."""
    endpoint, record = case
    check = validate_record(record, endpoint, platform_of(record))
    assert check.ok, check.problems


@given(st.data())
def test_removing_any_field_gives_missing_field(data):
    """Удаление любого обязательного поля дает missing_field (record_id — bad_record_id)."""
    endpoint = data.draw(st.sampled_from(list(contract.ENDPOINT_FIELDS)))
    record = data.draw(records(endpoint))
    name = data.draw(st.sampled_from(contract.field_names(endpoint)))
    broken = {key: value for key, value in record.items() if key != name}
    check = validate_record(broken, endpoint, platform_of(record))
    expected = "bad_record_id" if name == "record_id" else "missing_field"
    assert (expected, name) in [(p.reason_code, p.field) for p in check.problems]


@given(st.sampled_from(list(contract.ENDPOINT_FIELDS)).flatmap(lambda e: records(e)))
def test_json_round_trip(record):
    """Запись → JSON → запись: значения равны (сериализация обратима)."""
    assert json.loads(json.dumps(record, ensure_ascii=False)) == record


@given(st.integers(0, 31), st.integers(0, 31))
def test_oge_grade_bounded_and_monotonic(first, second):
    low, high = sorted((first, second))
    assert 2 <= oge_grade(low) <= oge_grade(high) <= 5


@given(st.integers(0, 20), st.integers(0, 20))
def test_vpr_grade_bounded_and_monotonic(first, second):
    low, high = sorted((first, second))
    assert 2 <= vpr_grade(low) <= vpr_grade(high) <= 5


@given(st.integers(0, 32), st.integers(0, 32))
def test_ege_test_score_bounded_and_monotonic(first, second):
    low, high = sorted((first, second))
    assert 0 <= ege_test_score(low) <= ege_test_score(high) <= 100


SMALL = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"


@settings(max_examples=5, deadline=None)
@given(st.integers(0, 10_000))
def test_generated_primary_score_in_range(seed):
    """Первичный балл сгенерированного участника всегда 0..max_primary_score."""
    profile = yaml.safe_load(SMALL.read_text(encoding="utf-8"))
    profile.update(anomalies={}, decoys={}, self_check=False)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "profile.yaml"
        path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
        generate(path, Path(folder) / "data", seed)
        for exam in ("ege", "oge", "vpr", "niko"):
            sessions = json.loads((Path(folder) / "data" / exam / "sessions.json").read_text())
            for s in sessions["items"]:
                assert (
                    s["primary_score"] is None or 0 <= s["primary_score"] <= s["max_primary_score"]
                )


# --- Свойства записи в базу --------------------------------------------------------------------


def answer_batches() -> st.SearchStrategy:
    """Пакет ответов ОГЭ с разными record_id."""
    return st.lists(
        records("answers", "OGE"), min_size=1, max_size=15, unique_by=lambda r: r["record_id"]
    )


def table_state(conn) -> list[tuple]:
    return conn.execute(
        "SELECT record_id, score, answer, updated_at, deleted, payload "
        "FROM core.answers ORDER BY record_id"
    ).fetchall()


def apply(conn, batch: list[dict]) -> None:
    load_id = store.start_load(conn, "OGE", "answers", None)
    store.upsert_rows(conn, "answers", [store.make_row("answers", r, load_id) for r in batch])


@DB_SETTINGS
@given(batch=answer_batches())
def test_upsert_twice_equals_once(loader_db, batch):
    """Один и тот же пакет дважды дает то же состояние базы, что и один раз."""
    loader_db.execute("DELETE FROM core.answers")
    apply(loader_db, batch)
    once = table_state(loader_db)
    apply(loader_db, batch)
    assert table_state(loader_db) == once
    loader_db.rollback()


@DB_SETTINGS
@given(record=records("answers", "OGE"), shifts=st.lists(st.integers(0, 10_000), min_size=2,
       max_size=6, unique=True), data=st.data())  # fmt: skip
def test_versions_in_any_order_keep_newest(loader_db, record, shifts, data):
    """Версии одной записи в любом порядке → в базе версия с наибольшим updated_at."""
    loader_db.execute("DELETE FROM core.answers")
    start = datetime(2026, 12, 5, 7, 0, tzinfo=UTC)
    versions = [
        {**record, "updated_at": (start + timedelta(seconds=s)).isoformat(), "answer": str(s)}
        for s in shifts
    ]
    for version in data.draw(st.permutations(versions)):
        apply(loader_db, [version])
    newest = max(versions, key=lambda v: datetime.fromisoformat(v["updated_at"]))
    row = loader_db.execute("SELECT answer, count(*) OVER () FROM core.answers").fetchone()
    assert row == (newest["answer"], 1)
    loader_db.rollback()
