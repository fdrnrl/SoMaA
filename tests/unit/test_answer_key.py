"""Закладки, ловушки и ключ answer_key.json (разделы 10.7–10.9)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from sma.generator.anomalies import fit_score
from sma.generator.generate import generate
from sma.loader.sources import FileSource
from sma.reference import rules

SMALL = Path(__file__).resolve().parents[2] / "config" / "generator" / "small.yaml"
EXAMS = ["EGE", "OGE", "VPR", "NIKO"]


@pytest.fixture(scope="module")
def small_dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    generate(SMALL, out, 2026)
    return out


def load_key(dataset: Path) -> dict:
    return json.loads((dataset / "answer_key.json").read_text(encoding="utf-8"))


def load_frames(dataset: Path, exam_type: str) -> dict[str, pd.DataFrame]:
    """Таблицы для эталонных правил — из файлов снимка, как их видит загрузчик."""
    source = FileSource(dataset / exam_type.lower())
    records = {e: source.read_items(e) for e in ("sessions", "answers", "proctoring-events")}
    return rules.to_frames(records)


def planted(key: dict, exam_type: str, rule: str) -> list[dict]:
    return [item for item in key["exams"][exam_type]["planted"] if item["rule"] == rule]


def test_key_structure(small_dataset):
    key = load_key(small_dataset)
    assert key["synthetic"] is True and "синтетические" in key["note"]
    assert set(key["exams"]) == set(EXAMS)
    for exam_type, info in key["exams"].items():
        assert {"seed", "planted", "decoys", "reference_check", "extras"} <= set(info)
        rules_planted = {item["rule"] for item in info["planted"]}
        expected = {"A2", "P1", "P2"} | ({"A3"} if exam_type != "VPR" else set())
        assert rules_planted == expected, exam_type
        assert info["extras"] == 0


def test_planted_participants_finished_and_schools_distinct(small_dataset):
    key = load_key(small_dataset)
    for exam_type in EXAMS:
        sessions = FileSource(small_dataset / exam_type.lower()).read_items("sessions")
        status = {s["participant_id"]: s["status"] for s in sessions}
        school = {s["participant_id"]: s["school_code"] for s in sessions}
        groups = []
        for item in key["exams"][exam_type]["planted"] + key["exams"][exam_type]["decoys"]:
            ids = item.get("participant_ids") or [item["participant_id"]]
            assert all(status[pid] == "finished" for pid in ids), item
            groups.append({school[pid] for pid in ids})
        assert all(len(group) == 1 for group in groups), "группа — из одной школы"
        schools = [next(iter(group)) for group in groups]
        assert len(schools) == len(set(schools)), "закладки — в разных школах"


@given(target=st.integers(0, 31), seed=st.integers(0, 10_000))
def test_fit_score_hits_target(target, seed):
    """П4: верность части 1 и баллы части 2 дают ровно заданный первичный балл."""
    rng = np.random.default_rng(seed)
    difficulty = np.linspace(-1.8, 1.8, 19)
    max2 = np.array([2, 2, 2, 2, 2, 2])
    correct, scores = fit_score(rng, target, difficulty, max2)
    assert int(correct.sum()) + int(scores.sum()) == target
    assert np.all(scores <= max2) and np.all(scores >= 0)


@pytest.mark.parametrize("exam_type", EXAMS)
def test_generator_key_matches_reference(medium_dataset, exam_type):
    """FR-06: эталонные правила на файлах набора medium находят все закладки из ключа,
    ловушки не срабатывают, лишних А2 и А3 нет.
    """
    key = load_key(medium_dataset)
    info = key["exams"][exam_type]
    frames = load_frames(medium_dataset, exam_type)
    sessions, answers = frames["sessions"], frames["answers"]

    a1 = rules.rule_a1(sessions)
    a1_found = set(a1.loc[a1["anomaly"], "school_code"])
    for item in planted(key, exam_type, "A1"):
        assert item["school_code"] in a1_found
        row = a1[a1["school_code"] == item["school_code"]].iloc[0]
        assert row["z"] == pytest.approx(item["expected"]["z"], abs=0.01)
        assert row["z"] > 2

    a2_found = [group["participant_ids"] for group in rules.rule_a2(answers, sessions)]
    assert sorted(a2_found) == sorted(
        item["participant_ids"] for item in planted(key, exam_type, "A2")
    )

    a3_found = set(rules.rule_a3(frames["proctoring-events"])["participant_id"])
    assert a3_found == {item["participant_id"] for item in planted(key, exam_type, "A3")}

    p1_found = [set(group["participant_ids"]) for group in rules.rule_p1(answers, sessions)]
    for item in planted(key, exam_type, "P1"):
        assert any(set(item["participant_ids"]) <= found for found in p1_found)

    for decoy in info["decoys"]:
        if decoy["kind"] == "A2_blank":
            assert decoy["participant_ids"] not in a2_found
        else:
            assert decoy["participant_id"] not in a3_found

    p2 = rules.rule_p2(answers, sessions).set_index("participant_id")
    for item in planted(key, exam_type, "P2"):
        assert p2.loc[item["participant_id"], "g_star"] > 0.8
    reference = info["reference_check"]
    assert sorted(a1_found) == reference["A1"]
    assert sorted(a3_found) == reference["A3"]


def test_medium_has_every_anomaly(medium_dataset):
    key = load_key(medium_dataset)
    for exam_type, info in key["exams"].items():
        found = {item["rule"] for item in info["planted"]}
        expected = {"A1", "A2", "P1", "P2", "P3", "P4a", "P4b"}
        if exam_type != "VPR":
            expected.add("A3")
        assert found == expected, exam_type
        assert {decoy["kind"] for decoy in info["decoys"]} == (
            {"A2_blank", "A3_focus"} if exam_type != "VPR" else {"A2_blank"}
        )
