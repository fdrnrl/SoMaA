"""Числа четырех графиков против контрольного JSON, без PostgreSQL."""

import importlib.util
import json
import math
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from statistics import median

from dashboard import charts, data, geo
from dashboard.demo import DemoData
from dashboard.presentation import comparison_rows, distribution_stats

FIXTURES = Path(__file__).with_name("fixtures")
HAS_MATPLOTLIB = importlib.util.find_spec("matplotlib") is not None


@unittest.skipUnless(HAS_MATPLOTLIB, "Для проверки объектов графиков нужен matplotlib")
class ChartReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = DemoData(FIXTURES / "small.zip")
        cls.reference = json.loads((FIXTURES / "small_summary.json").read_text(encoding="utf-8"))

    def test_distribution_bar_counts_match_independently_selected_raw_records(self):
        with zipfile.ZipFile(FIXTURES / "small.zip") as archive:
            for exam in data.EXAMS:
                records = []
                for name in archive.namelist():
                    if name.startswith(exam.lower() + "/sessions") and name.endswith(".json"):
                        records.extend(json.loads(archive.read(name))["items"])
                current = {}
                for row in sorted(records, key=lambda r: (r["updated_at"], r["record_id"])):
                    if not row["deleted"]:
                        current[row["participant_id"]] = row
                final = [r for r in current.values() if r["status"] == "finished"
                         and r["result_status"] in ("final", "appeal_changed")]
                for subject in {r["subject_code"] for r in final}:
                    expected = Counter(r["primary_score"] for r in final
                                       if r["subject_code"] == subject)
                    result = charts.distribution(self.reader.distribution(exam, subject))
                    actual = {round(p.get_x() + p.get_width() / 2): p.get_height()
                              for p in result.axes[0].patches}
                    self.assertEqual(actual, dict(expected), (exam, subject))
                    scores = [r["primary_score"] for r in final if r["subject_code"] == subject]
                    stats = distribution_stats(self.reader.distribution(exam, subject))
                    self.assertEqual(stats["participants"], len(scores))
                    self.assertAlmostEqual(stats["mean"], sum(scores) / len(scores), places=8)
                    self.assertEqual(stats["median"], median(scores))
                    for value in (sum(scores) / len(scores), median(scores)):
                        self.assertTrue(any(math.isclose(line.get_xdata()[0], value,
                                                        abs_tol=1e-9)
                                            for line in result.axes[0].lines))

    def test_coincident_mean_and_median_have_one_explicitly_labeled_marker(self):
        rows = [{"primary_score": score, "participants": 2, "max_primary_score": 10}
                for score in (2, 6)]
        axis = charts.distribution(rows).axes[0]
        self.assertEqual(len(axis.lines), 1)
        self.assertEqual(list(axis.lines[0].get_xdata()), [4, 4])
        self.assertEqual(axis.lines[0].get_label(), "Среднее = медиана: 4")

    def test_comparison_bars_match_reference_within_point_one_percentage_point(self):
        rows = comparison_rows(self.reader.region_results())
        result = charts.comparison(rows, data.EXAMS)
        regions = sorted({r["region_code"] for r in rows})
        reference = {(r["exam_type"], r["region_code"]): float(r["mean_pct"])
                     for r in self.reference["region_results"]}
        for exam, container in zip(data.EXAMS, result.axes[0].containers, strict=True):
            for region, patch in zip(regions, container.patches, strict=True):
                expected = reference.get((exam, region))
                if expected is None:
                    self.assertTrue(math.isnan(patch.get_width()))
                else:
                    self.assertLessEqual(abs(patch.get_width() - expected), 0.1)

    def test_grade_bars_match_reference_without_deriving_grades(self):
        for exam in ("OGE", "VPR"):
            rows = self.reader.region_results([exam])
            result = charts.bars([r["region_code"] for r in rows],
                                 [r["share_grade2"] * 100 for r in rows], "grade2, %")
            expected = {r["region_code"]: float(r["share_grade2"]) * 100
                        for r in self.reference["region_results"] if r["exam_type"] == exam}
            for row, patch in zip(rows, result.axes[0].patches, strict=True):
                self.assertLessEqual(abs(patch.get_width() - expected[row["region_code"]]), .1)

    def test_map_mean_and_polygon_color_match_reference_and_missing_stays_gray(self):
        from matplotlib import colormaps
        from matplotlib.colors import Normalize, to_rgba

        features = geo.load_regions()
        for exam in data.EXAMS:
            rows = self.reader.region_results([exam])
            expected = {r["region_code"]: float(r["mean_score"])
                        for r in self.reference["region_results"] if r["exam_type"] == exam}
            for row in rows:
                self.assertLessEqual(abs(row["mean_score"] - expected[row["region_code"]]), .01)
            result = charts.regions(rows, features)
            norm = Normalize(vmin=0, vmax=max(expected.values()))
            index = 0
            for feature in features:
                geometry = feature["geometry"]
                count = 1 if geometry["type"] == "Polygon" else len(geometry["coordinates"])
                code = feature["properties"]["region_code"]
                color = colormaps["viridis"](norm(expected[code])) if code in expected \
                    else to_rgba("#DDDDDD")
                for patch in result.axes[0].patches[index:index + count]:
                    for actual, target in zip(patch.get_facecolor(), color, strict=True):
                        self.assertAlmostEqual(actual, target, places=6)
                index += count
            self.assertEqual(index, len(result.axes[0].patches))


if __name__ == "__main__":
    unittest.main()
