"""Контроль подготовки T-23; реальный замер ожидает стенд, а не подставляется демо."""

import json
import tempfile
import unittest
from pathlib import Path

from measure_pages import p95, validate_manifest

from dashboard.data import EXAMS


class AcceptancePreparationTests(unittest.TestCase):
    def test_p95_uses_ninety_fifth_observation_out_of_one_hundred(self):
        self.assertEqual(p95(list(range(100, 0, -1))), 95)
        self.assertEqual(p95([3.] * 95 + [4.] * 5), 3.)
        self.assertEqual(p95([3.] * 94 + [4.] * 6), 4.)
        with self.assertRaises(ValueError):
            p95([])

    def test_large_manifest_requires_four_platforms_of_four_hundred_participants(self):
        manifest = {"synthetic": True, "platforms": {e: {"participants": 400} for e in EXAMS}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(len(validate_manifest(path)), 64)
            manifest["platforms"]["EGE"]["participants"] = 40
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_manifest(path)


if __name__ == "__main__":
    unittest.main()
