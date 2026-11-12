"""Настоящая геометрия и явное ограниченное сопоставление справочника."""

import gzip
import json
from functools import lru_cache
from pathlib import Path

from dashboard.data import DataUnavailable

GEOMETRY = Path(__file__).with_name("assets") / "regions.geojson.gz"
MAP_NOTE = (
    "Справочные контуры 2013 года, GIS-Lab / © OpenStreetMap contributors (ODbL). "
    "Сопоставлены 20 регионов тестового справочника. Обновление границ требует согласования."
)


@lru_cache(maxsize=1)
def load_regions():
    try:
        with gzip.open(GEOMETRY, "rt", encoding="utf-8") as source:
            features = json.load(source)["features"]
        codes = [f["properties"]["region_code"] for f in features
                 if f["properties"]["region_code"] is not None]
        if len(codes) != len(set(codes)) or any(
            f["geometry"]["type"] not in ("Polygon", "MultiPolygon") for f in features
        ):
            raise ValueError("Invalid geometry")
        return features
    except (OSError, ValueError, KeyError, TypeError):
        raise DataUnavailable("Не удалось загрузить контуры регионов.",
                              "geometry_unavailable") from None


def unmapped_codes(rows, features):
    codes = {f["properties"]["region_code"] for f in features}
    return sorted({r["region_code"] for r in rows} - codes)
