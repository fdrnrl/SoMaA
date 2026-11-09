"""Запуск проверок качества и отчет (раздел 13).

    python -m sma.quality.report            проверить данные и записать отчет

Результат: ops.quality_runs и ops.quality_issues, файлы
reports/quality_YYYYMMDD_HHMM.md и .json. Загрузчик запускает проверки сам раз в 10 минут.
"""

import argparse
import json
import logging
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from psycopg import Connection
from psycopg.types.json import Jsonb

from sma.db import connect
from sma.quality.checks import CHECKS, Issue, run_checks
from sma.timeutil import utc_now

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("reports")
GROUPS = {
    "Q-COMPL": "Полнота",
    "Q-UNIQ": "Уникальность",
    "Q-CONS": "Согласованность",
    "Q-TIME": "Своевременность",
    "Q-ACC": "Точность",
    "Q-MART": "Консистентность витрин",
    "Q-MISS": "Пропуски",
    "Q-OUT": "Выбросы",
}
LIMITATIONS = """\
- Проверки показывают нарушения правил, а не их причины: решение о том, ошибка ли это
  платформы, принимает человек.
- Q-TIME-01 проверяется только во время экзамена (есть сессии in_progress).
- Q-ACC-01 сравнивает с исходной записью 20 записей каждой таблицы, а не все.
- Q-MISS-01 и Q-OUT-01 только отмечают подозрительное; данные не удаляются.
"""


def save_run(conn: Connection, started: datetime, issues: list[Issue]) -> int:
    """Записывает запуск и нарушения в ops.quality_runs и ops.quality_issues."""
    summary = {
        "by_severity": dict(Counter(issue.severity for issue in issues)),
        "by_check": dict(Counter(issue.check_code for issue in issues)),
    }
    run_id = conn.execute(
        "INSERT INTO ops.quality_runs (started_at, finished_at, issues_total, summary) "
        "VALUES (%s, now(), %s, %s) RETURNING id",
        (started, len(issues), Jsonb(summary)),
    ).fetchone()[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ops.quality_issues "
            "(run_id, check_code, severity, exam_type, entity, entity_key, details) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (run_id, i.check_code, i.severity, i.exam_type, i.entity, i.entity_key,
                 Jsonb(i.details))
                for i in issues
            ],
        )  # fmt: skip
    return run_id


def describe_sources(conn: Connection, config_path: Path | None) -> tuple[list[str], bool]:
    """Платформы из журнала загрузок и признак «данные синтетические» (файлы генератора
    или тестовый API).
    """
    rows = conn.execute(
        "SELECT platform, max(finished_at), sum(saved) FROM ops.load_log "
        "WHERE status IN ('ok', 'partial') GROUP BY platform ORDER BY platform"
    ).fetchall()
    lines = [f"{p}: последняя загрузка {last:%Y-%m-%d %H:%M}, записей {n}" for p, last, n in rows]
    synthetic = True
    if config_path is not None and config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        synthetic = all(
            p["source"] == "file" or "mock" in str(p.get("base_url"))
            for p in config["platforms"]
        )  # fmt: skip
    return lines, synthetic


def markdown(run_id: int, moment: datetime, issues: list[Issue], sources, synthetic) -> str:
    """Отчет для людей: дата, источники, сводка по метрикам, нарушения, ограничения."""
    lines = [
        f"# Отчет о качестве данных СМА № {run_id}",
        "",
        f"Дата: {moment:%Y-%m-%d %H:%M} UTC",
        "",
    ]
    if synthetic:
        lines += ["**Данные синтетические** (генератор или тестовый API): выводы ограничены "
                  "свойствами генератора.", ""]  # fmt: skip
    lines += ["## Источники", ""] + [f"- {line}" for line in sources or ["нет загрузок"]] + [""]
    lines += [
        "## Сводка",
        "",
        "| Метрика | Проверки | error | warning | info |",
        "|---|---|---|---|---|",
    ]
    for prefix, title in GROUPS.items():
        codes = [code for code, _ in CHECKS if code.startswith(prefix)]
        found = [i for i in issues if i.check_code.startswith(prefix)]
        counts = Counter(i.severity for i in found)
        lines.append(f"| {title} | {', '.join(codes)} | {counts['error']} | "
                     f"{counts['warning']} | {counts['info']} |")  # fmt: skip
    lines += ["", "## Нарушения", ""]
    if not issues:
        lines.append("Нарушений нет.")
    for issue in sorted(issues, key=lambda i: ("ewi".index(i.severity[0]), i.check_code)):
        examples = ", ".join(issue.details.get("examples", [])[:5])
        count = issue.details.get("count", 1)
        lines.append(
            f"- **{issue.check_code}** [{issue.severity}] "
            f"{issue.exam_type or '—'} / {issue.entity}: "
            f"{issue.details.get('message', '')} — {count}"
            + (f" (например: {examples})" if examples else "")
        )
    lines += ["", "## Ограничения выводов", "", LIMITATIONS]
    return "\n".join(lines)


def run_quality(
    conn: Connection, reports_dir: Path | None = REPORTS_DIR, config_path: Path | None = None
) -> tuple[int, list[Issue]]:
    """Проверки → ops.quality_* → файлы отчета. Возвращает (run_id, нарушения)."""
    started = utc_now()
    issues = run_checks(conn)
    run_id = save_run(conn, started, issues)
    conn.commit()
    if reports_dir is not None:
        sources, synthetic = describe_sources(conn, config_path)
        reports_dir.mkdir(parents=True, exist_ok=True)
        base = reports_dir / f"quality_{started:%Y%m%d_%H%M}"
        base.with_suffix(".md").write_text(
            markdown(run_id, started, issues, sources, synthetic), encoding="utf-8"
        )
        payload = {
            "run_id": run_id,
            "generated_at": started.isoformat(),
            "synthetic": synthetic,
            "sources": sources,
            "issues": [issue.__dict__ for issue in issues],
        }
        base.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    errors = sum(issue.severity == "error" for issue in issues)
    logger.info(
        "Качество данных: запуск %d, нарушений %d, из них error %d", run_id, len(issues), errors
    )
    return run_id, issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверки качества данных СМА")
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR, help="папка для отчетов")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()
    config = Path(os.environ.get("SMA_PLATFORMS_CONFIG", "config/platforms.yaml"))
    password = os.environ.get("SMA_LOADER_PASSWORD", "")
    with connect(user="sma_loader", password=password) as conn:
        _, issues = run_quality(conn, args.reports, config)
    raise SystemExit(1 if any(issue.severity == "error" for issue in issues) else 0)


if __name__ == "__main__":
    main()
