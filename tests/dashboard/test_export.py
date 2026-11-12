"""Формат CSV и соответствие выгрузки показанной выборке без настоящей БД."""

import copy
import csv
import io
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock, patch

from test_auth import StopPage
from test_streamlit_pages import SavedAnalytics

from dashboard import auth, data, export, pages, ui


def read_csv(content):
    return list(csv.reader(io.StringIO(content.decode("utf-8"), newline=""), delimiter=";"))


class CsvFormatTests(unittest.TestCase):
    def test_empty_exports_have_exact_headers_without_bom(self):
        for serialize, columns in ((export.anomalies_csv, data.ANOMALY_COLUMNS),
                                   (export.schools_csv, data.SCHOOL_COLUMNS)):
            with self.subTest(export=serialize.__name__):
                content = serialize([])
                self.assertFalse(content.startswith(b"\xef\xbb\xbf"))
                self.assertEqual(read_csv(content), [list(columns)])
                self.assertTrue(content.endswith(b"\r\n"))

    def test_anomaly_round_trip_preserves_codes_unicode_quotes_and_newlines(self):
        row = SavedAnalytics().anomalies()[0]
        row.update(object_id='школа; "тест"\r\nстрока', subject_code="02", region_code="02",
                   school_code="00003", value=Decimal("21.4000"), threshold=17.9,
                   detected_at=datetime(2026, 12, 5, 8, 2, tzinfo=UTC), ignored="extra")
        content = export.anomalies_csv([row])
        header, values = read_csv(content)
        result = dict(zip(header, values, strict=True))
        self.assertEqual(header, list(data.ANOMALY_COLUMNS))
        self.assertEqual(len(values), 11)
        self.assertEqual(result["object_id"], row["object_id"])
        self.assertEqual(result["subject_code"], "02")
        self.assertEqual(result["region_code"], "02")
        self.assertEqual(result["school_code"], "00003")
        self.assertEqual(result["value"], "21,4000")
        self.assertEqual(result["threshold"], "17,9")
        self.assertEqual(result["detected_at"], "2026-12-05T11:02:00+03:00")

    def test_school_export_preserves_run_time_corrected_z_and_source_precision(self):
        row = SavedAnalytics().school_list(7)[0]
        row.update(calculated_at="2026-12-05T13:10:00+03:00", p1=1.2e-12,
                   z1=Decimal("3.1250"), z2=0., index=Decimal("2.10"), in_list=True)
        before = copy.deepcopy(row)
        header, values = read_csv(export.schools_csv([row]))
        result = dict(zip(header, values, strict=True))
        self.assertEqual(header, list(data.SCHOOL_COLUMNS))
        self.assertEqual(len(values), 19)
        self.assertEqual(result["run_id"], "7")
        self.assertEqual(result["calculated_at"], row["calculated_at"])
        self.assertEqual(result["p1"], "0,0000000000012")
        self.assertEqual(result["z1"], "3,1250")
        self.assertEqual(result["z2"], "0,0")
        self.assertEqual(result["index"], "2,10")
        self.assertEqual(result["in_list"], "true")
        self.assertEqual(row, before)

    def test_null_zero_boolean_lists_and_large_ids_are_distinct(self):
        row = {"missing": None, "zero": 0, "flag": False, "ids": ["p_000001", "p_000002"],
               "negative": -.125, "id": 99999999999999999999}
        self.assertEqual(read_csv(export.csv_bytes([row], tuple(row)))[1],
                         ["", "0", "false", "p_000001,p_000002", "-0,125",
                          "99999999999999999999"])

    def test_naive_invalid_and_non_datetime_timestamps_are_rejected(self):
        row = SavedAnalytics().anomalies()[0]
        naive = datetime.fromisoformat("2026-12-05T00:00:00")
        for value in (naive, "2026-12-05T11:02:00", "bad", 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                export.anomalies_csv([dict(row, detected_at=value)])

    def test_non_finite_numbers_and_missing_fields_cannot_create_partial_exports(self):
        row = SavedAnalytics().anomalies()[0]
        for value in (float("nan"), float("inf"), Decimal("-Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                export.anomalies_csv([row, dict(row, value=value)])
        with self.assertRaises(ValueError):
            export.anomalies_csv([{}])
        with self.assertRaises(TypeError):
            export.csv_bytes([{"a": object()}], ("a",))

    def test_iterator_order_and_quoted_fields_round_trip(self):
        rows = [{"code": ' 02;"quoted"\nline '}, {"code": "01"}]
        self.assertEqual(read_csv(export.csv_bytes(iter(rows), ("code",))),
                         [["code"], [rows[0]["code"]], ["01"]])


class CsvPageTests(unittest.TestCase):
    def make_page(self, empty=False):
        st, service = Mock(), Mock()
        st.session_state = {}
        reader = SavedAnalytics(empty)

        def read(st, mode, key, loader, **kwargs):
            return loader(reader)

        return st, service, reader, read

    def test_anomaly_button_exports_displayed_rows_and_click_checks_activity(self):
        st, service, reader, read = self.make_page()
        with patch.object(pages, "_read", side_effect=read):
            pages.anomalies_page(st, service, "demo")
        args = st.download_button.call_args.kwargs
        self.assertEqual(args["data"], export.anomalies_csv(st.dataframe.call_args.args[0]))
        self.assertEqual(args["file_name"], "demo_anomalies.csv")
        self.assertEqual(args["mime"], "text/csv; charset=utf-8")
        self.assertEqual(args["on_click"], ui.user_activity)
        args["on_click"](**args["kwargs"])
        service.touch.assert_called_once_with(st.session_state)

    def test_school_button_exports_filter_run_and_empty_completed_list(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                st, service, reader, read = self.make_page(empty)
                choices = [7, "EGE", "02"] + ([] if empty else [("EGE", "02", "s001")])
                with patch.object(pages, "_read", side_effect=read), \
                        patch.object(pages, "_choice", side_effect=choices):
                    pages.analysis_page(st, service, "database")
                args = st.download_button.call_args.kwargs
                self.assertEqual(args["file_name"], "schools_run_7_exam_EGE_subject_02.csv")
                rows = [] if empty else st.dataframe.call_args_list[0].args[0]
                self.assertEqual(args["data"], export.schools_csv(rows))

    def test_no_download_before_login_or_when_source_or_run_is_missing(self):
        denied = Mock()
        denied.require_user.side_effect = auth.AccessDenied("Нет доступа")
        for render in (pages.anomalies_page, pages.analysis_page):
            st = Mock()
            st.session_state = {}
            st.stop.side_effect = StopPage
            with self.subTest(page=render.__name__), self.assertRaises(StopPage):
                render(st, denied, "demo")
            st.download_button.assert_not_called()
        st, service, reader, read = self.make_page()
        with patch.object(pages, "_read", return_value=None):
            pages.anomalies_page(st, service, "database")
            pages.analysis_page(st, service, "database")
        with patch.object(pages, "_read", return_value={"rows": [], "error": None}):
            pages.analysis_page(st, service, "demo")
        st.download_button.assert_not_called()

    def test_invalid_export_data_shows_error_without_download(self):
        st, service = Mock(), Mock()
        pages._download(st, service, "database", [{}], export.anomalies_csv,
                        "anomalies.csv", "anomalies_csv")
        st.download_button.assert_not_called()
        st.error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
