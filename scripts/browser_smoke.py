"""Browser acceptance test against an already-running DEMO server. Creates a test subscription."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def main(url, screenshot_dir):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1200}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(url)
        expect(page.locator("#mode-badge")).to_contain_text("演示模式")
        page.locator("#new-user").click()
        name = "体验订阅 " + uuid4().hex[:6]
        page.locator("#name").fill(name)
        page.locator("#role").fill("后端工程师")
        page.locator("#save-button").click()
        expect(page.locator("#save-message")).to_contain_text("订阅已保存")
        user_id = page.locator("#user-select").input_value()
        page.locator("#generate-button").click()
        expect(page.locator(".article-card")).to_have_count(2, timeout=30000)
        expect(page.locator(".brief-date")).to_contain_text("已推送", timeout=10000)
        page.wait_for_function("id => Number(localStorage.getItem('daily-ai-cursor-'+id)) > 0", arg=user_id)
        assert "虚构" in page.locator("#brief-panel").inner_text()
        assert "融资计划" not in page.locator("#brief-panel").inner_text()
        page.locator("#trace-panel > summary").click()
        expect(page.locator(".trace-row")).to_have_count(8)
        expect(page.locator("#trace-mode")).to_contain_text("固定脚本")
        page.locator("#trace-panel > summary").click()
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / "preview-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(screenshot_dir / "preview-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1200})
        page.locator("#topics").fill("研究论文")
        page.locator("#save-button").click()
        expect(page.locator("#save-message")).to_contain_text("订阅已保存")
        page.locator("#generate-button").click()
        expect(page.locator(".article-card")).to_have_count(1, timeout=30000)
        expect(page.locator(".article-card")).to_contain_text("长上下文")
        expect(page.locator(".history-item")).to_have_count(2)
        page.locator(".history-item").last.click()
        expect(page.locator(".article-card")).to_have_count(2)
        page.reload()
        expect(page.locator("#name")).to_have_value(name)
        expect(page.locator("#topics")).to_have_value("研究论文")
        expect(page.locator(".history-item")).to_have_count(2)
        assert not errors, errors
        print(
            json.dumps(
                {
                    "status": "passed",
                    "user_id": user_id,
                    "checks": [
                        "create_subscription",
                        "generate",
                        "preference_filtering",
                        "exclusion",
                        "SSE_push",
                        "tool_trace",
                        "desktop",
                        "mobile_no_overflow",
                        "edit_preferences",
                        "history",
                        "reload_persistence",
                    ],
                    "browser_errors": errors,
                },
                ensure_ascii=False,
            )
        )
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--screenshots", type=Path, default=Path("docs"))
    args = parser.parse_args()
    main(args.url, args.screenshots)
