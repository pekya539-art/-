# -*- coding: utf-8 -*-
"""
東京都 本免学科試験 予約カレンダー キャンセル監視スクリプト

TEST_NOTIFY=1 の場合はサイトに一切アクセスせず、架空の日付(2099-01-01)に
偽の空きがあるとして通知パイプラインだけを検証する「テストモード」で動く。
"""
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

import config
import notify
import state_store

JST = timezone(timedelta(hours=9))
ARTIFACT_DIR = "artifacts"


def log(msg: str) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def ensure_artifact_dir():
    os.makedirs(ARTIFACT_DIR, exist_ok=True)


def dump_debug(page: Page, label: str) -> None:
    if not config.DEBUG:
        return
    ensure_artifact_dir()
    safe_label = re.sub(r"[^0-9A-Za-z_-]+", "_", label)
    ts = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=f"{ARTIFACT_DIR}/{ts}_{safe_label}.png", full_page=True)
        html = page.content()
        with open(f"{ARTIFACT_DIR}/{ts}_{safe_label}.html", "w", encoding="utf-8") as f:
            f.write(html)
        log(f"[debug] artifacts/{ts}_{safe_label}.(png|html) を保存しました")
    except Exception as e:  # noqa: BLE001
        log(f"[debug] artifact保存に失敗: {e}")


def is_forbidden_text(text: str) -> bool:
    return any(h in (text or "") for h in config.FORBIDDEN_BUTTON_TEXT_HINTS)


def click_selector(page: Page, selector: str, description: str, timeout_ms: int = None) -> bool:
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        text = (loc.inner_text() or "").strip()
        if is_forbidden_text(text):
            raise RuntimeError(f"安全のため '{text}' のクリックは拒否しました")
        loc.click(timeout=timeout_ms)
        time.sleep(config.ACTION_DELAY_SEC)
        return True
    except PWTimeout:
        log(f"'{description}' ({selector}) が時間内に見つかりませんでした")
        return False


def click_venue(page: Page, venue: str, timeout_ms: int = None) -> bool:
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    selector = f'label:has(input[name="{config.VENUE_RADIO_NAME}"][value*="{venue}"])'
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.click(timeout=timeout_ms)
        time.sleep(config.ACTION_DELAY_SEC)
        return True
    except PWTimeout:
        log(f"試験場 '{venue}' の選択肢が時間内に見つかりませんでした")
        return False


def wait_for_calendar(page: Page, timeout_ms: int = None) -> bool:
    timeout_ms = timeout_ms or config.NAV_TIMEOUT_MS
    try:
        page.locator("#datepicker table.ui-datepicker-calendar").wait_for(
            state="visible", timeout=timeout_ms
        )
        return True
    except PWTimeout:
        return False


def get_selectable_day_cells(page: Page):
    return page.locator("#datepicker td[data-handler='selectDay']")


def go_to_next_month(page: Page, timeout_ms: int = None) -> bool:
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    nxt = page.locator("a.ui-datepicker-next:not(.ui-state-disabled)")
    try:
        if nxt.count() == 0:
            return False
        nxt.first.click(timeout=timeout_ms)
        time.sleep(config.ACTION_DELAY_SEC)
        return True
    except PWTimeout:
        return False


def wait_for_time_panel(page: Page, timeout_ms: int = None) -> None:
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    try:
        page.locator("#visitTimeChoiceList").wait_for(state="visible", timeout=timeout_ms)
        page.locator("#waitVisitTimeList").wait_for(state="hidden", timeout=timeout_ms)
    except PWTimeout:
        pass


def extract_seats_from_panel(panel_text: str):
    result = {"am": None, "pm": None}
    if not panel_text:
        return result

    am_idx = panel_text.find(config.AM_TEXT)
    pm_idx = panel_text.find(config.PM_TEXT)

    def seats_in(segment: str):
        m = re.search(config.SEAT_TEXT_REGEX, segment)
        return int(m.group(1)) if m else None

    if am_idx != -1 and pm_idx != -1:
        if am_idx < pm_idx:
            result["am"] = seats_in(panel_text[am_idx:pm_idx])
            result["pm"] = seats_in(panel_text[pm_idx:])
        else:
            result["pm"] = seats_in(panel_text[pm_idx:am_idx])
            result["am"] = seats_in(panel_text[am_idx:])
    elif am_idx != -1:
        result["am"] = seats_in(panel_text[am_idx:])
    elif pm_idx != -1:
        result["pm"] = seats_in(panel_text[pm_idx:])

    return result


def collect_venue_results(page: Page, venue: str) -> dict:
    results = {}

    log(f"[{venue}] START_URL を開いています…")
    page.goto(
        config.START_URL,
        timeout=config.NAV_TIMEOUT_MS,
        wait_until="domcontentloaded",
        referer=config.REFERER,
    )
    time.sleep(config.ACTION_DELAY_SEC)
    dump_debug(page, f"{venue}_00_top")

    if not click_selector(page, config.EXAM_TYPE_SELECTOR, config.STEP_EXAM_TYPE_TEXT):
        dump_debug(page, f"{venue}_01_exam_type_notfound")
        return results
    dump_debug(page, f"{venue}_01_exam_type")

    if not click_selector(page, config.LICENSE_FORM_SELECTOR, config.STEP_LICENSE_FORM_TEXT):
        dump_debug(page, f"{venue}_02_license_form_notfound")
        return results
    dump_debug(page, f"{venue}_02_license_form")

    if not click_venue(page, venue):
        dump_debug(page, f"{venue}_03_venue_notfound")
        return results
    dump_debug(page, f"{venue}_03_venue_selected")

    if not wait_for_calendar(page):
        log(f"[{venue}] カレンダーが表示されませんでした")
        dump_debug(page, f"{venue}_04_calendar_notfound")
        return results

    for month_offset in range(config.MONTHS_AHEAD):
        if month_offset > 0:
            if not go_to_next_month(page):
                log(f"[{venue}] 翌月へ進めず、{month_offset}ヶ月目までで打ち切ります")
                break

        dump_debug(page, f"{venue}_month{month_offset}_calendar")

        cells = get_selectable_day_cells(page)
        cell_count = cells.count()
        log(f"[{venue}] 月{month_offset}: 選択可能な日付セル {cell_count} 件を確認")

        for i in range(cell_count):
            cell = get_selectable_day_cells(page).nth(i)
            try:
                data_year = cell.get_attribute("data-year")
                data_month = cell.get_attribute("data-month")  # 0始まり(0=1月)
                day_link = cell.locator("a").first
                data_date = day_link.get_attribute("data-date")
            except Exception:
                continue

            if not (data_year and data_month is not None and data_date):
                continue

            try:
                day_link.click(timeout=config.ACTION_TIMEOUT_MS)
            except Exception:
                continue

            wait_for_time_panel(page)

            try:
                panel_text = page.locator("#visitTimeList").inner_text()
            except Exception:
                panel_text = ""

            seats = extract_seats_from_panel(panel_text)

            try:
                date_str = f"{int(data_year):04d}-{int(data_month) + 1:02d}-{int(data_date):02d}"
            except ValueError:
                date_str = ""

            if date_str and (seats["am"] is not None or seats["pm"] is not None):
                results.setdefault(date_str, {})
                if seats["am"] is not None:
                    results[date_str]["am"] = seats["am"]
                if seats["pm"] is not None:
                    results[date_str]["pm"] = seats["pm"]
            else:
                dump_debug(page, f"{venue}_month{month_offset}_day{i}_unparsed")

    return results


def scanned_month_keys() -> list:
    """今回スキャン対象になった月("YYYY-MM"形式)の一覧。"""
    today = datetime.now(JST).date()
    keys = []
    y, m = today.year, today.month
    for _ in range(config.MONTHS_AHEAD):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def reset_unseen_slots(state: dict, venue: str, venue_results: dict, month_keys: list) -> int:
    """
    今回スキャンした月・試験場のうち、カレンダーに出てこなかった日付は
    「満席になって選択不可になった」とみなし、notified フラグを解除する。

    このサイトは満席の日をカレンダーから消してしまうため、この処理が無いと
    「残り0名」を一度も観測できず、notified が true のまま永久に残ってしまう。
    その結果、一度通知した枠が再び空いても二度と通知されなくなる。
    """
    observed = set()
    for date_str, seats in venue_results.items():
        for ampm in ("am", "pm"):
            if ampm in seats:
                observed.add(state_store.slot_key(venue, date_str, ampm))

    reset_count = 0
    for key, val in state["slots"].items():
        if key in observed:
            continue
        parts = key.split("|")
        if len(parts) != 3:
            continue
        k_venue, k_date, _k_ampm = parts
        if k_venue != venue:
            continue
        if k_date[:7] not in month_keys:
            continue
        if val.get("seats", 0) != 0 or val.get("notified", False):
            state["slots"][key] = {"seats": 0, "notified": False}
            reset_count += 1
    return reset_count


def process_venue_results(state: dict, venue: str, venue_results: dict) -> int:
    """検知→重複防止→通知送信。戻り値は通知した件数。"""
    count = 0
    for date_str, seats in venue_results.items():
        for ampm_key, ampm_label in (("am", "午前"), ("pm", "午後")):
            if ampm_key not in seats:
                continue
            n = seats[ampm_key]
            should_notify = state_store.evaluate_slot(state, venue, date_str, ampm_key, n)
            if should_notify:
                msg = notify.build_vacancy_message(venue, date_str, ampm_label, n)
                sent = notify.send_notification(msg, priority="urgent")
                log(f"[NOTIFY] {venue} {date_str} {ampm_label} 残り{n}名 送信={'成功' if sent else '失敗'}")
                if sent:
                    state_store.mark_notified(state, venue, date_str, ampm_key)
                    count += 1
    return count


def run_once() -> int:
    state = state_store.load_state()
    notify_count = 0

    if os.environ.get("TEST_NOTIFY", "0") == "1":
        # サイトには一切アクセスしない。通知パイプラインだけを本番と同じコードで検証する。
        # 実在しない日付(2099-01-01)なので本物のデータと絶対に混ざらない。
        test_venue = config.VENUES[0] if config.VENUES else "府中試験場"
        log(f"[TEST_NOTIFY] テストモード: {test_venue} 2099-01-01 午前に偽の空き(1名)があるとして検証します")
        notify_count += process_venue_results(state, test_venue, {"2099-01-01": {"am": 1}})
        state_store.save_state(state)
        return notify_count

    month_keys = scanned_month_keys()
    log(f"今回のスキャン対象月: {', '.join(month_keys)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                "TokyoLicenseCancelWatch/1.0 (individual-use monitoring; contact via GitHub)"
            ),
            extra_http_headers={"Referer": config.REFERER},
        )
        page = context.new_page()
        page.set_default_timeout(config.ACTION_TIMEOUT_MS)

        for venue in config.VENUES:
            try:
                venue_results = collect_venue_results(page, venue)
            except Exception as e:  # noqa: BLE001
                log(f"[{venue}] 収集中にエラー: {e}")
                traceback.print_exc()
                dump_debug(page, f"{venue}_ERROR")
                continue

            log(f"[{venue}] {len(venue_results)} 日分のデータを取得しました")
            notify_count += process_venue_results(state, venue, venue_results)

            n_reset = reset_unseen_slots(state, venue, venue_results, month_keys)
            if n_reset:
                log(f"[{venue}] カレンダーから消えた {n_reset} 枠を満席とみなしてリセットしました")

            time.sleep(config.VENUE_DELAY_SEC)

        browser.close()

    state_store.save_state(state)
    return notify_count


def main():
    try:
        count = run_once()
        log(f"完了。通知件数: {count}")
    except Exception as e:  # noqa: BLE001
        log(f"致命的エラー: {e}")
        traceback.print_exc()
        notify.send_notification(notify.build_error_message(f"{type(e).__name__}: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
