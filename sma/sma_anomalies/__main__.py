"""Запуск:  python -m sma_anomalies <папка набора> <папка результата>
Пример:   python -m sma_anomalies Тестовые_данные_СМА_v2/medium out/
"""
import sys

from .analysis import run_dataset


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__)
        return 2
    res = run_dataset(argv[0], argv[1])
    a, s, g = res["anomalies"], res["schools"], res["groups"]
    print(f"Аномалий А1–А3: {len(a)} | школ с индексом: {len(s)} | в перечне: {int(s['in_list'].sum()) if len(s) else 0} | групп П1: {len(g)}")
    print(f"Файлы записаны в {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
