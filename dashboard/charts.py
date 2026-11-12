"""Matplotlib Figure без общего состояния pyplot; единицы подписаны явно."""

import math
from collections import defaultdict
from textwrap import fill

from dashboard.presentation import EXAM_NAMES, distribution_stats, score_text

COLORS = ("#145DA0", "#A23B00", "#287A48", "#713E9A")


def figure(height=4):
    from matplotlib.figure import Figure

    result = Figure(figsize=(11, height), layout="constrained")
    return result, result.subplots()


def distribution(rows):
    result, ax = figure()
    ax.bar([r["primary_score"] for r in rows], [r["participants"] for r in rows],
           width=0.9, color=COLORS[0])
    maximum = max(r["max_primary_score"] for r in rows)
    ax.set(xlabel="Первичный балл", ylabel="Участники", xlim=(-0.6, maximum + 0.6))
    ax.yaxis.get_major_locator().set_params(integer=True)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    stats = distribution_stats(rows)
    mean, median = stats["mean"], stats["median"]
    if mean is not None:
        if math.isclose(mean, median, abs_tol=1e-9):
            ax.axvline(mean, color=COLORS[3], linestyle="--", linewidth=2,
                       label="Среднее = медиана: " + score_text(mean))
        else:
            ax.axvline(mean, color=COLORS[1], linestyle="--", linewidth=2,
                       label="Среднее: " + score_text(mean))
            ax.axvline(median, color=COLORS[2], linestyle=":", linewidth=2,
                       label="Медиана: " + score_text(median))
        ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncols=2, frameon=False)
    return result


def comparison(rows, exams):
    regions = sorted({r["region_code"] for r in rows})
    names = {r["region_code"]: r["region_name"] for r in rows}
    result, ax = figure(max(4, len(regions) * 0.65))
    width = 0.8 / len(exams)
    for index, exam in enumerate(exams):
        lookup = {r["region_code"]: r["mean_pct"] for r in rows if r["exam_type"] == exam}
        ax.barh([i - 0.4 + width / 2 + index * width for i in range(len(regions))],
                [lookup.get(code) if lookup.get(code) is not None else math.nan
                 for code in regions], height=width, label=EXAM_NAMES[exam],
                color=COLORS[index % len(COLORS)])
    ax.set(yticks=range(len(regions)),
           yticklabels=[_label(code + " · " + (names[code] or code), 36) for code in regions],
           xlabel="Среднее выполнение, %", xlim=(0, 100))
    ax.invert_yaxis()
    ax.legend()
    return result


def bars(labels, values, xlabel, *, maximum=100, colors=COLORS[0]):
    result, ax = figure(max(3.5, len(labels) * 0.45))
    ax.barh(range(len(labels)), values, color=colors)
    ax.set(yticks=range(len(labels)), yticklabels=labels, xlabel=xlabel, xlim=(0, maximum))
    ax.invert_yaxis()
    return result


def variants(rows, flagged):
    tasks = sorted({r["task_number"] for r in rows})
    variants_ = sorted({r["variant_id"] for r in rows})
    result, ax = figure()
    width = 0.8 / len(variants_)
    for index, variant in enumerate(variants_):
        lookup = {r["task_number"]: r["share_score"] for r in rows
                  if r["variant_id"] == variant and r["participants"] > 0}
        ax.bar([i - 0.4 + width / 2 + index * width for i in range(len(tasks))],
               [lookup[t] * 100 if lookup.get(t) is not None else math.nan for t in tasks],
               width, label=variant, color=COLORS[index % len(COLORS)])
    labels = [str(t) + (" *" if t in flagged else "") for t in tasks]
    ax.set(xticks=range(len(tasks)), xticklabels=labels, xlabel="Задание (* разница >15 п. п.)",
           ylabel="Средняя доля баллов, %", ylim=(0, 100))
    ax.legend(title="Вариант")
    return result


def regions(rows, features):
    from matplotlib import colormaps
    from matplotlib.colors import Normalize
    from matplotlib.patches import Patch, PathPatch
    from matplotlib.path import Path

    result, ax = figure(5)
    values = {r["region_code"]: r["mean_score"] for r in rows}
    scores = [v for v in values.values() if v is not None]
    norm = Normalize(vmin=0, vmax=max(scores) if scores and max(scores) > 0 else 1)
    cmap = colormaps["viridis"]
    for feature in features:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            polygons = [polygons]
        value = values.get(feature["properties"]["region_code"])
        color = cmap(norm(value)) if value is not None else "#DDDDDD"
        for polygon in polygons:
            points, codes = [], []
            for ring in polygon:
                points.extend((lon + 360 if lon < 0 else lon, lat) for lon, lat in ring)
                codes.extend([Path.MOVETO] + [Path.LINETO] * (len(ring) - 2)
                             + [Path.CLOSEPOLY])
            ax.add_patch(PathPatch(Path(points, codes), facecolor=color,
                                   edgecolor="#555555", linewidth=0.25))
    ax.set(xlim=(18, 191), ylim=(40, 83), xlabel="Долгота, ° в. д.", ylabel="Широта, ° с. ш.")
    ax.set_aspect(1 / math.cos(math.radians(60)))
    if scores:
        from matplotlib.cm import ScalarMappable

        result.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax,
                        label="Средний первичный балл", shrink=0.7)
    ax.legend(handles=[Patch(facecolor="#DDDDDD", edgecolor="#555555", label="Нет данных")])
    return result


def _label(value, width):
    text = " ".join(str(value).split())
    if len(text) > width * 2:
        text = text[:width * 2 - 1] + "…"
    return fill(text, width, max_lines=2, placeholder="…")


def _heatmap(values, xlabels, ylabels, xlabel, ylabel):
    import numpy as np
    from matplotlib import colormaps

    result, ax = figure(max(3.5, len(ylabels) * 0.6 + 1.4))
    if not xlabels or not ylabels:
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return result
    matrix = np.ma.masked_invalid(np.array(values, dtype=float))
    cmap = colormaps["Blues"].with_extremes(bad="#DDDDDD")
    image = ax.imshow(matrix, vmin=0, vmax=100, cmap=cmap, aspect="auto")
    ax.set(xticks=range(len(xlabels)), xticklabels=xlabels,
           yticks=range(len(ylabels)), yticklabels=ylabels, xlabel=xlabel, ylabel=ylabel)
    ax.tick_params(axis="x", labelsize=9)
    if matrix.size <= 500:
        for y in range(len(ylabels)):
            for x in range(len(xlabels)):
                missing = np.ma.is_masked(matrix[y, x])
                value = None if missing else float(matrix[y, x])
                color = "#111111"
                if value is not None:
                    rgb = cmap(value / 100)[:3]
                    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055)**2.4
                              for c in rgb]
                    luminance = sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722),
                                                         strict=True))
                    if 1.05 / (luminance + 0.05) >= (luminance + 0.05) / 0.05:
                        color = "white"
                ax.text(x, y, "—" if missing else score_text(value), ha="center", va="center",
                        color=color, fontsize=8)
    result.colorbar(image, ax=ax, label="Среднее выполнение, %", shrink=0.8)
    return result


def comparison_heatmap(rows, exams):
    regions_ = sorted({row["region_code"] for row in rows})
    names = {row["region_code"]: row["region_name"] for row in rows}
    lookup = {(r["region_code"], r["exam_type"]): r["mean_pct"] for r in rows}
    values = [[lookup.get((region, exam)) for exam in exams] for region in regions_]
    return _heatmap(values, [EXAM_NAMES.get(exam, exam) for exam in exams],
                    [_label(code + " · " + (names[code] or code), 36) for code in regions_],
                    "Экзамен", "Регион")


def variants_heatmap(rows, flagged, *, tasks=None, variants_=None):
    tasks = sorted({r["task_number"] for r in rows}) if tasks is None else tasks
    variants_ = sorted({r["variant_id"] for r in rows}) if variants_ is None else variants_
    lookup = {(r["variant_id"], r["task_number"]): r["share_score"] * 100 for r in rows
              if r["share_score"] is not None and r["participants"] > 0}
    values = [[lookup.get((variant, task)) for task in tasks] for variant in variants_]
    return _heatmap(values, [str(task) + (" *" if task in flagged else "") for task in tasks],
                    [_label(variant, 32) for variant in variants_],
                    "Задание (* разница >15 п. п.)", "Вариант")


def region_ranking(rows, reference_rows=None):
    valid = sorted((r for r in rows if r["mean_pct"] is not None and r["participants"] > 0),
                   key=lambda r: (-r["mean_pct"], r["region_code"]))
    labels = [_label(r["region_code"] + " · " + (r["region_name"] or r["region_code"]), 36)
              + "\nn=" + str(r["participants"]) for r in valid]
    result = bars(labels, [r["mean_pct"] for r in valid], "Среднее выполнение, %")
    reference = valid if reference_rows is None else [
        r for r in reference_rows if r["mean_pct"] is not None and r["participants"] > 0]
    count = sum(r["participants"] for r in reference)
    if count:
        mean = sum(r["mean_pct"] * r["participants"] for r in reference) / count
        result.axes[0].axvline(mean, color=COLORS[1], linestyle="--",
                              label="Среднее по выборке: " + score_text(mean) + "%")
        result.axes[0].legend(loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False)
    result.set_figheight(max(4, len(valid) * 0.8 + 1))
    return result


def platform_statuses(rows, exams=None):
    lookup = {r["exam_type"]: r for r in rows}
    exams = sorted(lookup) if exams is None else list(exams)
    result, ax = figure(max(3.5, len(exams) * 0.55 + 1.5))
    states = (("in_progress", "Пишут", COLORS[0]), ("finished", "Сдали", COLORS[2]),
              ("annulled", "Аннулированы", COLORS[1]), ("absent", "Не явились", "#777777"))
    complete = {exam: lookup.get(exam) is not None
                and all(lookup[exam][key] is not None for key, _, _ in states) for exam in exams}
    totals = [sum(lookup[exam][key] for key, _, _ in states) if complete[exam] else 0
              for exam in exams]
    left = [0] * len(exams)
    for key, label, color in states:
        values = [lookup[exam][key] if complete[exam] else math.nan for exam in exams]
        ax.barh(range(len(exams)), values, left=left, label=label, color=color)
        left = [a + b if math.isfinite(b) else a for a, b in zip(left, values, strict=True)]
    for index, exam in enumerate(exams):
        if not complete[exam]:
            ax.text(0.02, index, "Нет полной сводки", transform=ax.get_yaxis_transform(),
                    va="center", color="#555555")
        else:
            ax.annotate(str(totals[index]), (totals[index], index), xytext=(5, 0),
                        textcoords="offset points", va="center")
    ax.set(yticks=range(len(exams)), yticklabels=[EXAM_NAMES.get(e, e) for e in exams],
           xlabel="Число работ по сводке платформы", xlim=(0, max([1, *totals]) * 1.18))
    ax.invert_yaxis()
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), ncols=2, frameon=False)
    return result


def freshness_delays(rows):
    result, ax = figure(max(3.5, len(rows) * 0.55 + 1.5))
    labels = [_label(str(r["platform"]) + " · " + r["endpoint"], 42) for r in rows]
    values = [r["lag_sec"] if r["lag_sec"] is not None else math.nan for r in rows]
    colors = [COLORS[1] if math.isfinite(value) and value > 120 else COLORS[0]
              for value in values]
    ax.barh(range(len(rows)), values, color=colors)
    for index, value in enumerate(values):
        if not math.isfinite(value):
            ax.text(0.02, index, "Нет времени загрузки", va="center",
                    transform=ax.get_yaxis_transform(), color="#555555")
    valid = [value for value in values if math.isfinite(value)]
    ax.set(yticks=range(len(rows)), yticklabels=labels, xlabel="Задержка, с",
           xlim=(min([0, *valid]) * 1.1, max([120, *valid]) * 1.1))
    ax.axvline(120, color=COLORS[3], linestyle="--", label="Порог: 120 с")
    ax.invert_yaxis()
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.01), frameon=False)
    return result


def cumulative_distribution(rows):
    frequencies = defaultdict(int)
    for row in rows:
        if row["participants"] > 0:
            frequencies[row["primary_score"]] += row["participants"]
    result, ax = figure()
    total = sum(frequencies.values())
    if total:
        maximum = max(r["max_primary_score"] for r in rows)
        scores, values, cumulative = [-0.6], [0], 0
        for score, count in sorted(frequencies.items()):
            cumulative += count
            scores.append(score)
            values.append(cumulative * 100 / total)
        ax.step([*scores, maximum + 0.6], [*values, 100], where="post", color=COLORS[0])
        ax.set_xlim(-0.6, maximum + 0.6)
    else:
        ax.text(0.5, 0.5, "Нет данных", ha="center", va="center", transform=ax.transAxes)
    ax.set(xlabel="Первичный балл", ylabel="Участники с баллом не выше указанного, %",
           ylim=(0, 101))
    ax.set_axisbelow(True)
    ax.grid(alpha=0.25)
    return result
