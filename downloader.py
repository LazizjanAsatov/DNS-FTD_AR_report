"""Playwright downloader: login → today filter → Exactly + date range → error_check export template → CSV."""
import logging
import os
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [downloader] %(levelname)s %(message)s")

DATE_TYPE_SELECT = "li.daterange-type select"
FROM_DATE_INPUT = "input[name$=':from-date:date']"
TO_DATE_INPUT = "input[name$=':to-date:date']"
CSV_BUTTON = "input[name='post-buttons:download-csv-link']"


def download_csv(login_url, report_url, username, password, date_from, date_to, output_path, headless=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        log.info("Opening login page")
        page.goto(login_url)
        page.fill("input[name='j_username']", username)
        page.fill("input[name='j_password']", password)
        page.click("input[type='submit'], button[type='submit']")
        page.wait_for_load_state("networkidle")

        log.info("Opening reports page")
        page.goto(report_url)
        page.wait_for_load_state("networkidle")

        log.info("Opening search-template dropdown (advanced-search-menuDropper)")
        page.click("#advanced-search-menuDropper-box")
        page.wait_for_selector("a.drop-ul-li-a:has(span:text-is('today'))", state="visible")
        log.info("Choosing 'today' template")
        page.click("a.drop-ul-li-a:has(span:text-is('today'))")
        page.wait_for_load_state("networkidle")

        log.info("Setting date type to EXACTLY")
        page.wait_for_selector(DATE_TYPE_SELECT, state="attached", timeout=15000)
        sel = page.locator(DATE_TYPE_SELECT)
        log.info("Current date-type value: %s", sel.input_value())

        sel.select_option(value="EXACTLY")
        sel.evaluate(
            """el => {
                el.value = 'EXACTLY';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }"""
        )
        page.wait_for_timeout(500)
        log.info("After select, date-type value: %s", sel.input_value())

        try:
            page.wait_for_selector(FROM_DATE_INPUT, state="visible", timeout=5000)
        except Exception:
            log.warning("Date inputs not visible after select_option; trying keyboard")
            sel.focus()
            sel.press("e")
            page.wait_for_timeout(300)
            log.info("After keypress, date-type value: %s", sel.input_value())
            page.wait_for_selector(FROM_DATE_INPUT, state="visible", timeout=15000)
        page.wait_for_load_state("networkidle")

        log.info("Filling date range %s → %s", date_from, date_to)
        for attempt in range(3):
            for sel, val in [(FROM_DATE_INPUT, date_from), (TO_DATE_INPUT, date_to)]:
                page.locator(sel).click()
                page.locator(sel).press("Control+A")
                page.locator(sel).press("Delete")
                page.locator(sel).type(val, delay=20)
                page.locator(sel).press("Tab")
            page.wait_for_timeout(400)
            actual_from = page.locator(FROM_DATE_INPUT).input_value()
            actual_to = page.locator(TO_DATE_INPUT).input_value()
            log.info("Attempt %d: fields now %s → %s", attempt + 1, actual_from, actual_to)
            if actual_from == date_from and actual_to == date_to:
                break
        else:
            raise RuntimeError(f"Date fields did not accept values: got {actual_from} → {actual_to}")

        log.info("Opening export-template dropdown")
        page.hover(".order-export-menuDropper")
        page.wait_for_selector("a.orders-export-template-drop-ul-li-a:has(span:text-is('error_check'))", state="visible")
        log.info("Choosing 'error_check' export template")
        page.click("a.orders-export-template-drop-ul-li-a:has(span:text-is('error_check'))")

        log.info("Clicking CSV download")
        with page.expect_download(timeout=180000) as dl_info:
            page.click(CSV_BUTTON)
        download = dl_info.value

        out = os.path.abspath(output_path)
        download.save_as(out)

        with open(out, "r", encoding="utf-8-sig") as f:
            first = f.readline()
            rest = f.read()
        if first.lstrip().startswith('"Date range'):
            with open(out, "w", encoding="utf-8", newline="") as f:
                f.write(rest)
            log.info("Stripped metadata banner")

        log.info("Saved to %s", out)
        browser.close()
        return out


if __name__ == "__main__":
    from datetime import date, timedelta
    from pathlib import Path

    _yesterday = date.today() - timedelta(days=1)
    _start = _yesterday - timedelta(days=6)

    download_csv(
        login_url="https://gate.dns-pay.com/paynet-ui/login-step1",
        report_url="https://gate.dns-pay.com/paynet-ui/reports/transaction",
        username="la_dns",
        password="x9pZ0Z4NB6d_",
        date_from=_start.strftime("%d.%m.%Y"),
        date_to=_yesterday.strftime("%d.%m.%Y"),
        output_path=str(Path(__file__).resolve().parent / "input.csv"),
        headless=False,
    )
