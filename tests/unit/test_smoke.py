"""Проверка каркаса M0: пакет sma импортируется."""

import sma


def test_package_imports():
    assert sma.__version__ == "0.1.0"
