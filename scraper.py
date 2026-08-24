# -*- coding: utf-8 -*-
"""
東京都 本免学科試験 予約カレンダー キャンセル監視スクリプト

やること:
  1. START_URL を開く
  2. 「教習所卒業等」→「免許証のみ」を選択(実際のDOM構造に基づく確実なID指定)
  3. 対象試験場(府中/鮫洲/江東)を選択(value属性の部分一致で確実に指定)
  4. jQuery UI の datepicker で選択可能(data-handler="selectDay")な日付だけを順にクリック
  5. 午前/午後それぞれの残席数(「残り○名」)を読み取る
  6. 前回の記録(state.json)と比較し、0名→1名以上になったスロットを検知
  7. 検知したら Discord へ通知
  8. 予約操作(氏名・生年月日等を入力する以降の画面へは絶対に進まない)

このスクリプトは Playwright (Chromium) で動作する。
実際にサイトのHTML(artifacts経由で取得)を確認した上で、
テキストの部分一致ではなく、ID・name・value・data属性など
一意に特定できる情報を使って要素を探すようにしてある。
"""
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
    import os
    os.makedirs(ARTIFACT_DIR, exist_ok=True)


def dump_debug(page: Page, label: str) -> None:
    """調査・デバッグ用にスクリーンショットとHTMLを保存する。"""
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
    """CSSセレクタで一意に要素を指定してクリックする(見つからなければFalse)。"""
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        text = (loc.inner_text() or "").strip()
        if is_forbidden_text(text):
            raise RuntimeError(f"安全のため '{text}' のクリックは拒否しました(予約確定系の疑いがある文言)")
        loc.click(timeout=timeout_ms)
        time.sleep(config.ACTION_DELAY_SEC)
        return True
    except PWTimeout:
        log(f"'{description}' ({selector}) が時間内に見つかりませんでした")
        return False


def click_venue(page: Page, venue: str, timeout_ms: int = None) -> bool:
    """
    試験場のラジオボタンを、value属性の部分一致(例: value="270:府中試験場")で
    一意に特定してクリックする。テキストの部分一致だと、注釈文の中に
    試験場名が複数回出てくるページがあり誤爆するため使わない。
    """
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
    """日付選択カレンダー(jQuery UI datepicker)が表示されるのを待つ。"""
    timeout_ms = timeout_ms or config.NAV_TIMEOUT_MS
    try:
        page.locator("#datepicker table.ui-datepicker-calendar").wait_for(
            state="visible", timeout=timeout_ms
        )
        return True
    except PWTimeout:
        return False


def get_selectable_day_cells(page: Page):
    """
    カレンダー上の「本当に選択できる日付セル」だけを返す。
    jQuery UI datepicker では、選択可能な日には data-handler="selectDay" が
    付与され、選択不可の日は ui-datepicker-unselectable / ui-state-disabled
    が付き、クリックしても何も起きない。
    """
    return page.locator("#datepicker td[data-handler='selectDay']")


def go_to_next_month(page: Page, timeout_ms: int = None) -> bool:
    """
    「次へ」リンクをクリックして翌月に進む。
    既に進めない(無効化されている)場合は False を返す。
    """
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    nxt = page.locator("a.ui-datepicker-next:not(.ui-state-disabled)")
    try:
        if nxt.count() == 0:
            return False
        nxt.first.click(timeout=timeout_ms)
        # 月が切り替わるのを待つ(カレンダーが再描画されるまでの猶予)
        time.sleep(config.ACTION_DELAY_SEC)
        return True
    except PWTimeout:
        return False


def wait_for_time_panel(page: Page, timeout_ms: int = None) -> None:
    """
    日付クリック後、受付時間帯(午前/午後)の情報が表示されるのを待つ。
    出ない場合もあるので、失敗しても例外にはしない(パネルが空のまま処理継続)。
    """
    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    try:
        page.locator("#visitTimeChoiceList").wait_for(state="visible", timeout=timeout_ms)
        # ローディング表示(空き状況を調べています)が消えるのも待つ
        page.locator("#waitVisitTimeList").wait_for(state="hidden", timeout=timeout_ms)
    except PWTimeout:
        pass


def extract_seats_from_panel(panel_text: str):
    """
    日付クリック後に表示されるパネルのテキストから、
    午前/午後それぞれの残席数を抽出する。
    戻り値: {"am": int|None, "pm": int|None}
    """
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
    """
    1つの試験場について、START_URL から遷移し直して
    { "YYYY-MM-DD": {"am": int, "pm": int}, ... } を返す。
    """
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
            # 日付をクリックするたびにDOMが再描画されるため、
            # 毎回セル一覧を取り直してから i 番目を参照する。
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


def run_once() -> int:
    """
    1回分の監視を実行する。戻り値は通知した件数。
    """
    state = state_store.load_state()
    notify_count = 0

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

            for date_str, seats in venue_results.items():
                for ampm_key, ampm_label in (("am", "午前"), ("pm", "午後")):
                    if ampm_key not in seats:
                        continue
                    n = seats[ampm_key]
                    should_notify = state_store.evaluate_slot(state, venue, date_str, ampm_key, n)
                    if should_notify:
                        msg = notify.build_vacancy_message(venue, date_str, ampm_label, n)
                        sent = notify.send_discord_message(msg)
                        log(f"[NOTIFY] {venue} {date_str} {ampm_label} 残り{n}名 送信={'成功' if sent else '失敗'}")
                        if sent:
                            notify_count += 1

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
        notify.send_discord_message(notify.build_error_message(f"{type(e).__name__}: {e}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
