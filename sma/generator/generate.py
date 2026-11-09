"""Генератор синтетических данных экзаменов в формате API платформ (раздел 10).

Запуск:
    python -m sma.generator.generate --profile config/generator/medium.yaml \\
        --out data/generated/medium --seed 2026

Выход (раздел 10.2): папки ege, oge, vpr, niko — снимок API на момент snapshot профиля,
timeline/*.jsonl — все версии записей; answer_key.json — ключ заложенных аномалий;
manifest.json — параметры и контрольные суммы. Одинаковые профиль и seed дают побайтно
одинаковые файлы (NFR-23).
"""

import argparse
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from sma.generator import writer
from sma.generator.anomalies import (
    A1_BOOST,
    A1_BOOST_MAX,
    A1_BOOST_STEP,
    Plan,
    PlantingError,
    plan_anomalies,
    self_check,
)
from sma.generator.exams import ExamConfig, kim_rows, load_exams, make_answer_bank
from sma.generator.population import (
    School,
    build_participants,
    build_regions,
    build_schools,
    make_participant_ids,
)
from sma.generator.records import (
    FACT_ENDPOINTS,
    ExamResult,
    dictionary_records,
    iso,
    simulate_exam,
)
from sma.generator.timeline import SNAPSHOTS, live_status, snapshot

logger = logging.getLogger(__name__)

GENERATOR_VERSION = "1.0.0"
TIMELINE_ENDPOINTS = (*FACT_ENDPOINTS, "dictionaries/kim", "dictionaries/scales")
SYNTHETIC_NOTE = "Данные синтетические; выводы ограничены свойствами генератора"

DATASET_README = """# Синтетический набор данных СМА

**Данные синтетические.** Их создал генератор sma-pipeline (профиль {profile}, seed {seed}).
Реальных участников и персональных данных здесь нет; выводы, сделанные на этих данных,
ограничены свойствами генератора.

Шкалы перевода баллов и коды кодификатора в /dictionaries/scales и /dictionaries/kim —
условные. Для реальных данных их берут из документов ФИПИ.

- Папки ege, oge, vpr, niko — ответы API платформ (формат v2) в снимке {snapshot}.
- timeline/*.jsonl — все версии записей с полем visible_from (для режима воспроизведения).
- answer_key.json — заложенные аномалии, ловушки и результат эталонных правил.
- manifest.json — параметры, seed, время экзамена и контрольные суммы файлов.
"""


class GenerationError(RuntimeError):
    """Набор не удалось создать: закладки не проходят самопроверку."""


@dataclass
class ExamOutcome:
    """Итог генерации одного экзамена."""

    seed: int
    attempt: int
    plan: Plan
    kim: list[dict]
    result: ExamResult
    check: dict | None


def load_profile(path: Path) -> dict:
    """Читает профиль генератора и проверяет снимок."""
    profile = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if profile["snapshot"] not in SNAPSHOTS:
        raise ValueError(f"снимок {profile['snapshot']} — нужен один из {SNAPSHOTS}")
    return profile


def prepare_out_dir(out_dir: Path) -> None:
    """Готовит пустую папку. Старый набор (папка с manifest.json) удаляется целиком,
    чужую непустую папку генератор не трогает.
    """
    if out_dir.exists() and any(out_dir.iterdir()):
        if not (out_dir / "manifest.json").exists():
            raise FileExistsError(f"{out_dir} не пуста и не похожа на набор генератора")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)


def final_records(result: ExamResult) -> dict[str, list[dict]]:
    """Последние версии записей фактов — по ним работают эталонные правила."""
    return {endpoint: snapshot(result.versions[endpoint]) for endpoint in FACT_ENDPOINTS}


def generate_exam(
    seed: int,
    index: int,
    exam: ExamConfig,
    used_schools: list[School],
    participant_ids: list[str],
    variant_ids: list[str],
    regions_by_code: dict,
    profile: dict,
) -> ExamOutcome:
    """Один экзамен. Если закладки не проходят самопроверку — следующий seed (seed + попытка),
    до max_attempts; из прошедших берется попытка с наименьшим числом лишних срабатываний.
    """
    wanted = profile.get("self_check", False)
    attempts = profile.get("max_attempts", 60) if wanted else 1
    best: ExamOutcome | None = None
    last_problem = ""
    for attempt in range(attempts):
        exam_seed = seed + 1000 * (index + 1) + attempt
        rng = np.random.default_rng(exam_seed)
        participants = build_participants(
            rng, exam.has_rooms, used_schools, participant_ids, variant_ids, profile
        )
        try:
            boost = min(A1_BOOST + A1_BOOST_STEP * attempt, A1_BOOST_MAX)
            plan = plan_anomalies(
                rng,
                exam,
                participants,
                profile.get("anomalies", {}),
                profile.get("decoys", {}),
                a1_boost=boost,
            )
            bank = make_answer_bank(rng, exam, variant_ids)
            kim = kim_rows(rng, exam, variant_ids)
            result = simulate_exam(
                rng, exam, plan, len(used_schools), regions_by_code, bank, profile["rates"]
            )
        except PlantingError as problem:
            last_problem = str(problem)
            continue
        check = None if plan.is_empty else self_check(final_records(result), exam, plan)
        outcome = ExamOutcome(exam_seed, attempt, plan, kim, result, check)
        if not wanted or check is None:
            return outcome
        if not check["passed"]:
            last_problem = "; ".join(check["problems"])
            continue
        if best is None or check["extras"] < best.check["extras"]:
            best = outcome
        if check["extras"] == 0:
            break
    if best is None:
        raise GenerationError(
            f"{exam.exam_type}: за {attempts} попыток закладки не прошли самопроверку. "
            f"Последняя причина: {last_problem}. Увеличьте max_attempts или число школ."
        )
    return best


def write_platform(
    folder: Path, exam: ExamConfig, outcome: ExamOutcome, dictionaries: dict, moment
) -> None:
    """Снимок API на момент moment, сводка /live-status и timeline одной платформы."""
    folder.mkdir()
    versions = {**outcome.result.versions, **dictionaries}
    for endpoint, items in versions.items():
        writer.write_endpoint(folder, endpoint, snapshot(items, moment))
    sessions = snapshot(versions["sessions"], moment)
    writer.write_json(folder / "live-status.json", live_status(exam.exam_type, sessions, moment))
    for endpoint in TIMELINE_ENDPOINTS:
        writer.write_timeline(folder, endpoint, versions[endpoint])


def generate(profile_path: Path, out_dir: Path, seed: int) -> dict:
    """Создает набор данных в out_dir. Возвращает manifest."""
    profile = load_profile(profile_path)
    exams = load_exams()
    out_dir = Path(out_dir)
    prepare_out_dir(out_dir)

    rng = np.random.default_rng(seed)
    dictionary_regions, used_regions = build_regions(rng, profile)
    dictionary_schools, used_schools = build_schools(rng, dictionary_regions, used_regions, profile)
    regions_by_code = {region.code: region for region in dictionary_regions}
    per_exam = profile["participants_per_exam"]
    all_ids = make_participant_ids(rng, per_exam * len(profile["exams"]))
    variant_ids = [f"v{k}" for k in range(1, profile["variants_per_exam"] + 1)]

    platforms, key_exams = {}, {}
    for index, exam_type in enumerate(profile["exams"]):
        exam = exams[exam_type]
        ids = all_ids[index * per_exam : (index + 1) * per_exam]
        outcome = generate_exam(
            seed, index, exam, used_schools, ids, variant_ids, regions_by_code, profile
        )
        result = outcome.result
        moment = result.moments[profile["snapshot"]]
        dictionaries = dictionary_records(
            exam, dictionary_regions, dictionary_schools, outcome.kim, result.exam_end
        )
        write_platform(out_dir / exam_type.lower(), exam, outcome, dictionaries, moment)

        platforms[exam_type] = {
            "folder": exam_type.lower(),
            "seed": outcome.seed,
            "participants": len(ids),
            "exam_start": iso(result.exam_start),
            "exam_end": iso(result.exam_end),
            "snapshot_at": iso(moment),
            "snapshots": {name: iso(value) for name, value in result.moments.items()},
        }
        check = outcome.check or {}
        key_exams[exam_type] = {
            "seed": outcome.seed,
            "attempt": outcome.attempt,
            "planted": check.get("planted", []),
            "decoys": check.get("decoys", []),
            "reference_check": check.get("reference_check"),
            "extras": check.get("extras", 0),
            "appeals": result.appeals,
        }
        logger.info(
            "%s: seed %d (попытка %d), версий записей %d, лишних срабатываний %d",
            exam_type,
            outcome.seed,
            outcome.attempt + 1,
            sum(len(items) for items in result.versions.values()),
            key_exams[exam_type]["extras"],
        )

    writer.write_json(
        out_dir / "answer_key.json",
        {
            "generator_version": GENERATOR_VERSION,
            "profile": profile["name"],
            "synthetic": True,
            "note": SYNTHETIC_NOTE,
            "exams": key_exams,
        },
    )
    (out_dir / "README.md").write_text(
        DATASET_README.format(profile=profile["name"], seed=seed, snapshot=profile["snapshot"]),
        encoding="utf-8",
    )
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "synthetic": True,
        "note": SYNTHETIC_NOTE,
        "profile": profile["name"],
        "seed": seed,
        "snapshot": profile["snapshot"],
        "exam_date": exams[profile["exams"][0]].exam_date.isoformat(),
        "platforms": platforms,
        "files": writer.file_checksums(out_dir),
    }
    writer.write_json(out_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор синтетических данных СМА")
    parser.add_argument("--profile", required=True, type=Path, help="профиль, например small.yaml")
    parser.add_argument("--out", required=True, type=Path, help="папка для набора")
    parser.add_argument("--seed", type=int, default=2026, help="seed генератора")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        generate(args.profile, args.out, args.seed)
    except GenerationError as problem:
        raise SystemExit(f"Ошибка: {problem}") from problem
    logger.info("Набор записан в %s", args.out)


if __name__ == "__main__":
    main()
