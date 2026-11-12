"""Запуск всех тестов одной командой, если pytest не установлен:  python tests/run_tests.py
С pytest работает обычная команда:  pytest -q   (тесты — обычные функции test_*)."""
import importlib
import inspect
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    mods = sorted(f[:-3] for f in os.listdir(here) if f.startswith("test_") and f.endswith(".py"))
    ok = fail = skip = 0
    for m in mods:
        mod = importlib.import_module(f"tests.{m}")
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            t = time.time()
            try:
                fn()
                ok += 1
                print(f"PASS  {m}::{name}  ({time.time() - t:.1f} с)")
            except Exception as e:              # noqa: BLE001
                if type(e).__name__ == "Skip":
                    skip += 1
                    print(f"SKIP  {m}::{name}  {e}")
                else:
                    fail += 1
                    print(f"FAIL  {m}::{name}")
                    traceback.print_exc()
    print(f"\nпройдено {ok}, упало {fail}, пропущено {skip}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
