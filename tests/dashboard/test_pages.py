"""Проверки правил отображения и защиты страниц без БД и сторонних библиотек."""

import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from test_auth import NOW, FakeStreamlit, StopPage

from dashboard import data, geo, pages, presentation, ui
from dashboard.pages import _common, analysis, live


class PresentationTests(unittest.TestCase):
    def variants(self, *shares):
        return [{"exam_type": "EGE", "subject_code": "02", "task_number": 1,
                 "variant_id": str(i), "share_score": s, "participants": 10,
                 "diff_from_others": 0} for i, s in enumerate(shares)]

    def test_variant_threshold_is_strict_and_uses_max_min(self):
        self.assertFalse(presentation.variant_gaps(self.variants(.5, .65))[0]["flagged"])
        self.assertTrue(presentation.variant_gaps(self.variants(.5, .6501))[0]["flagged"])
        result = presentation.variant_gaps(self.variants(.4, .5, .6))[0]
        self.assertTrue(result["flagged"])
        self.assertAlmostEqual(result["gap_pp"], 20)

    def test_one_variant_and_null_or_empty_results_are_not_comparisons(self):
        self.assertIsNone(presentation.variant_gaps(self.variants(.6))[0]["gap_pp"])
        rows = self.variants(None, .9)
        rows[1]["participants"] = 0
        self.assertEqual(presentation.variant_gaps(rows), [])

    def test_variant_tasks_are_not_mixed_across_exam_and_subject(self):
        rows = self.variants(.3, .8)
        rows[1]["subject_code"] = "01"
        self.assertTrue(all(r["gap_pp"] is None for r in presentation.variant_gaps(rows)))

    def test_comparison_is_weighted_and_missing_is_not_zero(self):
        rows = [{"exam_type": "EGE", "region_code": "02", "region_name": "Region",
                 "participants": n, "mean_pct": score} for n, score in ((10, 20), (30, 80))]
        result = presentation.comparison_rows(rows)
        self.assertEqual(result[0]["mean_pct"], 65)
        rows[0]["mean_pct"] = rows[1]["mean_pct"] = None
        self.assertIsNone(presentation.comparison_rows(rows)[0]["mean_pct"])

    def test_new_anomalies_use_stable_id_not_recalculation_time(self):
        state = {}
        row = {"anomaly_id": 1, "detected_at": NOW}
        self.assertEqual(presentation.anomaly_feed(state, [row], "database"), [(row, False)])
        changed = dict(row, detected_at=NOW + timedelta(seconds=30))
        self.assertFalse(presentation.anomaly_feed(state, [changed], "database")[0][1])
        new = {"anomaly_id": 2, "detected_at": NOW - timedelta(seconds=1)}
        self.assertEqual(presentation.anomaly_feed(state, [changed, new], "database")[0],
                         (new, True))
        self.assertFalse(presentation.anomaly_feed(state, [new], "demo")[0][1])

    def test_delay_is_strictly_greater_than_120_seconds(self):
        self.assertFalse(presentation.is_stale(NOW - timedelta(seconds=120), NOW))
        self.assertTrue(presentation.is_stale(NOW - timedelta(seconds=121), NOW))
        self.assertIsNone(presentation.is_stale(None, NOW))
        self.assertIn("МСК", presentation.time_text(NOW))


class GeometryTests(unittest.TestCase):
    def test_real_novosibirsk_polygon_and_twenty_unique_codes(self):
        features = geo.load_regions()
        mapping = {f["properties"]["region_code"]: f for f in features
                   if f["properties"]["region_code"]}
        self.assertEqual(len(mapping), 20)
        shape = mapping["54"]["geometry"]
        ring = shape["coordinates"][0]
        if shape["type"] == "MultiPolygon":
            ring = ring[0]
        self.assertGreater(len(ring), 20)
        self.assertTrue(all(74 < lon < 86 and 52 < lat < 58 for lon, lat in ring))
        self.assertEqual(geo.unmapped_codes([{"region_code": "54"}, {"region_code": "99"}],
                                            features), ["99"])


class PageContractTests(unittest.TestCase):
    def test_feed_marks_new_record_with_text_and_does_not_repeat_on_recalculation(self):
        st = Mock()
        st.session_state = {}
        row = {"anomaly_id": 1, "detected_at": NOW, "rule": "A1", "exam_type": "EGE",
               "object_id": "s001"}
        live._feed(st, "demo", {"rows": [row], "error": None})
        st.warning.assert_not_called()
        added = dict(row, anomaly_id=2, object_id="s002")
        live._feed(st, "demo", {"rows": [row, added], "error": None})
        message = st.warning.call_args.args[0]
        self.assertTrue(message.startswith("НОВАЯ · А1"))
        self.assertIn("s002", message)
        st.warning.reset_mock()
        live._feed(st, "demo", {"rows": [row, added], "error": None})
        st.warning.assert_not_called()

    def test_live_refreshes_every_thirty_seconds_without_activity_callback(self):
        st, service = Mock(), Mock()
        st.fragment.side_effect = lambda **kwargs: lambda function: function
        with patch.object(live, "_live_body") as body, \
                patch.object(ui, "user_activity") as touch:
            pages.live_page(st, service, "demo")
            st.fragment.assert_called_once_with(run_every="30s")
            body.assert_called_once_with(st, service, "demo")
            touch.assert_not_called()

    def test_every_working_page_guards_before_reading_or_displaying_data(self):
        service = Mock()
        from dashboard.auth import AccessDenied

        service.require_user.side_effect = AccessDenied("Нет доступа")
        with patch.object(data, "read_page") as read:
            for _, _, render in pages.WORK_PAGES:
                with self.subTest(page=render.__name__), self.assertRaises(StopPage):
                    render(FakeStreamlit(), service, "demo")
            read.assert_not_called()

    def test_missing_optional_source_does_not_become_empty_success(self):
        missing = Mock(side_effect=data.DataUnavailable("Нет источника",
                                                       "source_not_configured"))
        result = _common._optional(missing)
        self.assertIsNone(result["rows"])
        self.assertEqual(result["error"], "Нет источника")
        self.assertEqual(_common._optional(lambda: []), {"rows": [], "error": None})
        with self.assertRaises(data.DataUnavailable):
            _common._optional(Mock(side_effect=data.DataUnavailable("БД недоступна")))

    def test_school_and_groups_use_same_reader_run_and_exact_school_key(self):
        reader = Mock()
        reader.completed_run.return_value = {"run_id": 7}
        reader.school_list.return_value = [{"exam_type": "EGE", "subject_code": "02",
                                            "school_code": "s001"}]
        reader.p1_groups.return_value = [{"run_id": 7, "size": 5}]
        selected = ("EGE", "02", "s001")
        result = analysis._school_bundle(reader, 7, "EGE", "02", selected)
        reader.school_list.assert_called_once_with(7, "EGE", "02")
        reader.p1_groups.assert_called_once_with(7, *selected)
        self.assertEqual(result["groups"]["rows"][0]["run_id"], 7)

    def test_empty_completed_school_list_and_unknown_school_do_not_query_groups(self):
        reader = Mock()
        reader.school_list.return_value = []
        result = analysis._school_bundle(reader, 7, None, None, ("EGE", "02", "s001"))
        self.assertEqual(result["schools"], [])
        self.assertIsNone(result["groups"])
        reader.p1_groups.assert_not_called()


if __name__ == "__main__":
    unittest.main()
