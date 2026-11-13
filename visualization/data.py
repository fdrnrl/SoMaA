"""Чтение набора платформ (папка medium/small или ZIP из генератора) в таблицы pandas."""

import glob
import json
import os
import tempfile
import zipfile

import pandas as pd

PLATFORMS = {"EGE": "ege", "OGE": "oge", "VPR": "vpr", "NIKO": "niko"}
ENDPOINTS = {
    "sessions": "sessions",
    "answers": "answers",
    "reviews": "expert-reviews",
    "kim": "dictionaries-kim",
    "regions": "dictionaries-regions",
    "schools": "dictionaries-schools",
    "subjects": "dictionaries-subjects",
}


def load_endpoint(folder: str, endpoint: str) -> pd.DataFrame:
    """Все страницы одного адреса: sessions.json или sessions.page-1.json, page-2 ..."""
    files = glob.glob(os.path.join(folder, f"{endpoint}.json"))
    files += glob.glob(os.path.join(folder, f"{endpoint}.page-*.json"))

    def page(name: str) -> int:
        return int(name.rsplit("page-", 1)[1].split(".")[0]) if "page-" in name else 0

    items: list = []
    for name in sorted(files, key=page):
        with open(name, encoding="utf-8") as handle:
            items += json.load(handle)["items"]
    return pd.json_normalize(items)


def load_set(source: str) -> dict[str, pd.DataFrame]:
    """Читает набор целиком: папку (medium, small) или ZIP. Все платформы склеены в одну таблицу."""
    if str(source).endswith(".zip"):
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(source) as archive:
                archive.extractall(folder)
            return load_set(folder)
    parts: dict[str, list] = {key: [] for key in ENDPOINTS}
    for folder_name in PLATFORMS.values():
        folder = os.path.join(source, folder_name)
        if not os.path.isdir(folder):
            continue
        for key, endpoint in ENDPOINTS.items():
            frame = load_endpoint(folder, endpoint)
            if len(frame):
                parts[key].append(frame)
    tables = {key: pd.concat(v, ignore_index=True) if v else pd.DataFrame()
              for key, v in parts.items()}
    tables["regions"] = tables["regions"].drop_duplicates("region_code")
    tables["schools"] = tables["schools"].drop_duplicates("school_code")
    tables["sessions"] = unify_grade(tables["sessions"])
    return tables


def unify_grade(sessions: pd.DataFrame) -> pd.DataFrame:
    """Отметка приходит как oge.grade / vpr.grade; расчету нужна одна колонка grade (FR-17)."""
    result = sessions.copy()
    result["grade"] = pd.NA
    for column in ("oge.grade", "vpr.grade"):
        if column in result.columns:
            result["grade"] = result["grade"].where(result["grade"].notna(), result[column])
    result["grade"] = pd.to_numeric(result["grade"], errors="coerce")
    return result
