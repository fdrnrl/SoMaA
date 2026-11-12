"""Локальные данные входа не попадают в копию для развертывания."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dashboard.deploy.prepare_runtime import prepare


class RuntimeCopyTests(unittest.TestCase):
    def test_local_credentials_are_excluded_from_runtime_and_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            source, target = home / "source", home / "runtime"
            source.mkdir()
            for name in ("README.md", "Makefile", "Dockerfile", "docker-compose.yml",
                         "requirements.txt", "pyproject.toml", ".env.example",
                         ".dockerignore", ".gitattributes", ".gitignore"):
                (source / name).write_text("", encoding="utf-8")
            private = source / "dashboard/.local"
            private.mkdir(parents=True)
            (private / "credentials.txt").write_text("private-test-data", encoding="utf-8")
            public = source / "dashboard/app.py"
            public.write_text("public-test-data", encoding="utf-8")
            with patch.object(Path, "home", return_value=home), redirect_stdout(io.StringIO()):
                prepare(source, target)
            self.assertEqual((target / "dashboard/app.py").read_text(), "public-test-data")
            self.assertFalse((target / "dashboard/.local").exists())
            manifest = json.loads((target / ".somaa-local-runtime").read_text())
            self.assertFalse(any(".local" in Path(name).parts for name in manifest["files"]))


if __name__ == "__main__":
    unittest.main()
