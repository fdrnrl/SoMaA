"""Общие мелочи тестов визуализации."""

import pandas as pd


def sess(pid, score=10, status="finished", rs="final", region="16", school="16001", exam="OGE",
         subj="02", grade=None, deleted=False, upd="2026-12-05T20:00:00+03:00", mx=31,
         variant="v1"):
    return {
        "record_id": f"r_{pid}_{upd}", "participant_id": pid, "exam_type": exam,
        "subject_code": subj, "region_code": region, "school_code": school,
        "variant_id": variant, "status": status, "result_status": rs, "primary_score": score,
        "max_primary_score": mx, "grade": grade, "updated_at": upd, "deleted": deleted,
    }


def frame(rows):
    return pd.DataFrame(rows)
