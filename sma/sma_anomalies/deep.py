"""Углубленный анализ: показатели П1–П4, z-оценки, индекс аномальности, перечень школ (FR-30–FR-44).

Порядок расчета (приложение А ТЗ): нормализация → частоты неверных ответов → П1 → П2 → П3 → П4 →
z-оценки → корректировка → индекс → перечень. Только задания части 1 (раздел 10 ТЗ).
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd
from scipy.stats import poisson

from .params import Params
from .prep import final_sessions, group_key_column, with_norm_answers

INDICATORS = ["P1", "P2", "P3", "P4a", "P4b"]


# ------------------------------------------------------------------------------- вспомогательное
def holm_adjust(pvals: np.ndarray) -> np.ndarray:
    """Поправка Холма (Holm, 1979): скорректированные p-значения в исходном порядке."""
    p = np.asarray(pvals, float)
    m = len(p)
    if m == 0:
        return p
    order = np.argsort(p, kind="stable")
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * p[idx])
        adj[idx] = min(1.0, running)
    return adj


def robust_z(x: pd.Series) -> pd.Series:
    """FR-42: z = (x − медиана) / (1,4826 × MAD). Если MAD = 0, z всех школ равна 0 (T-50)."""
    x = x.astype(float)
    med = x.median()
    mad = (x - med).abs().median()
    if not np.isfinite(mad) or mad == 0:
        return pd.Series(0.0, index=x.index)
    return (x - med) / (1.4826 * mad)


# ------------------------------------------------------------------------------- П1
def wrong_answer_frequencies(ans: pd.DataFrame) -> pd.DataFrame:
    """FR-34: для каждого задания каждого варианта — доля участников, давших каждый неверный непустой ответ.

    ans — ответы с variant_id и norm; доля берется от всех участников варианта.
    """
    n_var = ans.groupby("variant_id")["participant_id"].nunique()
    w = ans[(~ans["is_correct"]) & (ans["norm"] != "")]
    f = w.groupby(["variant_id", "task_number", "norm"]).size().rename("cnt").reset_index()
    f["p"] = f["cnt"] / f["variant_id"].map(n_var)
    return f


@dataclass
class P1Result:
    pairs: pd.DataFrame      # все проверенные пары (school_code, variant_id, a, b, k, lam, p_value, p_adj, suspicious)
    groups: pd.DataFrame     # группы П1 (раздел 7.3), без run_id и group_id
    by_school: pd.Series     # П1 школы: доля участников в группах


def compute_p1(sessions_fin: pd.DataFrame, ans: pd.DataFrame, params: Params, eligible_schools: set) -> P1Result:
    """П1 (FR-34–FR-37).

    Пары и группы ищутся во ВСЕХ школах — как в эталонной реализации набора (в small школы меньше N_min,
    а группы заложены). Ограничение N_min (FR-33) действует на школьное значение П1, z-оценку и индекс:
    для школ вне eligible_schools значение П1 не возвращается.
    """
    s = sessions_fin.copy()
    s["cmp_group"] = group_key_column(s)
    freq = wrong_answer_frequencies(ans)
    lam = freq.assign(p2=freq["p"] ** 2).groupby("variant_id")["p2"].sum()   # λ = Σ p² по варианту
    wrong = ans[(~ans["is_correct"]) & (ans["norm"] != "")]
    # (participant, task) -> неверный непустой ответ
    wmap = {pid: dict(zip(g["task_number"], g["norm"])) for pid, g in wrong.groupby("participant_id")}
    pair_rows = []
    for (school, variant), sv in s.groupby(["school_code", "variant_id"]):
        lam_v = float(lam.get(variant, 0.0))
        for cg, g in sv.groupby("cmp_group"):
            ids = sorted(g["participant_id"])
            for a, b in itertools.combinations(ids, 2):
                wa, wb = wmap.get(a, {}), wmap.get(b, {})
                k = sum(1 for t, v in wa.items() if wb.get(t) == v)
                pv = float(poisson.sf(k - 1, lam_v)) if k > 0 else 1.0     # P(X >= k)
                pair_rows.append((school, cg, variant, a, b, k, lam_v, pv))
    pairs = pd.DataFrame(pair_rows, columns=["school_code", "cmp_group", "variant_id", "a", "b", "k", "lam", "p_value"])
    pairs["p_adj"] = np.nan
    # поправка Холма по всем парам школы (FR-36)
    for school, idx in pairs.groupby("school_code").groups.items():
        pairs.loc[idx, "p_adj"] = holm_adjust(pairs.loc[idx, "p_value"].to_numpy())
    pairs["suspicious"] = (pairs["k"] >= params.k_min) & (pairs["p_adj"] < params.alpha)

    # группы — связные компоненты графа подозрительных пар (FR-37)
    sus = pairs[pairs["suspicious"]]
    group_rows = []
    g = nx.Graph()
    g.add_edges_from(zip(sus["a"], sus["b"]))
    by_pid = s.set_index("participant_id")
    for comp in nx.connected_components(g):
        members = sorted(comp)
        edges = sus[sus["a"].isin(comp) & sus["b"].isin(comp)]
        first = by_pid.loc[members[0]]
        room = first["room_code"] if "room_code" in by_pid.columns and pd.notna(first["room_code"]) else None
        group_rows.append({"exam_type": first["exam_type"], "subject_code": first["subject_code"],
                           "school_code": first["school_code"], "room_code": room,
                           "variant_id": first["variant_id"], "participant_ids": ",".join(members),
                           "size": len(members), "min_p_value": float(edges["p_adj"].min()),
                           "min_p_raw": float(edges["p_value"].min())})
    groups = pd.DataFrame(group_rows)
    if not groups.empty:
        groups = groups.sort_values(["school_code", "participant_ids"]).reset_index(drop=True)

    n_school = s.groupby("school_code").size()
    in_group = pd.Series(0, index=n_school.index, dtype=float)
    for _, r in groups.iterrows():
        in_group[r["school_code"]] += r["size"]
    share = (in_group / n_school)
    return P1Result(pairs, groups, share[share.index.isin(eligible_schools)])


# ------------------------------------------------------------------------------- П2
def compute_p2(sessions_fin: pd.DataFrame, ans: pd.DataFrame) -> pd.DataFrame:
    """FR-38: ошибки Гуттмана G и G* = G / (r × (n − r)) для каждого участника; П2 школы — медиана G*.

    Задания варианта упорядочены по доле верных ответов среди участников варианта (от легких к трудным);
    пара считается ошибкой, если более легкое задание решено неверно, а более трудное — верно.
    Ничьи по доле верных разрешаются номером задания (меньший номер — легче), чтобы порядок был полным,
    а G* лежало в [0; 1] и равнялось 1 у участника, решившего только самые трудные задания (T-47).
    Так же считает эталонная реализация набора.
    """
    a = ans[ans["participant_id"].isin(sessions_fin["participant_id"])]
    var = sessions_fin.set_index("participant_id")["variant_id"]
    out = []
    for variant, vp in var.groupby(var):
        av = a[a["participant_id"].isin(vp.index)]
        if av.empty:
            continue
        mat = av.pivot_table(index="participant_id", columns="task_number", values="is_correct", aggfunc="first")
        mat = mat.dropna(axis=1, how="all").fillna(False).astype(bool)
        ease = mat.mean(axis=0)                                  # доля верных по заданию
        order = sorted(mat.columns, key=lambda t: (-ease[t], t))  # от легких к трудным, ничья — по номеру
        M = mat[order].to_numpy()                                 # столбцы уже упорядочены по трудности
        n = M.shape[1]
        upper = np.triu(np.ones((n, n), dtype=bool), k=1)         # i < j: i легче j
        for pid, row in zip(mat.index, M):
            r = int(row.sum())
            if r == 0 or r == n:
                G, gstar = 0, 0.0
            else:
                wrong_easy = (~row)[:, None] & row[None, :]            # i решено неверно, j решено верно
                G = int((wrong_easy & upper).sum())
                gstar = G / (r * (n - r))
            out.append((pid, G, gstar))
    return pd.DataFrame(out, columns=["participant_id", "G", "g_star"])


# ------------------------------------------------------------------------------- П3
def compute_p3(sessions_fin: pd.DataFrame, ans: pd.DataFrame, eligible_schools: set) -> pd.Series:
    """FR-39: П3 = (средняя разность «процент выполнения в школе − в регионе» по трудным заданиям)
    − (то же по легким), в п. п.; трудные — задания, у которых доля верных по региону ниже медианы.

    Задание — это номер задания: варианты параллельные, у заданий с одним номером одинаковые коды
    кодификатора (README набора), поэтому доли считаются по номеру по всем вариантам. Медиана берется
    по заданиям региона. Разбивка по паре (вариант, номер) дает другие значения и с эталоном не сходится.
    """
    s = sessions_fin[["participant_id", "region_code", "school_code"]]
    a = ans.drop(columns=["region_code", "school_code"], errors="ignore").merge(s, on="participant_id")
    a["ok"] = a["is_correct"].astype(float) * 100.0
    reg = a.groupby(["region_code", "task_number"])["ok"].mean().rename("reg_pct").reset_index()
    reg["median"] = reg.groupby("region_code")["reg_pct"].transform("median")
    reg["hard"] = reg["reg_pct"] < reg["median"]
    sch = a.groupby(["region_code", "school_code", "task_number"])["ok"].mean().rename("sch_pct").reset_index()
    m = sch.merge(reg, on=["region_code", "task_number"])
    m["diff"] = m["sch_pct"] - m["reg_pct"]
    res = {}
    for school, g in m.groupby("school_code"):
        if school not in eligible_schools:
            continue
        hard, easy = g.loc[g["hard"], "diff"], g.loc[~g["hard"], "diff"]
        res[school] = float(hard.mean() - easy.mean()) if len(hard) and len(easy) else 0.0
    return pd.Series(res, dtype=float)


# ------------------------------------------------------------------------------- П4
def compute_p4(sessions_fin: pd.DataFrame, threshold: float, params: Params) -> pd.DataFrame:
    """FR-40, FR-41: П4а — стандартное отклонение первичных баллов школы; П4б — доля участников с баллом
    в окне [порог; порог + Δ]. Только окончательные результаты."""
    g = sessions_fin.groupby("school_code")["primary_score"]
    p4a = g.std(ddof=1)
    in_win = sessions_fin.assign(w=(sessions_fin["primary_score"] >= threshold) &
                                 (sessions_fin["primary_score"] <= threshold + params.delta))
    p4b = in_win.groupby("school_code")["w"].mean()
    return pd.DataFrame({"P4a": p4a, "P4b": p4b})


# ------------------------------------------------------------------------------- индекс и перечень
@dataclass
class DeepResult:
    exam_type: str
    subject_code: str
    schools: pd.DataFrame        # раздел 7.4 без run_id и calculated_at
    groups: pd.DataFrame         # группы П1 (раздел 7.3) без run_id и group_id
    participants: pd.DataFrame   # П2: G и G* участников
    pairs: pd.DataFrame          # все проверенные пары П1 (для проверки и отладки)
    params: dict = field(default_factory=dict)


def analyze_exam(sessions: pd.DataFrame, answers: pd.DataFrame, subjects: pd.DataFrame,
                 params: Params = Params()) -> DeepResult:
    """Углубленный анализ одного сочетания «экзамен, предмет» (платформа = один экзамен и один предмет)."""
    fin = final_sessions(sessions)
    if fin.empty:
        raise ValueError("нет сессий с окончательным результатом")
    exam, subject = str(fin["exam_type"].iloc[0]), str(fin["subject_code"].iloc[0])
    threshold = float(subjects.loc[(subjects["exam_type"] == exam) & (subjects["subject_code"] == subject),
                                   "threshold_score"].iloc[0])
    ans = with_norm_answers(answers)
    ans = ans[ans["participant_id"].isin(fin["participant_id"])]
    ans = ans.merge(fin[["participant_id", "variant_id"]], on="participant_id")

    n = fin.groupby("school_code").size()
    eligible = set(n[n >= params.n_min].index)                            # FR-33
    region = fin.groupby("school_code")["region_code"].first()

    p1 = compute_p1(fin, ans, params, eligible)
    p2p = compute_p2(fin, ans)
    p2 = p2p.merge(fin[["participant_id", "school_code"]], on="participant_id").groupby("school_code")["g_star"].median()
    p3 = compute_p3(fin, ans, eligible)
    p4 = compute_p4(fin, threshold, params)

    df = pd.DataFrame({"participants": n}).loc[sorted(eligible)]
    df["P1"], df["P2"], df["P3"] = p1.by_school, p2, p3
    df["P4a"], df["P4b"] = p4["P4a"], p4["P4b"]
    df = df.fillna(0.0)
    # z-оценки по всем школам сочетания (FR-42), знак П4а меняется; корректировка на размер (FR-43)
    zadj = pd.DataFrame(index=df.index)
    for col in INDICATORS:
        z = robust_z(df[col])
        if col == "P4a":
            z = -z
        zadj[col] = z * df["participants"] / (df["participants"] + params.k_adj)
    agg = zadj.max(axis=1) if params.index_agg == "max" else zadj.mean(axis=1)
    out = pd.DataFrame({
        "exam_type": exam, "subject_code": subject, "region_code": region.loc[df.index].to_numpy(),
        "school_code": df.index, "participants": df["participants"].astype(int).to_numpy(),
        "p1": df["P1"].to_numpy(), "p2": df["P2"].to_numpy(), "p3": df["P3"].to_numpy(),
        "p4a": df["P4a"].to_numpy(), "p4b": df["P4b"].to_numpy(),
        "z1": zadj["P1"].to_numpy(), "z2": zadj["P2"].to_numpy(), "z3": zadj["P3"].to_numpy(),
        "z4a": zadj["P4a"].to_numpy(), "z4b": zadj["P4b"].to_numpy(),
        "index": agg.to_numpy()})
    out["in_list"] = out["index"] >= params.i_min
    out = out.sort_values(["index", "school_code"], ascending=[False, True]).reset_index(drop=True)
    groups = p1.groups.copy()
    if not groups.empty:
        groups.insert(0, "region_code", groups["school_code"].map(fin.groupby("school_code")["region_code"].first()))
    return DeepResult(exam, subject, out, groups, p2p, p1.pairs, params.as_dict())
