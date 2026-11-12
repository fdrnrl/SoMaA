"""Модуль аналитика аномалий СМА: правила А1–А3, показатели П1–П4, индекс аномальности (ТЗ v0.4, FR-07–FR-13, FR-30–FR-47)."""
from .params import Params
from .rules import rule_a1, rule_a2, rule_a3, run_rules, merge_new
from .deep import analyze_exam, DeepResult
from .analysis import analyze_platform, run_dataset

__all__ = ["Params", "rule_a1", "rule_a2", "rule_a3", "run_rules", "merge_new",
           "analyze_exam", "DeepResult", "analyze_platform", "run_dataset"]
