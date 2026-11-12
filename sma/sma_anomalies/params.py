"""Параметры правил и углубленного анализа (таблица 13 ТЗ v0.4).

Менять их может только администратор СМА (раздел 5.5), поэтому все значения собраны
в одном объекте, который пишется в журнал каждого запуска (NFR-24, T-41).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

FINAL_RESULTS = ("final", "appeal_changed")      # окончательный результат (глоссарий)
COUNTED_EVENTS = ("tab_switch", "copy", "paste", "fullscreen_exit")  # FR-10; focus_lost не считается


@dataclass(frozen=True)
class Params:
    # --- таблица 13 ---
    n_min: int = 10          # N_min: наименьшее число участников школы (FR-33, FR-07, FR-08)
    k_min: int = 3           # k_min: наименьшее число совпавших неверных ответов в паре (FR-36)
    alpha: float = 0.01      # α: уровень значимости после поправки Холма (FR-36)
    delta: int = 2           # Δ: окно над пороговым баллом, первичных баллов (FR-41)
    k_adj: int = 20          # K: корректировка на размер школы (FR-43)
    i_min: float = 2.0       # И_min: порог индекса для перечня (FR-44)
    # --- оперативные правила (разделы FR-07..FR-10) ---
    a1_sigma_mult: float = 2.0   # «более чем на 2σ»
    a1_min_schools: int = 3      # FR-08: школ с n >= N_min в регионе
    a1_ddof: int = 1             # σ по выборке (как pandas .std()); ТЗ ddof не уточняет — см. README, «вопросы к ТЗ»
    a2_min_group: int = 5        # FR-09: 5 и более участников
    a2_min_nonempty: float = 0.5  # FR-09: не менее половины ответов непустые
    a3_min_events: int = 3       # FR-10
    # --- варианты расчета, которых в ТЗ нет (по умолчанию выключены = поведение ТЗ v0.4) ---
    a1_sigma_mode: str = "all"      # "all" — как в FR-07; "leave_one_out" — σ без проверяемой школы (раздел 10 ТЗ)
    index_agg: str = "mean"         # "mean" — как в FR-44; "max" — максимум z′ (наблюдение 3 README)

    def as_dict(self) -> dict:
        return asdict(self)
