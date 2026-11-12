"""Настоящий Streamlit AppTest; тестовые учетные записи существуют только в этом файле."""

import importlib.util
import unittest
from collections import Counter
from statistics import median
from unittest.mock import patch

from dashboard import charts, data

try:
    HAS_STREAMLIT = (importlib.util.find_spec("streamlit.testing.v1") is not None
                     and importlib.util.find_spec("matplotlib") is not None)
except ModuleNotFoundError:
    HAS_STREAMLIT = False

SCRIPT = '''
import streamlit as st
from datetime import timedelta
from dashboard import auth, app, pages
from test_auth import MemoryAccounts, account, NOW, session

if "_test_service" not in st.session_state:
    st.session_state["_test_service"] = auth.AuthService(
        MemoryAccounts(account(), account("admin", "admin")),
        lambda: NOW + timedelta(seconds=st.session_state.get("_test_clock_offset", 0)))
service = st.session_state["_test_service"]
route = st.session_state.get("_test_route", "login")
if route == "login":
    app.main(st, service)
else:
    if auth.SESSION_KEY not in st.session_state:
        st.session_state.update(session())
    render = next(render for _, path, render in pages.WORK_PAGES if path == route)
    if st.session_state.get("_test_analytics"):
        from unittest.mock import patch
        from test_streamlit_pages import SavedAnalytics
        reader = SavedAnalytics(st.session_state.get("_test_empty", False))
        def read(key, loader, **kwargs):
            return {"data": loader(reader), "source": {"note": "Тестовая аналитика"}}
        with patch("dashboard.data.read_page", side_effect=read), \
                patch("streamlit.download_button", wraps=st.download_button) as downloads:
            render(st, service, "demo")
        st.session_state["_test_downloads"] = [
            {key: call.kwargs[key] for key in ("data", "file_name", "mime")}
            for call in downloads.call_args_list]
    else:
        render(st, service, st.session_state.get("_test_mode", "demo"))
'''


class SavedAnalytics:
    """Минимальные сохраненные результаты для проверки UI; не ключ из генератора."""

    def __init__(self, empty=False):
        self.empty = empty

    def analysis_runs(self):
        from test_auth import NOW

        return [{"run_id": 7, "started_at": NOW, "finished_at": NOW,
                 "params": {"exam_type": "EGE", "subject_code": "02", "threshold": 2}}]

    def completed_run(self, run):
        assert run == 7
        return self.analysis_runs()[0]

    def school_list(self, run, exam=None, subject=None):
        from test_auth import NOW

        from dashboard.data import SCHOOL_COLUMNS

        assert run == 7
        row = dict.fromkeys(SCHOOL_COLUMNS, 0.5)
        row.update(run_id=7, calculated_at=NOW, exam_type="EGE", subject_code="02",
                   region_code="02", school_code="s001", participants=20, index=2.5,
                   in_list=True)
        return [] if self.empty or exam not in (None, "EGE") or subject not in (None, "02") \
            else [row]

    def p1_groups(self, run, exam, subject, school):
        assert (run, exam, subject, school) == (7, "EGE", "02", "s001")
        return [{"group_id": 1, "exam_type": exam, "subject_code": subject,
                 "school_code": school, "room_code": "r001", "variant_id": "v001",
                 "participant_ids": ["p_000001", "p_000002"], "size": 2,
                 "min_p_value": .001, "run_id": run}]

    def anomalies(self):
        from test_auth import NOW

        return [{"anomaly_id": 1, "rule": "A1", "exam_type": "EGE", "subject_code": "02",
                 "object_type": "school", "object_id": "s001", "region_code": "02",
                 "school_code": "s001", "value": 3., "threshold": 2., "detected_at": NOW}]

    def live(self):
        from test_auth import NOW

        from dashboard.data import EXAMS

        return [{"exam_type": exam, "in_progress": 5, "finished": 10, "annulled": 0,
                 "absent": 1, "generated_at": NOW, "last_successful_load_at": NOW,
                 "anomalies_by_rule": {"A1": 1, "A2": 0, "A3": 0}} for exam in EXAMS]

    def freshness(self):
        return []

    def school_counts(self, run):
        assert run == 7
        return [{"exam_type": "EGE", "schools": 1}]


@unittest.skipUnless(HAS_STREAMLIT, "Streamlit/matplotlib не установлены в окружении проверки")
class StreamlitPageTests(unittest.TestCase):
    def make_app(self, route):
        from streamlit.testing.v1 import AppTest

        app = AppTest.from_string(SCRIPT, default_timeout=30)
        app.session_state["_test_route"] = route
        return app

    def assert_no_exception(self, app):
        self.assertEqual([e.message for e in app.exception], [])

    def assert_distribution_cards_match_table(self, app):
        rows = app.dataframe[0].value.to_dict("records")
        scores = [int(row["primary_score"]) for row in rows
                  for _ in range(int(row["participants"]))]
        self.assertEqual([metric.label for metric in app.metric],
                         ["Участники", "Средний балл", "Медиана", "Мода"])
        self.assertEqual(int(app.metric[0].value), len(scores))
        self.assertAlmostEqual(float(app.metric[1].value.replace(",", ".")),
                               sum(scores) / len(scores), delta=0.005)
        self.assertEqual(float(app.metric[2].value.replace(",", ".")), median(scores))
        counts = Counter(scores)
        modes = sorted(score for score, count in counts.items() if count == max(counts.values()))
        if len(modes) == len(counts) and len(counts) > 1:
            self.assertEqual(app.metric[3].value, "Нет")
        else:
            expected = "; ".join(map(str, modes[:3])) + ("; …" if len(modes) > 3 else "")
            self.assertEqual(app.metric[3].value, expected)

    def test_all_eight_pages_render_in_real_streamlit_with_static_fixture(self):
        from dashboard.pages import WORK_PAGES

        for title, route, _ in WORK_PAGES:
            with self.subTest(route=route):
                app = self.make_app(route).run()
                self.assert_no_exception(app)
                self.assertEqual(app.title[0].value, title)
                self.assertIn("Синтетические", app.warning[0].value)

    def test_distribution_filters_change_the_rendered_participant_count(self):
        app = self.make_app("distribution").run()
        self.assert_no_exception(app)
        self.assertEqual(app.metric[0].value, "43")
        self.assert_distribution_cards_match_table(app)
        app.selectbox(key="distribution_region").select("77").run()
        self.assert_no_exception(app)
        self.assertLess(int(app.metric[0].value), 43)
        self.assert_distribution_cards_match_table(app)
        app.selectbox(key="distribution_exam").select("OGE").run()
        self.assert_no_exception(app)
        app.selectbox(key="distribution_region").select(None).run()
        self.assert_no_exception(app)
        self.assertEqual(app.metric[0].value, "45")
        self.assert_distribution_cards_match_table(app)

    def test_distribution_filters_and_metrics_have_their_own_column_rows(self):
        app = self.make_app("distribution").run()
        self.assert_no_exception(app)
        columns = app.get("column")
        self.assertEqual(len(columns), 7)
        self.assertEqual([column.selectbox[0].key for column in columns[:3]],
                         ["distribution_exam", "distribution_subject", "distribution_region"])
        self.assertEqual([len(column.metric) for column in columns[3:]], [1, 1, 1, 1])

    def test_empty_distribution_has_no_statistic_cards(self):
        original_read = data.read_page

        def read(key, loader, **kwargs):
            if key[0] == "distribution" and isinstance(key[1], tuple):
                return {"data": [], "source": {"note": "Тестовый пустой результат"}}
            return original_read(key, loader, **kwargs)

        with patch.object(data, "read_page", side_effect=read):
            app = self.make_app("distribution").run()
        self.assert_no_exception(app)
        self.assertEqual(len(app.metric), 0)
        self.assertIn("Нет окончательных результатов", app.info[0].value)

    def test_new_chart_views_preserve_tables_and_selected_filters(self):
        for route, key, value in (("distribution", "distribution_view", "Накопленная доля"),
                                  ("comparison", "comparison_view", "Столбцы"),
                                  ("regions", "regions_view", "Рейтинг"),
                                  ("topics", "topics_view", "Столбцы")):
            with self.subTest(route=route):
                app = self.make_app(route).run()
                self.assert_no_exception(app)
                before = [frame.value.copy() for frame in app.dataframe]
                metrics = [metric.value for metric in app.metric]
                app.radio(key=key).set_value(value).run()
                self.assert_no_exception(app)
                self.assertEqual([metric.value for metric in app.metric], metrics)
                self.assertEqual(len(app.dataframe), len(before))
                self.assertTrue(all(frame.value.equals(expected) for frame, expected
                                    in zip(app.dataframe, before, strict=True)))

    def test_many_new_regions_can_be_paged_without_truncating_the_table(self):
        rows = [{"region_code": f"new-{i:02}", "region_name": "Новый регион " + str(i),
                 "exam_type": "EGE", "mean_pct": i, "participants": 1} for i in range(43)]

        def read(key, loader, **kwargs):
            return {"data": rows, "source": {"note": "Новый тестовый набор"}}

        with patch.object(data, "read_page", side_effect=read), \
                patch.object(charts, "comparison_heatmap", wraps=charts.comparison_heatmap) as plot:
            app = self.make_app("comparison").run()
            self.assert_no_exception(app)
            self.assertEqual(len(plot.call_args.args[0]), 20)
            app.selectbox(key="comparison_chart_page").select(2).run()
            self.assert_no_exception(app)
            self.assertEqual(len(plot.call_args.args[0]), 3)
            self.assertEqual(len(app.dataframe[0].value), 43)

    def test_sparse_variant_pages_with_no_intersection_do_not_fail_in_bar_view(self):
        original_read = data.read_page
        rows = [{"exam_type": "EGE", "subject_code": "02", "task_number": task,
                 "variant_id": f"v{42 - task:02}", "participants": 1, "share_score": .5}
                for task in range(1, 42)]

        def read(key, loader, **kwargs):
            result = original_read(key, loader, **kwargs)
            if key[0] == "topics" and key[1] in data.EXAMS:
                result = {**result, "data": {**result["data"], "variants": rows}}
            return result

        with patch.object(data, "read_page", side_effect=read):
            app = self.make_app("topics").run()
            self.assert_no_exception(app)
            app.radio(key="topics_view").set_value("Столбцы").run()
            self.assert_no_exception(app)
            self.assertTrue(any("нет пересекающихся" in item.value for item in app.info))

    def test_login_reaches_protected_home_without_bypassing_form(self):
        from test_auth import TEST_PASSWORD

        app = self.make_app("login").run()
        self.assert_no_exception(app)
        app.text_input(key="_sma_login_name").input("staff")
        app.text_input(key="_sma_login_password").input(TEST_PASSWORD)
        app.button[0].click().run()
        self.assert_no_exception(app)
        self.assertEqual(app.title[0].value, "СМА")
        from dashboard.auth import SESSION_KEY

        self.assertEqual(app.session_state[SESSION_KEY].role, "staff")
        self.assertFalse(any(t.key == "_sma_new_login" for t in app.text_input))

    def test_live_rerun_does_not_extend_session_activity(self):
        from dashboard.auth import SESSION_KEY

        app = self.make_app("live").run()
        self.assert_no_exception(app)
        before = app.session_state[SESSION_KEY].last_activity
        app.session_state["_test_clock_offset"] = 60
        app.run()
        self.assert_no_exception(app)
        self.assertEqual(app.session_state[SESSION_KEY].last_activity, before)

    def test_database_failure_stays_database_failure(self):
        app = self.make_app("distribution")
        app.session_state["_test_mode"] = "database"
        app.run()
        self.assert_no_exception(app)
        self.assertFalse(any("Синтетические" in w.value for w in app.warning))
        self.assertTrue(app.warning)
        self.assertEqual(len(app.metric), 0)

    def test_saved_anomaly_displays_all_eleven_fields(self):
        from dashboard.data import ANOMALY_COLUMNS

        app = self.make_app("anomalies")
        app.session_state["_test_analytics"] = True
        app.run()
        self.assert_no_exception(app)
        self.assertEqual(list(app.dataframe[0].value.columns), list(ANOMALY_COLUMNS))

    def test_completed_run_school_and_p1_members_render_together(self):
        from dashboard.data import GROUP_COLUMNS, SCHOOL_COLUMNS

        app = self.make_app("analysis")
        app.session_state["_test_analytics"] = True
        app.run()
        app.selectbox(key="analysis_run").select(7).run()
        self.assert_no_exception(app)
        self.assertEqual(len(app.dataframe), 2)
        self.assertEqual(list(app.dataframe[0].value.columns), list(SCHOOL_COLUMNS))
        self.assertEqual(list(app.dataframe[1].value.columns), list(GROUP_COLUMNS))
        self.assertEqual(app.dataframe[1].value.iloc[0]["size"], 2)
        self.assertEqual(app.dataframe[1].value.iloc[0]["run_id"], 7)
        app.session_state["_test_empty"] = True
        app.run()
        self.assert_no_exception(app)
        self.assertTrue(any("Перечень школ" in message.value for message in app.info))

    def test_live_school_count_does_not_claim_zero_for_another_platform_scope(self):
        app = self.make_app("live")
        app.session_state["_test_analytics"] = True
        app.run()
        app.selectbox(key="live_run").select(7).run()
        self.assert_no_exception(app)
        counts = [m.value for m in app.metric if m.label == "Школы в перечне"]
        self.assertEqual(counts, ["1", "—", "—", "—"])

    def test_anomaly_download_button_carries_the_displayed_csv(self):
        import csv
        import io

        from dashboard.data import ANOMALY_COLUMNS

        app = self.make_app("anomalies")
        app.session_state["_test_analytics"] = True
        app.run()
        self.assert_no_exception(app)
        self.assertEqual(len(app.get("download_button")), 1)
        download, = app.session_state["_test_downloads"]
        rows = list(csv.reader(io.StringIO(download["data"].decode("utf-8")), delimiter=";"))
        self.assertEqual(rows[0], list(ANOMALY_COLUMNS))
        self.assertEqual(rows[1][0], str(app.dataframe[0].value.iloc[0]["anomaly_id"]))
        self.assertEqual(download["file_name"], "demo_anomalies.csv")

    def test_school_csv_follows_run_and_filters_including_empty_selection(self):
        import csv
        import io

        from dashboard.data import SCHOOL_COLUMNS

        app = self.make_app("analysis")
        app.session_state["_test_analytics"] = True
        app.run()
        self.assert_no_exception(app)
        self.assertEqual(app.session_state["_test_downloads"], [])
        app.selectbox(key="analysis_run").select(7).run()
        app.selectbox(key="analysis_exam").select("EGE").run()
        app.selectbox(key="analysis_subject").select("02").run()
        self.assert_no_exception(app)
        download, = app.session_state["_test_downloads"]
        rows = list(csv.reader(io.StringIO(download["data"].decode("utf-8")), delimiter=";"))
        self.assertEqual(rows[0], list(SCHOOL_COLUMNS))
        self.assertEqual(rows[1][0], "7")
        self.assertEqual(rows[1][3], "02")
        self.assertEqual(download["file_name"], "demo_schools_run_7_exam_EGE_subject_02.csv")
        app.selectbox(key="analysis_exam").select("OGE").run()
        self.assert_no_exception(app)
        self.assertEqual(len(app.get("download_button")), 1)
        download, = app.session_state["_test_downloads"]
        rows = list(csv.reader(io.StringIO(download["data"].decode("utf-8")), delimiter=";"))
        self.assertEqual(rows, [list(SCHOOL_COLUMNS)])
        self.assertEqual(download["file_name"], "demo_schools_run_7_exam_OGE_subject_all.csv")


if __name__ == "__main__":
    unittest.main()
