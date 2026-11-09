"""Правила А1–А3 (ТЗ СМА v0.3, FR-07–FR-10). Результат — в analytics.anomalies.

Данные:
    А1 — mart.school_results (mean_score, participants по школам региона);
    А2 — mart.answers_flat (answer_norm, school_code, variant_id);
    А3 — mart.proctoring_counts (counted_events).
Запись в analytics.anomalies — «с заменой» по ключу (rule, exam_type, object_type, object_id).
Проверка — по ключу data/generated/medium/answer_key.json (planted и decoys).
"""
