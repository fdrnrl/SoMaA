"""Правила представления без Streamlit и без повторного расчета аналитики."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from dashboard.data import MSK

EXAM_NAMES = {"EGE": "ЕГЭ", "OGE": "ОГЭ", "VPR": "ВПР", "NIKO": "НИКО"}
RULE_NAMES = {"A1": "А1 · Аномальный результат школы",
              "A2": "А2 · Совпадение ответов", "A3": "А3 · События прокторинга"}


def distribution_stats(rows):
    """Статистика участников по частотам; равномерный набор не имеет выраженной моды."""
    frequencies = defaultdict(int)
    for row in rows:
        count = row["participants"]
        if count < 0:
            raise ValueError("Частота балла не может быть отрицательной")
        if count:
            frequencies[row["primary_score"]] += count
    ranked = sorted(frequencies.items())
    total = sum(frequencies.values())
    if not total:
        return {"participants": 0, "mean": None, "median": None, "modes": []}
    middle = []
    for rank in ((total + 1) // 2, (total + 2) // 2):
        cumulative = 0
        for score, count in ranked:
            cumulative += count
            if cumulative >= rank:
                middle.append(score)
                break
    largest = max(frequencies.values())
    modes = [score for score, count in ranked if count == largest]
    if len(modes) == len(ranked) and len(ranked) > 1:
        modes = []
    return {
        "participants": total,
        "mean": sum(score * count for score, count in ranked) / total,
        "median": sum(middle) / 2,
        "modes": modes,
    }


def score_text(value):
    if value is None:
        return "—"
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def comparison_rows(rows):
    """Объединение предметов: средний процент, взвешенный числом участников."""
    groups = defaultdict(list)
    for row in rows:
        groups[(row["exam_type"], row["region_code"])].append(row)
    result = []
    for (exam, region), values in sorted(groups.items()):
        valid = [r for r in values if r["mean_pct"] is not None and r["participants"] > 0]
        count = sum(r["participants"] for r in valid)
        result.append({
            "exam_type": exam, "region_code": region,
            "region_name": values[0]["region_name"], "participants": count,
            "mean_pct": sum(r["mean_pct"] * r["participants"] for r in valid) / count
            if count else None,
        })
    return result


def variant_gaps(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["share_score"] is not None and row["participants"] > 0:
            key = (row["exam_type"], row["subject_code"], row["task_number"])
            groups[key].append(row)
    result = []
    for key, values in sorted(groups.items()):
        shares = [Decimal(str(r["share_score"])) for r in values]
        gap = max(shares) - min(shares)
        enough = len({r["variant_id"] for r in values}) >= 2
        result.append({
            "exam_type": key[0], "subject_code": key[1], "task_number": key[2],
            "gap_pp": float(gap * 100) if enough else None,
            "flagged": enough and gap > Decimal("0.15"),
        })
    return result


def time_text(value):
    if value is None:
        return "Нет сведений"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(MSK).strftime("%d.%m.%Y %H:%M:%S МСК")


def is_stale(value, now):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return (now - value).total_seconds() > 120


def anomaly_feed(state, rows, source):
    """Первое чтение — базовая линия; новым является только ранее не встречавшийся ID."""
    key = "_sma_seen_anomalies_" + source
    current = {r["anomaly_id"] for r in rows}
    previous = state.get(key)
    new = current - previous if previous is not None else set()
    state[key] = current | (previous or set())
    ordered = sorted(rows, key=lambda r: (r["detected_at"], r["anomaly_id"]), reverse=True)
    ordered.sort(key=lambda r: r["anomaly_id"] not in new)
    return [(row, row["anomaly_id"] in new) for row in ordered]
