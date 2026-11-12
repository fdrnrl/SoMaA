"""Контрольный расчет показателей графиков (чистый pandas, без обращения к базе и дашборду).

Правила из ТЗ v0.4, которые держит весь модуль:
  * удаленные записи (deleted) не учитываются; при нескольких записях участника берется
    с наибольшим updated_at (время сравнивается в UTC);
  * в итоги входят только сессии finished с result_status final / appeal_changed;
    pending, absent, annulled не участвуют;
  * null и 0 — разное: строки без значения пропускаются, а не считаются нулем;
  * отметка — поле grade платформы, не пересчитывается;
  * округление только при показе, внутри расчетов полная точность.
"""

import numpy as np
import pandas as pd

FINAL_RESULT = ("final", "appeal_changed")


def _latest_by_record(frame: pd.DataFrame) -> pd.DataFrame:
    """Из версий одной записи (record_id) оставляет последнюю по updated_at."""
    if frame.empty or "record_id" not in frame.columns:
        return frame
    ordered = frame.assign(_u=pd.to_datetime(frame["updated_at"], utc=True))
    ordered = ordered.sort_values("_u", kind="stable")
    return ordered.drop_duplicates(["exam_type", "record_id"], keep="last").drop(columns="_u")


def _latest_by_key(frame: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Без удаленных; при нескольких записях с одним естественным ключом — последняя."""
    if frame.empty:
        return frame
    current = _latest_by_record(frame)
    if "deleted" in current.columns:
        current = current[~current["deleted"].fillna(False).astype(bool)]
    ordered = current.assign(_u=pd.to_datetime(current["updated_at"], utc=True))
    ordered = ordered.sort_values("_u", kind="stable")
    return ordered.drop_duplicates(key, keep="last").drop(columns="_u")


def actual_sessions(sessions: pd.DataFrame) -> pd.DataFrame:
    """Актуальные сессии: без удаленных и без дублей по (exam_type, participant_id)."""
    return _latest_by_key(sessions, ["exam_type", "participant_id"])


def final_sessions(sessions: pd.DataFrame) -> pd.DataFrame:
    """Сессии с окончательным результатом — основа итоговых графиков."""
    current = actual_sessions(sessions)
    done = (current["status"] == "finished") & current["result_status"].isin(FINAL_RESULT)
    final = current[done & current["primary_score"].notna()].copy()
    final["pct"] = final["primary_score"] / final["max_primary_score"] * 100
    return final


def region_results(sessions: pd.DataFrame, regions: pd.DataFrame | None = None) -> pd.DataFrame:
    """По каждой тройке (экзамен, предмет, регион): участники, школы, средние, доли в процентах.

    share_grade2, share_absent, share_annulled — в процентах (0–100). Доля отметок 2 считается
    среди участников, у которых отметка пришла с платформы.
    """
    key = ["exam_type", "subject_code", "region_code"]
    final = final_sessions(sessions)
    final = final.assign(_g=final["grade"].notna().astype(int),
                         _g2=(final["grade"] == 2).astype(int))
    result = final.groupby(key, dropna=False).agg(
        n=("participant_id", "size"),
        schools=("school_code", "nunique"),
        mean_score=("primary_score", "mean"),
        pct_exec=("pct", "mean"),
        n_graded=("_g", "sum"),
        n_grade2=("_g2", "sum"),
    ).reset_index()
    graded = result["n_graded"].where(result["n_graded"] > 0)
    result["share_grade2"] = 100 * result["n_grade2"] / graded

    current = actual_sessions(sessions)
    current = current.assign(_abs=(current["status"] == "absent").astype(int),
                             _ann=(current["status"] == "annulled").astype(int))
    states = current.groupby(key, dropna=False).agg(
        total=("participant_id", "size"), absent=("_abs", "sum"), annulled=("_ann", "sum"),
    ).reset_index()
    states["share_absent"] = 100 * states["absent"] / states["total"]
    states["share_annulled"] = 100 * states["annulled"] / states["total"]
    result = result.merge(states[key + ["share_absent", "share_annulled"]], on=key, how="left")

    if regions is not None and len(regions):
        names = regions[["region_code", "name"]].rename(columns={"name": "region_name"})
        result = result.merge(names, on="region_code", how="left")
    else:
        result["region_name"] = result["region_code"]
    result["region_name"] = result["region_name"].fillna(result["region_code"])
    return result.sort_values(key).reset_index(drop=True)


def school_results(sessions: pd.DataFrame) -> pd.DataFrame:
    """Итоги школ: участники, средний и медианный балл, средний процент выполнения."""
    final = final_sessions(sessions)
    keys = ["exam_type", "subject_code", "region_code", "school_code"]
    return final.groupby(keys).agg(
        n=("participant_id", "size"), mean_score=("primary_score", "mean"),
        median_score=("primary_score", "median"), pct_exec=("pct", "mean"),
    ).reset_index()


def score_histogram(sessions: pd.DataFrame, exam_type: str, subject_code: str,
                    region_code: str | None = None, max_score: int | None = None) -> pd.DataFrame:
    """Гистограмма первичных баллов: строка на каждый балл 0..максимум (пустые баллы = 0)."""
    final = final_sessions(sessions)
    final = final[(final["exam_type"] == exam_type) & (final["subject_code"] == subject_code)]
    if region_code is not None:
        final = final[final["region_code"] == region_code]
    top = int(max_score if max_score is not None
              else (final["max_primary_score"].max() if len(final) else 0))
    scores = final["primary_score"].to_numpy(dtype=float)
    if len(scores) and not np.all(scores == np.floor(scores)):
        raise ValueError("первичные баллы должны быть целыми")
    counts = pd.Series(scores.astype(int)).value_counts()
    counts = counts.reindex(range(top + 1), fill_value=0).sort_index()
    return pd.DataFrame({"score": counts.index, "count": counts.to_numpy()})


def part_scores(sessions: pd.DataFrame, answers: pd.DataFrame,
                reviews: pd.DataFrame) -> pd.DataFrame:
    """Доли набранного балла за задания: часть 1 — по ответам, часть 2 — проверенные оценки.

    Берутся участники с окончательным результатом. Колонка share = score / max_score.
    """
    final = final_sessions(sessions)[["exam_type", "participant_id", "subject_code", "variant_id"]]
    columns = ["exam_type", "participant_id", "task_number", "score", "max_score"]
    key = ["exam_type", "participant_id", "task_number"]
    part1 = _latest_by_key(answers, key)[columns]
    part2 = _latest_by_key(reviews, key)
    part2 = part2[part2["check_status"] == "checked"][columns]
    scores = pd.concat([part1, part2], ignore_index=True)
    scores = scores[scores["score"].notna() & scores["max_score"].notna()]
    scores = scores.merge(final, on=["exam_type", "participant_id"], how="inner")
    scores["share"] = scores["score"] / scores["max_score"]
    return scores


def _kim_clean(kim: pd.DataFrame) -> pd.DataFrame:
    key = ["exam_type", "subject_code", "exam_year", "variant_id", "task_number"]
    current = _latest_by_key(kim, key)
    return current[["exam_type", "subject_code", "variant_id", "task_number",
                    "section", "content_codes", "level"]]


def topic_stats(scores: pd.DataFrame, kim: pd.DataFrame, exam_type: str, subject_code: str,
                by: str = "code") -> pd.DataFrame:
    """Средняя доля набранного балла: по (раздел, код элемента, уровень) или по разделам.

    Доля — среднее по ответам от «балл / максимальный балл» (так и в витрине дашборда).
    tasks — число различных пар (вариант, задание). share_pct — в процентах.
    """
    on = ["exam_type", "subject_code", "variant_id", "task_number"]
    chosen = scores[(scores["exam_type"] == exam_type) & (scores["subject_code"] == subject_code)]
    joined = chosen.merge(_kim_clean(kim), on=on, how="inner")
    if by == "code":
        joined = joined.explode("content_codes").rename(columns={"content_codes": "content_code"})
        joined = joined[joined["content_code"].notna()]
        keys = ["section", "content_code", "level"]
    else:
        keys = ["section"]
    joined = joined.assign(_task=joined["variant_id"].astype(str) + "/"
                           + joined["task_number"].astype(str))
    result = joined.groupby(keys).agg(
        tasks=("_task", "nunique"), n_answers=("share", "size"), share=("share", "mean"),
    ).reset_index()
    result["share_pct"] = 100 * result["share"]
    return result.drop(columns="share").sort_values(keys).reset_index(drop=True)


def variant_comparison(scores: pd.DataFrame, exam_type: str, subject_code: str,
                       threshold_pp: float = 15.0, min_n: int = 0) -> pd.DataFrame:
    """Сравнение вариантов: для (задание, вариант) — участники, доля, разность с остальными.

    spread_pp = максимум − минимум доли по вариантам задания; flagged = spread_pp > порога
    (строго больше: ровно 15 п. п. не отмечается). Варианты, где участников меньше min_n,
    в разброс не входят.
    """
    chosen = scores[(scores["exam_type"] == exam_type) & (scores["subject_code"] == subject_code)]
    grouped = chosen.groupby(["task_number", "variant_id"]).agg(
        n=("share", "size"), share=("share", "mean")).reset_index()
    grouped["share_pct"] = 100 * grouped["share"]
    grouped = grouped.drop(columns="share")
    enough = grouped[grouped["n"] >= min_n]

    def versus_others(row: pd.Series) -> float:
        others = enough[(enough["task_number"] == row["task_number"])
                        & (enough["variant_id"] != row["variant_id"])]
        return row["share_pct"] - others["share_pct"].mean() if len(others) else np.nan

    grouped["diff_to_others_pp"] = (grouped.apply(versus_others, axis=1) if len(grouped)
                                    else pd.Series(dtype=float))
    spread = enough.groupby("task_number")["share_pct"].agg(
        lambda s: s.max() - s.min() if len(s) > 1 else np.nan)
    grouped["spread_pp"] = grouped["task_number"].map(spread)
    grouped["flagged"] = grouped["spread_pp"] > threshold_pp
    return grouped.sort_values(["task_number", "variant_id"]).reset_index(drop=True)
