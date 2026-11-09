"""Показатели П1–П4 и индекс школы (ТЗ СМА v0.3, FR-36 и таблица параметров).

Данные: mart.answers_flat, mart.task_stats, mart.task_stats_region, mart.sessions_current.
Результат одного расчета:
    analytics.analysis_runs — запуск (params — параметры расчета);
    analytics.p1_groups     — группы П1;
    analytics.school_index  — показатели и индекс школ, in_list — школа в списке на проверку.
"""
