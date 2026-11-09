"""Эталонные правила А1–А3 и П1–П4 (раздел 11).

Только для самопроверки генератора и тестов — это не боевая аналитика (ее делает
аналитик аномалий). Работает на pandas по записям набора: sessions, answers,
proctoring-events. Результаты считаются по сданным работам с итогом
(status = finished, result_status ≠ pending). Параметры — из ТЗ СМА v0.3.
"""

import math

import numpy as np
import pandas as pd
from scipy.stats import poisson

from sma.normalize import normalize_answer

A1_MIN_PARTICIPANTS = 10  # школа в А1 и П3–П4 — не меньше 10 участников (N_min)
A1_MIN_SCHOOLS = 3  # регион в А1 — не меньше 3 таких школ
A1_Z = 2.0
A2_MIN_GROUP = 5
A3_MIN_EVENTS = 3
COUNTED_EVENTS = ("tab_switch", "copy", "paste", "fullscreen_exit")
P1_K_MIN = 3
P1_ALPHA = 0.01
P4_DELTA = 2  # окно П4б: [порог, порог + 2]
SHRINK_K = 20  # z′ = z × n / (n + 20)
MAD_SCALE = 1.4826


def to_frames(records: dict[str, list[dict]]) -> dict[str, pd.DataFrame]:
    """Записи адресов → таблицы pandas (только неудаленные записи)."""
    frames = {}
    for endpoint in ("sessions", "answers", "proctoring-events"):
        frame = pd.DataFrame(records.get(endpoint, []))
        if not frame.empty:
            frame = frame[~frame["deleted"]]
        frames[endpoint] = frame
    return frames


def final_sessions(sessions: pd.DataFrame) -> pd.DataFrame:
    """Сданные работы с окончательным итогом."""
    if sessions.empty:
        return sessions
    done = sessions[(sessions["status"] == "finished") & (sessions["result_status"] != "pending")]
    return done.assign(primary_score=done["primary_score"].astype(float))


def final_answers(answers: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """Ответы сданных работ с данными сессии и нормализованным ответом."""
    done = final_sessions(sessions)
    columns = ["exam_type", "participant_id", "subject_code", "region_code", "school_code",
               "room_code", "variant_id"]  # fmt: skip
    if answers.empty or done.empty:
        return pd.DataFrame(columns=[*columns, "task_number", "answer", "is_correct", "norm"])
    merged = answers.merge(done[columns], on=["exam_type", "participant_id"])
    return merged.assign(norm=merged["answer"].map(normalize_answer))


# --- А1: школа с аномально высоким средним ------------------------------------------------


def max_z_include_self(n_schools: int) -> float:
    """Наибольший возможный z школы, если σ считается вместе с ней: (n − 1) / √n.

    При 5 школах — 1,79: правило А1 с порогом 2 не сработает никогда (раздел 11).
    """
    return (n_schools - 1) / math.sqrt(n_schools)


def rule_a1(sessions: pd.DataFrame, sigma_mode: str = "include_self") -> pd.DataFrame:
    """z каждой школы (≥ 10 участников) относительно школ своего региона (≥ 3 таких школ).

    z = (среднее школы − среднее средних по региону) / σ, σ — выборочное (ddof = 1)
    стандартное отклонение средних школ региона. sigma_mode = leave_one_out — среднее
    и σ без самой школы. anomaly = z > 2.
    """
    done = final_sessions(sessions)
    columns = ["exam_type", "subject_code", "region_code", "school_code", "participants",
               "school_mean", "region_mean", "sigma", "z", "anomaly"]  # fmt: skip
    if done.empty:
        return pd.DataFrame(columns=columns)
    key = ["exam_type", "subject_code", "region_code", "school_code"]
    schools = (
        done.groupby(key)["primary_score"]
        .agg(participants="size", school_mean="mean")
        .reset_index()
    )
    schools = schools[schools["participants"] >= A1_MIN_PARTICIPANTS]
    rows = []
    for _, region in schools.groupby(["exam_type", "subject_code", "region_code"]):
        if len(region) < A1_MIN_SCHOOLS:
            continue
        means = region["school_mean"].to_numpy()
        for position, school in enumerate(region.itertuples(index=False)):
            reference = means if sigma_mode == "include_self" else np.delete(means, position)
            sigma = float(np.std(reference, ddof=1))
            region_mean = float(reference.mean())
            z = (school.school_mean - region_mean) / sigma if sigma > 0 else 0.0
            rows.append([*school, region_mean, sigma, z, z > A1_Z])
    return pd.DataFrame(rows, columns=columns)


# --- А2: одинаковые ответы ------------------------------------------------------------------


def rule_a2(answers: pd.DataFrame, sessions: pd.DataFrame) -> list[dict]:
    """Группы ≥ 5 участников одной школы и варианта с одинаковыми нормализованными ответами
    части 1, у которых непустых ответов не меньше половины заданий.
    """
    data = final_answers(answers, sessions)
    if data.empty:
        return []
    data = data.sort_values("task_number")
    vectors = (
        data.groupby(["exam_type", "school_code", "variant_id", "participant_id"])
        .apply(
            lambda part: tuple(zip(part["task_number"], part["norm"], strict=True)),
            include_groups=False,
        )  # fmt: skip
        .rename("vector")
        .reset_index()
    )
    groups = []
    for (exam_type, school, variant, vector), group in vectors.groupby(
        ["exam_type", "school_code", "variant_id", "vector"]
    ):
        non_empty = sum(1 for _, norm in vector if norm != "")
        if len(group) >= A2_MIN_GROUP and non_empty >= len(vector) / 2:
            groups.append(
                {
                    "exam_type": exam_type,
                    "school_code": school,
                    "variant_id": variant,
                    "participant_ids": sorted(group["participant_id"]),
                }
            )
    return groups


# --- А3: засчитываемые события прокторинга -----------------------------------------------------


def rule_a3(proctoring: pd.DataFrame) -> pd.DataFrame:
    """Участники с ≥ 3 событиями tab_switch, copy, paste, fullscreen_exit."""
    if proctoring.empty:
        return pd.DataFrame(columns=["exam_type", "participant_id", "counted_events"])
    counted = proctoring[proctoring["event_type"].isin(COUNTED_EVENTS)]
    totals = counted.groupby(["exam_type", "participant_id"]).size().rename("counted_events")
    return totals[totals >= A3_MIN_EVENTS].reset_index()


# --- П1: совпадающие неверные ответы ---------------------------------------------------------


def holm(p_values: list[float]) -> list[float]:
    """Скорректированные p-значения по Холму (в исходном порядке)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda k: p_values[k])
    adjusted = [0.0] * m
    running = 0.0
    for rank, k in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[k]))
        adjusted[k] = running
    return adjusted


def connected_groups(pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Связные компоненты графа, заданного ребрами."""
    parent: dict[str, str] = {}

    def root(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for first, second in pairs:
        parent[root(first)] = root(second)
    components: dict[str, list[str]] = {}
    for node in parent:
        components.setdefault(root(node), []).append(node)
    return [sorted(members) for members in components.values()]


def rule_p1(answers: pd.DataFrame, sessions: pd.DataFrame) -> list[dict]:
    """Группы участников с подозрительно частыми одинаковыми неверными ответами.

    Для варианта v: p(v, t, a) — доля участников варианта с неверным непустым ответом a
    на задание t; λ(v) = Σ p². Пара из одной группы сравнения (аудитория, иначе школа)
    с одним вариантом: k — число заданий с одинаковым неверным непустым ответом;
    p-value = P(X ≥ k), X ~ Poisson(λ). Поправка Холма по всем парам школы.
    Подозрительная пара: k ≥ 3 и скорректированное p < 0,01. Группы — связные компоненты.
    """
    data = final_answers(answers, sessions)
    if data.empty:
        return []
    data = data.assign(group_key=data["room_code"].fillna(data["school_code"]))
    wrong = data[~data["is_correct"].astype(bool) & (data["norm"] != "")]

    lambdas = {}
    for (exam_type, variant), part in data.groupby(["exam_type", "variant_id"]):
        n = part["participant_id"].nunique()
        shares = wrong[(wrong["exam_type"] == exam_type) & (wrong["variant_id"] == variant)]
        shares = shares.groupby(["task_number", "norm"])["participant_id"].nunique() / n
        lambdas[(exam_type, variant)] = float((shares**2).sum())
    wrong_sets = {
        key: set(zip(part["task_number"], part["norm"], strict=True))
        for key, part in wrong.groupby(["exam_type", "participant_id"])
    }
    people = data[["exam_type", "participant_id", "school_code", "group_key", "variant_id"]]
    people = people.drop_duplicates()

    groups = []
    for (exam_type, school), school_people in people.groupby(["exam_type", "school_code"]):
        pairs = []
        for (group_key, variant), members in school_people.groupby(["group_key", "variant_id"]):
            ids = sorted(members["participant_id"])
            lam = lambdas[(exam_type, variant)]
            for i, first in enumerate(ids):
                for second in ids[i + 1 :]:
                    shared = wrong_sets.get((exam_type, first), set())
                    k = len(shared & wrong_sets.get((exam_type, second), set()))
                    p_value = float(poisson.sf(k - 1, lam)) if k > 0 else 1.0
                    pairs.append((first, second, k, p_value, group_key, variant))
        if not pairs:
            continue
        adjusted = holm([pair[3] for pair in pairs])
        suspicious = [
            pair for pair, p_adj in zip(pairs, adjusted, strict=True)
            if pair[2] >= P1_K_MIN and p_adj < P1_ALPHA
        ]  # fmt: skip
        for members in connected_groups([(pair[0], pair[1]) for pair in suspicious]):
            inside = [pair for pair in suspicious if pair[0] in members]
            groups.append(
                {
                    "exam_type": exam_type,
                    "school_code": school,
                    "group_key": inside[0][4],
                    "variant_id": inside[0][5],
                    "participant_ids": members,
                    "min_p_value": min(pair[3] for pair in inside),
                }
            )
    return groups


# --- П2: решены трудные задания при нерешенных легких --------------------------------------


def rule_p2(answers: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """G* каждого участника: задания упорядочены по доле верных в варианте (легкие первыми);
    G — число пар (легкое неверно, трудное верно); G* = G / (r × (n − r)), r — решено,
    n — заданий; при r = 0 или r = n G* = 0.
    """
    data = final_answers(answers, sessions)
    rows = []
    for (exam_type, _variant), part in data.groupby(["exam_type", "variant_id"]):
        shares = part.groupby("task_number")["is_correct"].mean().sort_values(ascending=False)
        order = list(shares.index)
        for participant, own in part.groupby("participant_id"):
            solved = own.set_index("task_number")["is_correct"].reindex(order, fill_value=False)
            correct = solved.astype(int).to_numpy()
            n, r = len(correct), int(correct.sum())
            wrong_before = np.cumsum(1 - correct) - (1 - correct)
            g = int((correct * wrong_before).sum())
            g_star = g / (r * (n - r)) if 0 < r < n else 0.0
            rows.append((exam_type, participant, g, g_star))
    return pd.DataFrame(rows, columns=["exam_type", "participant_id", "g", "g_star"])


# --- П3: школа лучше региона именно на трудных заданиях -----------------------------------


def rule_p3(answers: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """П3 = средняя разность (процент выполнения школы − региона) по трудным заданиям
    минус то же по легким. Трудные — доля верных по региону ниже медианы заданий региона.
    """
    data = final_answers(answers, sessions)
    big = school_sizes(sessions)
    rows = []
    for (exam_type, region), part in data.groupby(["exam_type", "region_code"]):
        region_share = part.groupby("task_number")["is_correct"].mean()
        hard = region_share < region_share.median()
        for school, own in part.groupby("school_code"):
            if big.get((exam_type, school), 0) < A1_MIN_PARTICIPANTS:
                continue
            school_share = own.groupby("task_number")["is_correct"].mean()
            difference = (school_share - region_share).dropna()
            p3 = (
                difference[hard.reindex(difference.index)].mean()
                - difference[~hard.reindex(difference.index)].mean()
            )
            rows.append((exam_type, region, school, float(p3)))
    return pd.DataFrame(rows, columns=["exam_type", "region_code", "school_code", "p3"])


# --- П4: одинаковые баллы и баллы у порога -----------------------------------------------------


def school_sizes(sessions: pd.DataFrame) -> dict[tuple[str, str], int]:
    done = final_sessions(sessions)
    return done.groupby(["exam_type", "school_code"]).size().to_dict()


def robust_z(values: pd.Series) -> pd.Series:
    """z = (x − медиана) / (1,4826 × MAD); при MAD = 0 — нули."""
    median = values.median()
    mad = (values - median).abs().median()
    if mad == 0:
        return values * 0.0
    return (values - median) / (MAD_SCALE * mad)


def rule_p4(sessions: pd.DataFrame, thresholds: dict[str, int]) -> pd.DataFrame:
    """П4а — разброс баллов школы (робастная z со сменой знака: меньше разброс — выше z);
    П4б — доля участников с баллом в [порог, порог + 2]. z′ = z × n / (n + 20).
    """
    done = final_sessions(sessions)
    rows = []
    for (exam_type, subject), part in done.groupby(["exam_type", "subject_code"]):
        threshold = thresholds[exam_type]
        in_window = part["primary_score"].between(threshold, threshold + P4_DELTA)
        stats = (
            part.assign(in_window=in_window)
            .groupby("school_code")
            .agg(
                participants=("primary_score", "size"),
                sd=("primary_score", "std"),  # выборочное, ddof = 1
                share_window=("in_window", "mean"),
            )
        )
        stats = stats[stats["participants"] >= A1_MIN_PARTICIPANTS]
        if stats.empty:
            continue
        shrink = stats["participants"] / (stats["participants"] + SHRINK_K)
        stats["z4a"] = -robust_z(stats["sd"]) * shrink
        stats["z4b"] = robust_z(stats["share_window"]) * shrink
        for school, row in stats.iterrows():
            rows.append((exam_type, subject, school, row["sd"], row["share_window"],
                         row["z4a"], row["z4b"]))  # fmt: skip
    columns = ["exam_type", "subject_code", "school_code", "sd", "share_window", "z4a", "z4b"]
    return pd.DataFrame(rows, columns=columns)
