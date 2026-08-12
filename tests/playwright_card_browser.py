from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".data"
BASE_URL = os.environ.get("CARD_BROWSER_URL", "http://127.0.0.1:8041").rstrip("/")


def _assert_no_page_overflow(page) -> None:
    dimensions = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth
        })"""
    )
    assert dimensions["document"] <= dimensions["viewport"], dimensions
    assert dimensions["body"] <= dimensions["viewport"], dimensions


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        desktop = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        desktop.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        desktop.goto(BASE_URL + "/")
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="治理总览").wait_for()
        assert desktop.get_by_text("阻断诊断").is_visible()
        knowledge_status = desktop.get_by_label("Knowledge 状态")
        assert knowledge_status.get_by_text("Knowledge 当前", exact=True).is_visible()
        assert knowledge_status.get_by_text("9", exact=True).is_visible()
        acceptance = desktop.get_by_label("验收状态")
        assert "static" not in acceptance.inner_text().lower()
        for label in ("静态", "楼层", "边界", "场景", "完整"):
            item = acceptance.locator("div", has_text=label)
            assert item.get_by_text(label, exact=True).is_visible()
            assert item.get_by_text("passed", exact=True).is_visible()
        assert acceptance.get_by_text("failed", exact=True).count() == 0
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-dashboard-desktop.png"), full_page=True)

        desktop.goto(BASE_URL + "/catalog")
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="总管目录").wait_for()
        assert desktop.get_by_text("knowledge.kernel-architecture", exact=True).first.is_visible()
        assert desktop.get_by_text("check.scenario.handoff", exact=True).first.is_visible()
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-catalog-desktop.png"), full_page=True)

        desktop.goto(BASE_URL + "/checks")
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="检测证据").wait_for()
        assert desktop.get_by_text("当前", exact=True).first.is_visible()
        desktop.get_by_text("check.product.formal", exact=True).first.click()
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="规则结果").wait_for()
        assert desktop.get_by_text("constitution.product-formal-acceptance", exact=True).first.is_visible()
        assert desktop.get_by_text("精确证据足迹", exact=True).is_visible()
        assert desktop.get_by_text("source-global", exact=True).count() == 0
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-checks-desktop.png"), full_page=True)

        desktop.goto(BASE_URL + "/cards")
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("searchbox", name="搜索卡片").fill("语义")
        desktop.get_by_role("button", name="查询").click()
        desktop.wait_for_load_state("networkidle")
        assert desktop.get_by_text("floor.workbench-v070", exact=True).is_visible()
        desktop.get_by_text("floor.workbench-v070", exact=True).click()
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="v0.7.0 工作台后端").wait_for()
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-card-detail-desktop.png"), full_page=True)

        desktop.goto(BASE_URL + "/cards/knowledge.kernel-architecture")
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="Base 内核导航").wait_for()
        assert desktop.get_by_text("当前可复用知识 · 无修订历史 · current", exact=True).is_visible()
        assert desktop.get_by_text("此卡只表达当前可复用知识，不保存历史", exact=False).is_visible()
        assert "rNone" not in desktop.content()
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-knowledge-desktop.png"), full_page=True)

        desktop.goto(BASE_URL + "/contracts?disposition=boundary")
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_role("heading", name="产品合同全局视图").wait_for()
        assert desktop.get_by_text("boundary.cartridge-handoff", exact=True).first.is_visible()
        assert desktop.locator("tbody").get_by_text("unclassified", exact=True).count() == 0
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-contracts-desktop.png"), full_page=True)

        context_link = desktop.locator('a[href="/context"]')
        assert context_link.is_visible(), desktop.locator("nav").inner_html()
        context_link.click()
        desktop.wait_for_load_state("networkidle")
        desktop.get_by_label("目标路径").fill("desktop-runner:shell/go/internal/api/api.go")
        desktop.get_by_role("button", name="编译上下文").click()
        desktop.wait_for_load_state("networkidle")
        assert desktop.get_by_text("floor.dr-v060-sp", exact=True).first.is_visible()
        assert desktop.get_by_text("floor.workbench-v070", exact=True).count() == 0
        assert desktop.get_by_text("boundary.cartridge-handoff", exact=True).count() == 0
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-context-desktop.png"), full_page=True)

        desktop.get_by_label("目标路径").fill("")
        desktop.get_by_label("公开合同").fill("cartridgeflow:cartridgeflow.package.manifest@1.0.0")
        desktop.get_by_role("button", name="编译上下文").click()
        desktop.wait_for_load_state("networkidle")
        assert desktop.get_by_text("precise", exact=True).is_visible()
        assert desktop.get_by_text("boundary.cartridge-handoff", exact=True).first.is_visible()
        assert desktop.get_by_text("floor.workbench-v070", exact=True).first.is_visible()
        assert desktop.get_by_text("floor.dr-v060-sp", exact=True).first.is_visible()
        assert desktop.get_by_text("scenario.workbench-to-dr", exact=True).is_visible()
        _assert_no_page_overflow(desktop)
        desktop.screenshot(path=str(OUTPUT / "browser-contract-route-desktop.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
        mobile.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        mobile.goto(BASE_URL + "/")
        mobile.wait_for_load_state("networkidle")
        mobile.get_by_role("heading", name="治理总览").wait_for()
        assert mobile.get_by_label("验收状态").get_by_text("完整", exact=True).is_visible()
        _assert_no_page_overflow(mobile)
        mobile.screenshot(path=str(OUTPUT / "browser-dashboard-mobile.png"), full_page=True)

        mobile.goto(BASE_URL + "/coverage?status=uncovered")
        mobile.wait_for_load_state("networkidle")
        mobile.get_by_role("heading", name="作用域覆盖").wait_for()
        assert mobile.get_by_role("navigation", name="主导航").is_visible()
        _assert_no_page_overflow(mobile)
        mobile.screenshot(path=str(OUTPUT / "browser-coverage-mobile.png"), full_page=True)

        browser.close()
    assert not console_errors, console_errors
    print(
        json.dumps(
            {
                "desktop": "browser-dashboard-desktop.png",
                "catalog": "browser-catalog-desktop.png",
                "checks": "browser-checks-desktop.png",
                "card": "browser-card-detail-desktop.png",
                "knowledge": "browser-knowledge-desktop.png",
                "contracts": "browser-contracts-desktop.png",
                "context": "browser-context-desktop.png",
                "contract_route": "browser-contract-route-desktop.png",
                "mobile_dashboard": "browser-dashboard-mobile.png",
                "mobile": "browser-coverage-mobile.png",
                "console_errors": console_errors,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
