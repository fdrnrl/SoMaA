"""Визуальная проверка отдельного синтетического стенда в изолированном Chrome."""

import argparse
import json
import re
from pathlib import Path

from browser_checks import CONTRAST_JS, login, ready
from playwright.sync_api import expect, sync_playwright

from dashboard.pages import WORK_PAGES


def capture(page, title, route, width, height, output):
    ready(page)
    page.evaluate("document.fonts.ready")
    page.wait_for_function(
        "Array.from(document.querySelectorAll('[data-testid=stImage] img'))"
        ".every(image => image.complete && image.naturalWidth > 0)"
    )
    colors = page.evaluate(CONTRAST_JS)
    heading = page.get_by_role("heading", name=title, exact=True).bounding_box()
    toolbar = page.get_by_test_id("stHeader").bounding_box()
    result = {
        "route": route, "viewport": [width, height],
        "overflow": page.evaluate("document.documentElement.scrollWidth > innerWidth"),
        "contrast_min": min((r["ratio"] for r in colors), default=None),
        "contrast_failures": [r for r in colors if r["ratio"] < 4.5],
        "heading_clear_of_toolbar": heading["y"] >= toolbar["y"] + toolbar["height"],
        "screenshot": f"{route}-{width}.png",
    }
    page.screenshot(path=str(output / result["screenshot"]))
    print(f"Checked: {route} {width}x{height}", flush=True)
    return result


def run(url, output, quick=False, live_refresh=False, scheme="light", routes_only=None):
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Isolated synthetic UI; no production database or user browser profile",
              "scheme": scheme, "screens": []}
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        report["browser"] = browser.version
        context = browser.new_context(viewport={"width": 1280, "height": 720},
                                      color_scheme=scheme)
        page = context.new_page()
        page.goto(url, wait_until="commit")
        expect(page.get_by_role("heading", name="Вход в СМА", exact=True)).to_be_visible(
            timeout=30000)
        report["screens"].append(capture(page, "Вход в СМА", "login", 1280, 720, output))
        login(page, url)
        report["screens"].append(capture(page, "СМА", "home", 1280, 720, output))
        routes = WORK_PAGES[:1] if quick else WORK_PAGES
        if routes_only:
            routes = [r for r in WORK_PAGES if r[1] in routes_only]
        sizes = ((1280, 720),) if quick else ((1280, 720), (1920, 1080))
        for width, height in sizes:
            page.set_viewport_size({"width": width, "height": height})
            for title, route, _ in routes:
                page.get_by_test_id("stSidebar").get_by_role("link", name=title,
                                                            exact=True).click()
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                ready(page)
                if route in ("analysis", "live"):
                    page.get_by_role("combobox", name="Завершенный расчет", exact=True).click()
                    page.get_by_role("option", name=re.compile(r"^7 ·")).click()
                    ready(page)
                report["screens"].append(capture(page, title, route, width, height, output))
        if live_refresh:
            page.get_by_test_id("stSidebar").get_by_role("link", name="Живой экран",
                                                        exact=True).click()
            ready(page)
            caption = page.get_by_test_id("stCaptionContainer").filter(
                has_text="Экран обновлен:")
            before = caption.inner_text()
            expect(caption).not_to_have_text(before, timeout=45000)
            report["refresh_without_interaction"] = {
                "before": before, "after": caption.inner_text()}
        browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    failures = [r for r in report["screens"] if r["overflow"] or r["contrast_failures"]
                or not r["heading_clear_of_toolbar"]]
    if failures:
        raise AssertionError("Layout/contrast failures; see " + str(output / "report.json"))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8517")
    parser.add_argument("--output", type=Path, default=Path("tests/dashboard/artifacts/design"))
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--live-refresh", action="store_true")
    parser.add_argument("--scheme", choices=("light", "dark"), default="light")
    parser.add_argument("--routes", nargs="+", choices=[r[1] for r in WORK_PAGES])
    args = parser.parse_args()
    run(args.url, args.output, args.quick, args.live_refresh, args.scheme, args.routes)
