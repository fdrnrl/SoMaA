"""Копия текущего рабочего дерева в Linux-диск WSL, без изменения общих исходников."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path


def prepare(source, target):
    source, target = source.resolve(), target.resolve()
    if not str(target).startswith(str(Path.home()) + "/"):
        raise ValueError("Runtime must be inside the Linux user home")
    marker = target / ".somaa-local-runtime"
    if target.exists() and any(target.iterdir()) and not marker.exists():
        raise ValueError("Target contains unrelated files")
    target.mkdir(parents=True, exist_ok=True)
    hashes = {}
    roots = ("dashboard", "sma", "db", "config", "docs", "tests")
    top = ("README.md", "Makefile", "Dockerfile", "docker-compose.yml", "requirements.txt",
           "pyproject.toml", ".env.example", ".dockerignore", ".gitattributes", ".gitignore")
    files = [source / name for name in top]
    files += [p for name in roots for p in (source / name).rglob("*") if p.is_file()]
    for path in files:
        relative = path.relative_to(source)
        if any(part in ("__pycache__", ".pytest_cache", "artifacts", ".venv", ".local")
               for part in relative.parts):
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    environment = target / ".env"
    if not environment.exists():
        content = (target / ".env.example").read_text(encoding="utf-8")
        for name in ("POSTGRES_PASSWORD", "SMA_LOADER_PASSWORD", "SMA_READER_PASSWORD",
                     "SMA_APP_PASSWORD", "MOCK_API_TOKEN", "MOCK_ADMIN_TOKEN"):
            content = content.replace(name + "=\n", name + "=" + secrets.token_hex(24) + "\n")
        environment.write_text(content, encoding="utf-8")
        os.chmod(environment, 0o600)
    marker.write_text(json.dumps({"source": str(source), "files": hashes}, indent=2),
                      encoding="utf-8")
    print(json.dumps({"runtime": str(target), "copied_files": len(hashes),
                      "environment_created": environment.exists()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare(args.source, args.target)
