"""Графики на новых кодах, пропусках, нулях и разных объемах данных."""

import importlib.util
import math
import unittest
from unittest.mock import Mock

from dashboard import charts
from dashboard.pages._common import _chart_page


@unittest.skipUnless(importlib.util.find_spec("matplotlib"), "Нужен matplotlib")
class AdaptiveChartsTests(unittest.TestCase):
    def test_comparison_uses_dynamic_axes_and_does_not_replace_missing_with_zero(self):
        import numpy as np

        rows = [{"region_code": "new-1", "region_name": "Новый регион " * 30, "exam_type": "EGE",
                 "mean_pct": 0}, {"region_code": "new-2", "region_name": None,
                                  "exam_type": "OGE", "mean_pct": 75}]
        axis = charts.comparison_heatmap(rows, ["OGE", "EGE"]).axes[0]
        values = axis.images[0].get_array()
        self.assertEqual(values.shape, (2, 2))
        self.assertTrue(np.ma.is_masked(values[0, 0]))
        self.assertEqual(values[0, 1], 0)
        self.assertEqual(values[1, 0], 75)
        self.assertEqual([t.get_text() for t in axis.get_xticklabels()], ["ОГЭ", "ЕГЭ"])
        self.assertEqual(axis.images[0].get_clim(), (0, 100))
        self.assertLessEqual(len(axis.get_yticklabels()[0].get_text().splitlines()), 2)
        self.assertTrue(axis.get_yticklabels()[0].get_text().endswith("…"))

    def test_heatmap_annotation_contrast_is_readable_at_all_fill_levels(self):
        from matplotlib.colors import to_rgb

        rows = [{"region_code": str(value), "region_name": str(value), "exam_type": "EGE",
                 "mean_pct": value} for value in (0, 25, 50, 75, 100)]
        axis = charts.comparison_heatmap(rows, ["EGE"]).axes[0]
        image = axis.images[0]
        for text in axis.texts:
            value = float(text.get_text().replace(",", "."))
            luminances = []
            for rgb in (to_rgb(text.get_color()), image.cmap(image.norm(value))[:3]):
                linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055)**2.4 for c in rgb]
                luminances.append(sum(c * w for c, w in
                                      zip(linear, (.2126, .7152, .0722), strict=True)))
            self.assertGreaterEqual((max(luminances) + .05) / (min(luminances) + .05), 4.5)

    def test_variant_cells_keep_real_zero_and_mask_zero_participants(self):
        import numpy as np

        rows = [{"variant_id": variant, "task_number": task, "share_score": value,
                 "participants": count} for variant, task, value, count in
                (("new-A", 101, 0, 5), ("new-A", 105, .9, 0), ("new-B", 105, .25, 4))]
        axis = charts.variants_heatmap(rows, {105}).axes[0]
        values = axis.images[0].get_array()
        self.assertEqual(values[0, 0], 0)
        self.assertTrue(np.ma.is_masked(values[0, 1]))
        self.assertTrue(np.ma.is_masked(values[1, 0]))
        self.assertEqual(values[1, 1], 25)
        self.assertEqual([t.get_text() for t in axis.get_xticklabels()], ["101", "105 *"])

    def test_empty_heatmaps_and_distribution_do_not_fail(self):
        for figure in (charts.comparison_heatmap([], []), charts.variants_heatmap([], set()),
                       charts.cumulative_distribution([])):
            self.assertTrue(any(t.get_text() == "Нет данных" for t in figure.axes[0].texts))

    def test_ranking_reference_uses_weights_from_the_full_selection(self):
        rows = [{"region_code": "new-A", "region_name": "А", "mean_pct": 90,
                 "participants": 1}, {"region_code": "new-B", "region_name": "Б",
                                       "mean_pct": 10, "participants": 9}]
        axis = charts.region_ranking(rows[:1], rows).axes[0]
        self.assertEqual(axis.patches[0].get_width(), 90)
        self.assertEqual(list(axis.lines[0].get_xdata()), [18, 18])
        self.assertIn("n=1", axis.get_yticklabels()[0].get_text())

    def test_statuses_distinguish_unknown_summary_from_zero_counts(self):
        rows = [{"exam_type": "new-A", "in_progress": 2, "finished": 5,
                 "annulled": 0, "absent": 3}, {"exam_type": "new-B", "in_progress": None,
                                               "finished": 4, "annulled": 0, "absent": 0},
                {"exam_type": "new-C", "in_progress": 0, "finished": 0,
                 "annulled": 0, "absent": 0}]
        axis = charts.platform_statuses(rows).axes[0]
        self.assertEqual([c.patches[0].get_width() for c in axis.containers], [2, 5, 0, 3])
        self.assertTrue(all(math.isnan(c.patches[1].get_width()) for c in axis.containers))
        self.assertTrue(all(c.patches[2].get_width() == 0 for c in axis.containers))
        self.assertEqual(sum(t.get_text() == "Нет полной сводки" for t in axis.texts), 1)

    def test_freshness_preserves_unknown_zero_boundary_and_large_outlier(self):
        rows = [{"platform": "new", "endpoint": str(i), "lag_sec": value}
                for i, value in enumerate((None, 0, 120, 121, 10**8))]
        axis = charts.freshness_delays(rows).axes[0]
        self.assertTrue(math.isnan(axis.patches[0].get_width()))
        self.assertEqual([p.get_width() for p in axis.patches[1:]], [0, 120, 121, 10**8])
        self.assertEqual(axis.patches[1].get_facecolor(), axis.patches[2].get_facecolor())
        self.assertNotEqual(axis.patches[2].get_facecolor(), axis.patches[3].get_facecolor())
        self.assertGreater(axis.get_xlim()[1], 10**8)
        self.assertEqual(list(axis.lines[0].get_xdata()), [120, 120])

    def test_cumulative_percentages_use_frequencies_and_reach_one_hundred(self):
        rows = [{"primary_score": score, "participants": count, "max_primary_score": 20}
                for score, count in ((10, 3), (0, 1), (0, 1))]
        line = charts.cumulative_distribution(rows).axes[0].lines[0]
        self.assertEqual(list(line.get_xdata()), [-0.6, 0, 10, 20.6])
        self.assertEqual(list(line.get_ydata()), [0, 40, 100, 100])

    def test_many_identifiers_are_not_limited_to_fixture_labels(self):
        rows = [{"variant_id": "new-" + str(i), "task_number": 100 + t,
                 "share_score": (i + t) / 100, "participants": 1}
                for i in range(20) for t in range(20)]
        axis = charts.variants_heatmap(rows, set()).axes[0]
        self.assertEqual(axis.images[0].get_array().shape, (20, 20))
        self.assertEqual(len(axis.get_xticklabels()), 20)
        self.assertEqual(len(axis.get_yticklabels()), 20)


class ChartPaginationTests(unittest.TestCase):
    def test_every_item_is_available_across_pages_and_filter_shrink_resets_page(self):
        st = Mock()
        st.session_state = {}
        st.selectbox.side_effect = lambda label, options, **kwargs: st.session_state[kwargs["key"]]
        items = list(range(53))
        shown = []
        for page in range(3):
            st.session_state["page"] = page
            chunk = _chart_page(st, Mock(), items, "page")
            self.assertLessEqual(len(chunk), 20)
            shown.extend(chunk)
        self.assertEqual(shown, items)
        reduced = _chart_page(st, Mock(), items[:21], "page")
        self.assertEqual(reduced, items[:20])
        self.assertEqual(st.session_state["page"], 0)
        self.assertEqual(_chart_page(st, Mock(), [], "page"), [])


if __name__ == "__main__":
    unittest.main()
