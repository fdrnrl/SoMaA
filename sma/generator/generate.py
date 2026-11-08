"""Генератор синтетических данных экзаменов в формате API платформ (раздел 10).

Запуск:
    python -m sma.generator.generate --profile config/generator/small.yaml \\
        --out data/generated/small --seed 2026

Одинаковые профиль и seed дают побайтно одинаковые файлы (NFR-23).
"""

import argparse
import logging
import shutil
from pathlib import Path

import numpy as np
import yaml

from sma.generator import writer
from sma.generator.exams import kim_rows, load_exams, make_answer_bank
from sma.generator.population import (
    build_participants,
    build_regions,
    build_schools,
    make_participant_ids,
)
from sma.generator.records import dictionary_records, iso, simulate_exam

logger = logging.getLogger(__name__)

GENERATOR_VERSION = "0.2.0"
SUPPORTED_SNAPSHOTS = ("after_check",)
FACT_ENDPOINTS = ("sessions", "answers", "expert-reviews", "proctoring-events")
SYNTHETIC_NOTE = "Данные синтетические; выводы ограничены свойствами генератора"

DATASET_README = """# Синтетический набор данных СМА

**Данные синтетические.** Их создал генератор sma-pipeline (профиль {profile}, seed {seed}).
Реальных участников и персональных данных здесь нет; выводы, сделанные на этих данных,
ограничены свойствами генератора.

Шкалы перевода баллов и коды кодификатора в /dictionaries/scales и /dictionaries/kim —
условные. Для реальных данных их берут из документов ФИПИ.

Папки ege, oge, vpr, niko — ответы API платформ (формат v2) в снимке {snapshot}.
Параметры, seed и контрольные суммы файлов — в manifest.json.
"""


def load_profile(path: Path) -> dict:
    """Читает профиль генератора и проверяет поддерживаемый снимок."""
    profile = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if profile["snapshot"] not in SUPPORTED_SNAPSHOTS:
        raise ValueError(f"снимок {profile['snapshot']} пока не поддерживается (M6)")
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

    platforms = {}
    for index, exam_type in enumerate(profile["exams"]):
        exam = exams[exam_type]
        exam_rng = np.random.default_rng([seed, index + 1])
        ids = all_ids[index * per_exam : (index + 1) * per_exam]
        participants = build_participants(
            exam_rng, exam.has_rooms, used_schools, ids, variant_ids, profile
        )
        bank = make_answer_bank(exam_rng, exam, variant_ids)
        kim = kim_rows(exam_rng, exam, variant_ids)
        result = simulate_exam(
            exam_rng,
            exam,
            participants,
            len(used_schools),
            regions_by_code,
            bank,
            profile["rates"],
        )

        folder = out_dir / exam_type.lower()
        folder.mkdir()
        endpoints = {endpoint: result.records[endpoint] for endpoint in FACT_ENDPOINTS}
        endpoints.update(
            dictionary_records(exam, dictionary_regions, dictionary_schools, kim, result.exam_end)
        )
        for endpoint, items in endpoints.items():
            writer.write_endpoint(folder, endpoint, items)
        writer.write_json(folder / "live-status.json", result.live_status)

        platforms[exam_type] = {
            "folder": folder.name,
            "participants": len(participants),
            "exam_start": iso(result.exam_start),
            "exam_end": iso(result.exam_end),
            "snapshot_at": iso(result.snapshot_at),
        }
        logger.info(
            "%s: %d участников, %d ответов",
            exam_type,
            len(participants),
            len(result.records["answers"]),
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
    generate(args.profile, args.out, args.seed)
    logger.info("Набор записан в %s", args.out)


if __name__ == "__main__":
    main()
