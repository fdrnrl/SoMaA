"""Чтение синтетического ZIP-снимка без PostgreSQL и сторонних библиотек."""

import json
import zipfile
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath

from dashboard.data import EXAMS, DataUnavailable, _conditions, display_value, filter_options


def _latest(records, fields):
    current = {}
    for row in sorted(records, key=lambda r: (datetime.fromisoformat(r["updated_at"]),
                                             r["record_id"])):
        if not row["deleted"]:
            current[tuple(row[field] for field in fields)] = row
    return list(current.values())


def _average(values, places="0.0001"):
    if not values:
        return None
    return (sum(values) / len(values)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


class DemoData:
    """Статический источник; ключ аномалий не выдается за результаты аналитика."""

    def __init__(self, path):
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("synthetic") is not True:
                    raise ValueError("Нужен синтетический набор")
                self.files = {
                    name: json.loads(archive.read(name)) for name in archive.namelist()
                    if name.endswith(".json") and PurePosixPath(name).parts[0] in
                    {exam.lower() for exam in EXAMS}
                }
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            raise DataUnavailable("Не удалось прочитать синтетический набор.",
                                  "demo_unavailable") from None
        self.metadata = {
            "mode": "demo", "synthetic": True, "snapshot": manifest["snapshot"],
            "note": "Данные синтетические; выводы ограничены свойствами генератора. "
                    "Статический снимок, загрузки в БД и автообновление не проверены.",
            "snapshot_times": {exam: self.files[exam.lower() + "/live-status.json"]
                               ["generated_at"] for exam in EXAMS},
        }
        self.sessions = []
        for exam in EXAMS:
            folder = exam.lower()
            regions = {r["region_code"]: r["name"]
                       for r in self._records(folder, "dictionaries-regions")}
            for row in _latest(self._records(folder, "sessions"),
                               ("exam_type", "participant_id")):
                self.sessions.append({**row, "region_name": regions.get(row["region_code"])})

    def _records(self, folder, endpoint):
        result = []
        for name, content in self.files.items():
            path = PurePosixPath(name)
            if str(path.parent) == folder and (
                path.name == endpoint + ".json" or path.name.startswith(endpoint + ".page-")
            ):
                result.extend(content["items"])
        return result

    def _sessions(self, exam_type=None, subject_code=None, region_code=None):
        _conditions(exam_type, subject_code, region_code)
        return [r for r in self.sessions
                if (exam_type is None or r["exam_type"] == exam_type)
                and (subject_code is None or r["subject_code"] == subject_code)
                and (region_code is None or r["region_code"] == region_code)]

    @staticmethod
    def _final(rows):
        return [r for r in rows if r["status"] == "finished"
                and r["result_status"] in ("final", "appeal_changed")]

    def filters(self, exam_type=None, subject_code=None):
        return filter_options(self._sessions(exam_type, subject_code))

    def distribution(self, exam_type, subject_code, region_code=None):
        groups = defaultdict(list)
        for row in self._final(self._sessions(exam_type, subject_code, region_code)):
            groups[row["primary_score"]].append(row)
        return [{"primary_score": score, "participants": len(rows),
                 "max_primary_score": max(r["max_primary_score"] for r in rows)}
                for score, rows in sorted(groups.items())]

    def region_results(self, exam_types=None, subject_code=None):
        if exam_types is not None and (
            isinstance(exam_types, str) or any(e not in EXAMS for e in exam_types)
        ):
            raise ValueError("Нужен список известных экзаменов")
        groups = defaultdict(list)
        for row in self._sessions(subject_code=subject_code):
            if exam_types is None or row["exam_type"] in exam_types:
                groups[(row["exam_type"], row["subject_code"], row["region_code"])].append(row)
        result = []
        for (exam, subject, region), rows in sorted(groups.items()):
            final = self._final(rows)
            result.append({
                "exam_type": exam, "subject_code": subject, "region_code": region,
                "region_name": rows[0]["region_name"], "participants": len(final),
                "schools": len({r["school_code"] for r in final}),
                "mean_score": _average([Decimal(r["primary_score"]) for r in final], "0.01"),
                "mean_pct": _average([Decimal(100) * r["primary_score"] /
                                      r["max_primary_score"] for r in final], "0.01"),
                "share_grade2": _average([Decimal(r[exam.lower()]["grade"] == 2)
                                          for r in final]) if exam in ("OGE", "VPR") else None,
                "share_absent": _average([Decimal(r["status"] == "absent") for r in rows]),
                "share_annulled": _average([Decimal(r["status"] == "annulled") for r in rows]),
            })
        return display_value(result)

    def live(self):
        return [{**self.files[exam.lower() + "/live-status.json"],
                 "generated_at": display_value(datetime.fromisoformat(
                     self.files[exam.lower() + "/live-status.json"]["generated_at"])),
                 "last_successful_load_at": None, "anomalies_by_rule": None}
                for exam in sorted(EXAMS)]

    def freshness(self):
        return []

    def _scores(self, exam_type, subject_code):
        sessions = self._sessions(exam_type, subject_code)
        if any(r["status"] == "finished" and r["result_status"] == "pending" for r in sessions):
            raise DataUnavailable("Для такого снимка нужно согласовать отбор pending.",
                                  "policy_not_agreed")
        sessions = {r["participant_id"]: r for r in sessions if r["status"] == "finished"}
        scores = defaultdict(list)
        for endpoint in ("answers", "expert-reviews"):
            rows = _latest(self._records(exam_type.lower(), endpoint),
                           ("exam_type", "participant_id", "task_number"))
            for row in rows:
                session = sessions.get(row["participant_id"])
                if session is None or (endpoint == "expert-reviews"
                                       and row["check_status"] != "checked"):
                    continue
                key = (session["variant_id"], row["task_number"])
                scores[key].append((row["participant_id"],
                                    Decimal(row["score"]) / row["max_score"]))
        return scores

    def topics(self, exam_type, subject_code):
        _conditions(exam_type, subject_code)
        scores = self._scores(exam_type, subject_code)
        groups = {}
        tasks = _latest(self._records(exam_type.lower(), "dictionaries-kim"),
                        ("exam_type", "subject_code", "exam_year", "variant_id", "task_number"))
        for task in tasks:
            if task["subject_code"] != subject_code:
                continue
            for code in task["content_codes"]:
                key = (task["exam_year"], task["section"], code, task["level"])
                group = groups.setdefault(key, {"tasks": set(), "scores": []})
                task_key = (task["variant_id"], task["task_number"])
                group["tasks"].add(task_key)
                group["scores"].extend(share for _, share in scores.get(task_key, []))
        return display_value([
            {"exam_type": exam_type, "subject_code": subject_code, "exam_year": year,
             "section": section, "content_code": code, "level": level,
             "tasks": len(group["tasks"]), "n_answers": len(group["scores"]),
             "mean_share_score": _average(group["scores"])}
            for (year, section, code, level), group in sorted(groups.items())
        ])

    def variants(self, exam_type, subject_code):
        _conditions(exam_type, subject_code)
        scores = self._scores(exam_type, subject_code)
        means = {key: _average([share for _, share in values]) for key, values in scores.items()}
        result = []
        for (variant, task), share in sorted(means.items(), key=lambda item: item[0][::-1]):
            others = [value for (v, t), value in means.items() if t == task and v != variant]
            result.append({
                "exam_type": exam_type, "subject_code": subject_code, "task_number": task,
                "variant_id": variant,
                "participants": len({p for p, _ in scores[(variant, task)]}),
                "share_score": share,
                "diff_from_others": (share - sum(others) / len(others)).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP) if others else None,
            })
        return display_value(result)

    @staticmethod
    def _no_analysis(*args, **kwargs):
        raise DataUnavailable("В снимке нет сохраненных результатов аналитика.",
                              "source_not_configured")

    anomalies = analysis_runs = completed_run = _no_analysis
    school_list = p1_groups = school_counts = _no_analysis
