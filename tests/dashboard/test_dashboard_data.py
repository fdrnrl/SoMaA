"""Проверки слоя чтения без внешних пакетов и подключения к PostgreSQL."""

import json
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dashboard import data
from dashboard.demo import DemoData

FIXTURES = Path(__file__).parent / "fixtures"
SOURCES = {
    "anomalies": "mart.unit_anomalies", "runs": "mart.unit_runs",
    "schools": "mart.unit_schools", "p1_groups": "mart.unit_groups",
}
RUN = {"run_id": 7, "started_at": None,
       "finished_at": datetime.fromisoformat("2026-12-05T17:00:00+03:00"), "params": {}}


class Cursor:
    def __init__(self, rows):
        names = list(rows[0]) if rows else []
        self.description = [SimpleNamespace(name=name) for name in names]
        self.rows = [tuple(row[name] for name in names) for row in rows]

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, *responses, error=None):
        self.responses = list(responses)
        self.queries = []
        self.error = error
        self.closed = False

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if self.error:
            raise self.error
        if sql.startswith("SET TRANSACTION"):
            return Cursor([])
        return Cursor(self.responses.pop(0) if self.responses else [])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class QueryTests(unittest.TestCase):
    def test_deployed_synthetic_database_has_explicit_provenance(self):
        with patch.dict("os.environ", {"SMA_DASHBOARD_SYNTHETIC": "true"}):
            metadata = data.MartData(Connection()).metadata
            self.assertIs(metadata["synthetic"], True)
            self.assertIn("синтетические", metadata["note"])

    def test_database_cache_separates_provenance_configuration(self):
        factory = Mock(side_effect=[Connection(), Connection()])
        cache = data.PageCache()
        for value in ("true", "false"):
            with patch.dict("os.environ", {"SMA_DASHBOARD_SYNTHETIC": value}):
                data.read_page("live-provenance", lambda r: r.live(),
                               connection_factory=factory, cache=cache)
        self.assertEqual(factory.call_count, 2)

    def test_distribution_uses_parameters_and_only_final_results(self):
        connection = Connection([])
        data.MartData(connection).distribution("OGE", "02", "77")
        sql, params = connection.queries[0]
        self.assertEqual(params, ("OGE", "02", "77"))
        self.assertNotIn("'77'", sql)
        self.assertIn("status = 'finished'", sql)
        self.assertIn("result_status IN ('final', 'appeal_changed')", sql)
        self.assertIn("GROUP BY primary_score", sql)

    def test_region_selection_supports_several_exams_and_empty_selection(self):
        connection = Connection([])
        reader = data.MartData(connection)
        reader.region_results(("EGE", "OGE"), "02")
        self.assertEqual(connection.queries[0][1], ("02", ["EGE", "OGE"]))
        self.assertEqual(reader.region_results([]), [])
        self.assertEqual(len(connection.queries), 1)

    def test_filter_codes_keep_leading_zero(self):
        connection = Connection([{"exam_type": "VPR", "subject_code": "01",
                                  "region_code": "02", "region_name": "Region"}])
        options = data.MartData(connection).filters("VPR", "01")
        self.assertEqual(options["subject_codes"], ["01"])
        self.assertEqual(options["regions"][0]["region_code"], "02")

    def test_topics_and_variants_use_their_marts(self):
        connection = Connection([], [])
        reader = data.MartData(connection)
        reader.topics("OGE", "02")
        reader.variants("OGE", "02")
        self.assertIn("mart.kim_stats", connection.queries[0][0])
        self.assertIn("mart.variant_comparison", connection.queries[1][0])
        self.assertTrue(all(params == ("OGE", "02") for _, params in connection.queries))

    def test_live_and_freshness_do_not_invent_missing_rows(self):
        connection = Connection([], [])
        reader = data.MartData(connection)
        self.assertEqual(reader.live(), [])
        self.assertEqual(reader.freshness(), [])
        self.assertIn("generated_at", connection.queries[0][0])
        self.assertIn("last_error_at", connection.queries[1][0])

    def test_unconfigured_analytics_is_not_an_empty_result(self):
        with self.assertRaises(data.DataUnavailable) as caught:
            data.MartData(Connection(), {}).anomalies()
        self.assertEqual(caught.exception.code, "source_not_configured")

    def test_analytics_identifier_cannot_contain_sql(self):
        connection = Connection()
        with self.assertRaises(data.DataUnavailable) as caught:
            data.MartData(connection, {"anomalies": "mart.x; DROP TABLE app.users"}).anomalies()
        self.assertEqual(caught.exception.code, "invalid_source")
        self.assertEqual(connection.queries, [])

    def test_anomalies_have_complete_fields_and_stable_sort(self):
        connection = Connection([])
        data.MartData(connection, SOURCES).anomalies("OGE", "02")
        sql, params = connection.queries[0]
        for column in ("anomaly_id", "object_type", "threshold", "detected_at"):
            self.assertIn(column, sql)
        self.assertEqual(len(data.ANOMALY_COLUMNS), 11)
        self.assertIn("detected_at DESC, anomaly_id DESC", sql)
        self.assertEqual(params, ("OGE", "02"))

    def test_completed_empty_analysis_differs_from_missing_analysis(self):
        reader = data.MartData(Connection([RUN], []), SOURCES)
        self.assertEqual(reader.school_list(7, "OGE", "02"), [])
        with self.assertRaises(data.DataUnavailable) as caught:
            data.MartData(Connection([]), SOURCES).school_list(7, "OGE", "02")
        self.assertEqual(caught.exception.code, "analysis_missing")

    def test_school_and_groups_are_bound_to_the_selected_run(self):
        connection = Connection([RUN], [], [RUN], [])
        reader = data.MartData(connection, SOURCES)
        reader.school_list(7, "OGE", "02")
        reader.p1_groups(7, "OGE", "02", "77001")
        self.assertEqual(connection.queries[1][1], (7, "OGE", "02"))
        self.assertEqual(connection.queries[3][1], (7, "OGE", "02", "77001"))
        self.assertIn("calculated_at", connection.queries[1][0])
        self.assertIn('ORDER BY "index" DESC', connection.queries[1][0])
        self.assertEqual(len(data.SCHOOL_COLUMNS), 19)

    def test_school_count_counts_schools_not_subject_rows(self):
        connection = Connection([RUN], [])
        data.MartData(connection, SOURCES).school_counts(7)
        self.assertIn("count(DISTINCT school_code)", connection.queries[1][0])

    def test_query_errors_do_not_expose_connection_details(self):
        with self.assertRaises(data.DataUnavailable) as caught:
            data.MartData(Connection(error=RuntimeError("PRIVATE_DATABASE_DETAIL"))).live()
        self.assertNotIn("PRIVATE_DATABASE_DETAIL", str(caught.exception))

    def test_reader_connection_never_uses_owner_credentials(self):
        settings = Mock(return_value={"user": "sma_reader", "password": ""})
        connect = Mock()
        with patch.dict("sys.modules", {
            "psycopg": SimpleNamespace(connect=connect),
            "sma.db": SimpleNamespace(connection_settings=settings),
        }), patch.dict("os.environ", {"SMA_READER_PASSWORD": "unit-only"}):
            data._connect_reader()
        settings.assert_called_once_with(user="sma_reader", password="")
        self.assertEqual(connect.call_args.kwargs["user"], "sma_reader")
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 3)

    def test_snapshot_uses_one_read_only_repeatable_read_connection(self):
        connection = Connection([], [])
        factory = Mock(return_value=connection)
        result = data.read_page(
            "two-tables", lambda reader: {"live": reader.live(), "fresh": reader.freshness()},
            connection_factory=factory, cache=data.PageCache(),
        )
        factory.assert_called_once()
        self.assertEqual(len(connection.queries), 3)
        self.assertIn("REPEATABLE READ, READ ONLY", connection.queries[0][0])
        self.assertTrue(connection.closed)
        self.assertEqual(result["source"]["mode"], "database")

    def test_database_failure_does_not_fall_back_to_demo(self):
        factory = Mock(side_effect=RuntimeError("PRIVATE_DATABASE_DETAIL"))
        with self.assertRaises(data.DataUnavailable) as caught:
            data.read_page("failure", lambda reader: reader.live(),
                           connection_factory=factory, cache=data.PageCache())
        self.assertNotIn("PRIVATE_DATABASE_DETAIL", str(caught.exception))

    def test_invalid_connection_settings_are_not_exposed(self):
        factory = Mock(side_effect=ValueError("PRIVATE_DATABASE_DETAIL"))
        with self.assertRaises(data.DataUnavailable) as caught:
            data.read_page("bad-settings", lambda reader: reader.live(),
                           connection_factory=factory, cache=data.PageCache())
        self.assertNotIn("PRIVATE_DATABASE_DETAIL", str(caught.exception))

    def test_cache_is_invalidated_when_a_source_setting_changes(self):
        first, second = Connection([]), Connection([])
        factory = Mock(side_effect=[first, second])
        cache = data.PageCache()
        for source in ("mart.first", "mart.second"):
            with patch.dict("os.environ", {"SMA_DASHBOARD_ANOMALIES_VIEW": source}):
                data.read_page("anomalies", lambda reader: reader.anomalies(),
                               connection_factory=factory, cache=cache)
        self.assertEqual(factory.call_count, 2)
        self.assertIn("mart.first", first.queries[1][0])
        self.assertIn("mart.second", second.queries[1][0])


class ValueAndCacheTests(unittest.TestCase):
    def test_numbers_null_codes_and_time(self):
        row = data.display_value({"code": "02", "empty": None, "zero": Decimal(0),
                                  "time": datetime.fromisoformat("2026-12-05T14:00:00+07:00")})
        self.assertEqual(row["code"], "02")
        self.assertIsNone(row["empty"])
        self.assertEqual(row["zero"], 0.0)
        self.assertEqual(row["time"].hour, 10)
        self.assertEqual(row["time"].utcoffset(), timedelta(hours=3))
        with self.assertRaises(ValueError):
            data.display_value(datetime.fromisoformat("2026-12-05T10:00:00"))

    def test_cache_expires_at_thirty_seconds_and_isolates_mutations(self):
        now = [0]
        cache = data.PageCache(clock=lambda: now[0])
        loader = Mock(return_value={"rows": [1]})
        cache.load("page", loader)["rows"].append(2)
        now[0] = 29
        self.assertEqual(cache.load("page", loader), {"rows": [1]})
        self.assertEqual(loader.call_count, 1)
        now[0] = 30
        cache.load("page", loader)
        self.assertEqual(loader.call_count, 2)
        with self.assertRaises(ValueError):
            data.PageCache(ttl=31)

    def test_failed_reads_are_not_cached(self):
        cache = data.PageCache()
        loader = Mock(side_effect=[data.DataUnavailable("Unavailable"), [1]])
        with self.assertRaises(data.DataUnavailable):
            cache.load("page", loader)
        self.assertEqual(cache.load("page", loader), [1])


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.reader = DemoData(FIXTURES / "small.zip")

    def test_filters_and_distribution_use_real_selected_records(self):
        self.assertEqual(self.reader.filters("OGE")["subject_codes"], ["02"])
        all_rows = self.reader.distribution("OGE", "02")
        region_rows = self.reader.distribution("OGE", "02", "77")
        self.assertEqual(sum(row["participants"] for row in all_rows), 45)
        self.assertLess(sum(row["participants"] for row in region_rows), 45)
        self.assertEqual(self.reader.distribution("OGE", "01"), [])

    def test_distribution_excludes_pending_and_annulled(self):
        rows = self.reader._final(self.reader._sessions("OGE", "02"))
        rows[0]["result_status"] = "pending"
        rows[1]["status"] = "annulled"
        self.assertEqual(sum(r["participants"] for r in self.reader.distribution("OGE", "02")), 43)

    def test_regional_results_match_the_prepared_reference(self):
        reference = json.loads((FIXTURES / "small_summary.json").read_text(encoding="utf-8"))
        expected = {(r["exam_type"], r["subject_code"], r["region_code"]): r
                    for r in reference["region_results"]}
        actual = self.reader.region_results()
        self.assertEqual(len(actual), len(expected))
        for row in actual:
            target = expected[(row["exam_type"], row["subject_code"], row["region_code"])]
            self.assertEqual(row["participants"], target["participants"])
            self.assertAlmostEqual(row["mean_score"], float(target["mean_score"]), places=2)
            self.assertAlmostEqual(row["mean_pct"], float(target["mean_pct"]), places=2)
            if target["share_grade2"] is None:
                self.assertIsNone(row["share_grade2"])
            else:
                self.assertAlmostEqual(row["share_grade2"], float(target["share_grade2"]), places=4)

    def test_demo_has_no_fictitious_load_times_or_saved_analysis(self):
        self.assertTrue(self.reader.metadata["synthetic"])
        self.assertEqual(self.reader.metadata["mode"], "demo")
        self.assertEqual(len(self.reader.live()), 4)
        self.assertTrue(all(row["last_successful_load_at"] is None for row in self.reader.live()))
        self.assertTrue(all(row["anomalies_by_rule"] is None for row in self.reader.live()))
        self.assertEqual(self.reader.freshness(), [])
        with self.assertRaises(data.DataUnavailable):
            self.reader.anomalies()

    def test_topics_and_variants_include_both_parts_and_preserve_units(self):
        topics = self.reader.topics("OGE", "02")
        variants = self.reader.variants("OGE", "02")
        self.assertTrue(topics)
        self.assertTrue(all(0 <= row["mean_share_score"] <= 1 for row in topics))
        self.assertTrue(all(0 <= row["share_score"] <= 1 for row in variants))
        self.assertTrue(any(row["task_number"] == 25 for row in variants))
        self.assertTrue(all(row["subject_code"] == "02" for row in topics))

    def test_topics_do_not_choose_a_pending_policy_silently(self):
        self.reader._final(self.reader._sessions("OGE", "02"))[0]["result_status"] = "pending"
        with self.assertRaises(data.DataUnavailable) as caught:
            self.reader.topics("OGE", "02")
        self.assertEqual(caught.exception.code, "policy_not_agreed")

    def test_cached_page_returns_source_metadata_with_the_data(self):
        result = data.read_page(
            ("distribution", "OGE", "02"), lambda reader: reader.distribution("OGE", "02"),
            mode="demo", cache=data.PageCache(),
        )
        self.assertEqual(sum(row["participants"] for row in result["data"]), 45)
        self.assertTrue(result["source"]["synthetic"])


if __name__ == "__main__":
    unittest.main()
