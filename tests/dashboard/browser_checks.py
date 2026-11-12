"""Chrome QA изолированного стенда: экраны, скачивание CSV, цвета текста."""

import argparse
import csv
import io
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from test_auth import TEST_PASSWORD

from dashboard.pages import WORK_PAGES

CONTRAST_JS = """() => {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', {willReadFrequently:true});
  function rgba(color) {
    ctx.clearRect(0,0,1,1); ctx.fillStyle = color; ctx.fillRect(0,0,1,1);
    const p = ctx.getImageData(0,0,1,1).data; return [p[0],p[1],p[2],p[3]/255];
  }
  function blend(top, bottom) {
    return top.slice(0,3).map((v,i) => v*top[3] + bottom[i]*(1-top[3]));
  }
  function luminance(rgb) {
    const c = rgb.map(v => v/255).map(v => v<=0.04045 ? v/12.92 : ((v+0.055)/1.055)**2.4);
    return c[0]*0.2126 + c[1]*0.7152 + c[2]*0.0722;
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const results = []; let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.trim(), el = node.parentElement;
    if (!text || !el || el.closest('script,style,svg,[aria-hidden="true"]')) continue;
    if (!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) continue;
    if (el.getBoundingClientRect().width === 0 || el.closest('button:disabled')) continue;
    const ancestors = []; for(let p=el; p; p=p.parentElement) ancestors.push(p);
    let bg = [255,255,255], opacity = 1;
    for(const p of ancestors.reverse()) {
      const s = getComputedStyle(p); bg = blend(rgba(s.backgroundColor),bg);
      opacity *= Number(s.opacity);
    }
    const fg = rgba(getComputedStyle(el).color); fg[3] *= opacity;
    const foreground = blend(fg,bg), a = luminance(foreground), b = luminance(bg);
    const ratio = (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    results.push({text:text.slice(0,110),ratio,foreground,background:bg,
                  tag:el.tagName,html: text==='2' ? el.outerHTML : undefined});
  }
  return results;
}"""


def ready(page):
    expect(page.get_by_test_id("stApp")).to_have_attribute(
        "data-test-script-state", "notRunning", timeout=30000)
    expect(page.get_by_test_id("stException")).to_have_count(0)


def login(page, url, user="staff", password=TEST_PASSWORD):
    page.goto(url, wait_until="commit")
    page.get_by_label("Логин", exact=True).fill(user)
    page.get_by_label("Пароль", exact=True).fill(password)
    page.get_by_role("button", name="Войти", exact=True).click()
    expect(page.get_by_role("heading", name="СМА", exact=True)).to_be_visible(timeout=30000)
    ready(page)


def run(url, output):
    output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Synthetic UI only; not PostgreSQL, replay or T-23", "screens": []}
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel="chrome", headless=True)
        report["browser"] = browser.version
        (output / "browser-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        context = browser.new_context(viewport={"width": 1280, "height": 720},
                                      color_scheme="light", accept_downloads=True)
        page = context.new_page()
        login(page, url)
        for width, height in ((1280, 720), (1920, 1080)):
            page.set_viewport_size({"width": width, "height": height})
            for title, route, _ in WORK_PAGES:
                page.get_by_role("link", name=title, exact=True).click()
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                ready(page)
                if route in ("analysis", "live"):
                    page.get_by_role("combobox", name="Завершенный расчет", exact=True).click()
                    page.get_by_role("option", name=re.compile(r"^7 ·")).click()
                    ready(page)
                overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                if overflow:
                    raise AssertionError(f"Horizontal overflow: {route} {width}")
                colors = page.evaluate(CONTRAST_JS)
                failures = [r for r in colors if r["ratio"] < 4.5]
                screen = {"route": route, "viewport": [width, height], "overflow": overflow,
                          "contrast_min": min(r["ratio"] for r in colors),
                          "contrast_failures": failures}
                if route in ("analysis", "anomalies"):
                    with page.expect_download() as pending:
                        page.get_by_role("button", name="Выгрузить", exact=True).click()
                    download = pending.value
                    path = output / download.suggested_filename
                    download.save_as(path)
                    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8")),
                                           delimiter=";"))
                    assert len(rows[0]) == (19 if route == "analysis" else 11)
                    assert len(rows) == 2
                    screen["download"] = path.name
                    ready(page)
                page.screenshot(path=str(output / f"chrome-{width}-{route}.png"))
                report["screens"].append(screen)
                print(f"Chrome {width} {route}: contrast failures={len(failures)}", flush=True)
                (output / "browser-report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        context.close()
        browser.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8507")
    parser.add_argument("--output", type=Path, default=Path("tests/dashboard/artifacts/browser"))
    args = parser.parse_args()
    run(args.url, args.output)
