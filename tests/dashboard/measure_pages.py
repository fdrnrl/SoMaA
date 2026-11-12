"""T-23: 100 переходов в 10 сеансах подготовленного стенда; не замер демо."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from dashboard.data import EXAMS
from dashboard.pages import WORK_PAGES


def validate_manifest(path):
    content = path.read_bytes()
    manifest = json.loads(content)
    counts = {exam: row["participants"] for exam, row in manifest["platforms"].items()}
    if manifest.get("synthetic") is not True or set(counts) != set(EXAMS) \
            or any(value != 400 for value in counts.values()):
        raise ValueError("Для T-23 нужен загруженный набор по 400 участников на платформу.")
    return hashlib.sha256(content).hexdigest()


def p95(values):
    if not values:
        raise ValueError("Нет измерений.")
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


async def ready(page, expect):
    await expect(page.get_by_test_id("stApp")).to_have_attribute(
        "data-test-script-state", "notRunning", timeout=30000)
    await expect(page.get_by_test_id("stException")).to_have_count(0)
    if await page.get_by_text("ТЕСТОВЫЙ СТЕНД", exact=False).count() \
            or await page.get_by_text("Синтетические данные · статический", exact=False).count():
        raise ValueError("Тестовый UI/статический снимок не является стендом T-23.")
    for image in await page.get_by_test_id("stMain").locator("img").all():
        await expect(image).to_be_visible()
        await page.wait_for_function("img => img.complete && img.naturalWidth > 0",
                                     arg=await image.element_handle())


async def measure_session(browser, number, args, login, password, expect):
    context = await browser.new_context(viewport={"width": 1280, "height": 720})
    measurements = []
    try:
        page = await context.new_page()
        await page.goto(args.url, wait_until="commit")
        await page.get_by_label("Логин", exact=True).fill(login)
        await page.get_by_label("Пароль", exact=True).fill(password)
        await page.get_by_role("button", name="Войти", exact=True).click()
        await expect(page.get_by_role("heading", name="СМА", exact=True)).to_be_visible(
            timeout=30000)
        await ready(page, expect)
        for title in ("Углубленный анализ", "Живой экран"):
            await page.get_by_role("link", name=title, exact=True).click()
            await ready(page, expect)
            await page.get_by_role("combobox", name="Завершенный расчет", exact=True).click()
            import re

            await page.get_by_role("option", name=re.compile(rf"^{args.run_id} ·")).click()
            await ready(page, expect)
        await page.get_by_role("link", name="Главная", exact=True).click()
        await ready(page, expect)
        for index in range(10):
            title, route, _ = WORK_PAGES[(number + index) % len(WORK_PAGES)]
            started = time.perf_counter()
            await page.get_by_role("link", name=title, exact=True).click()
            await expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible(
                timeout=30000)
            await ready(page, expect)
            measurements.append({"session": number + 1, "route": route,
                                 "seconds": time.perf_counter() - started})
        return {"measurements": measurements, "error": None}
    except Exception as error:
        return {"measurements": measurements, "error": type(error).__name__}
    finally:
        await context.close()


async def measure(args, login, password, manifest_hash):
    from playwright.async_api import async_playwright, expect

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(channel="chrome", headless=True)
        version = browser.version
        try:
            results = await asyncio.gather(*(measure_session(
                browser, number, args, login, password, expect) for number in range(10)))
        finally:
            await browser.close()
    rows = [row for result in results for row in result["measurements"]]
    errors = [result["error"] for result in results if result["error"]]
    percentile = p95([row["seconds"] for row in rows]) if rows else None
    report = {"test": "T-23", "browser": version, "manifest_sha256": manifest_hash,
              "dataset_loaded_confirmed_by_operator": True, "run_id": args.run_id,
              "sessions": 10, "expected_opens": 100, "successful_opens": len(rows),
              "p95_method": "nearest rank", "p95_seconds": percentile, "errors": errors,
              "passed": len(rows) == 100 and not errors and percentile <= 3,
              "scope": "Navigation to rendered page in authenticated sessions; "
                       "analysis/live run selected before measurement", "measurements": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("successful_opens", "p95_seconds", "passed")}))
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--confirmed-dataset-loaded", action="store_true")
    parser.add_argument("--output", type=Path,
                        default=Path("tests/dashboard/artifacts/t23.json"))
    args = parser.parse_args()
    url = urlparse(args.url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username \
            or url.password or url.query or url.fragment or args.run_id < 1:
        parser.error("Нужен адрес стенда без секретов в URL и завершенный run_id.")
    if not args.confirmed_dataset_loaded:
        parser.error("Сначала загрузить отдельный набор и подтвердить его соответствие стенду.")
    login, password = os.environ.get("SMA_QA_LOGIN"), os.environ.get("SMA_QA_PASSWORD")
    if not login or not password:
        parser.error("Нужны SMA_QA_LOGIN/SMA_QA_PASSWORD через окружение, не аргументы.")
    try:
        manifest_hash = validate_manifest(args.manifest)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Не удалось подтвердить манифест набора по 400 участников.")
    return asyncio.run(measure(args, login, password, manifest_hash))


if __name__ == "__main__":
    raise SystemExit(main())
