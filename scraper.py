# -*- coding: utf-8 -*-
"""
東京都 本免学科試験 予約カレンダー キャンセル監視スクリプト

やること:
  1. START_URL を開く
  2. 「教習所卒業等」→「免許証のみ」を選択
  3. 対象試験場(府中/鮫洲/江東)を選択
  4. カレンダーに表示されている選択可能な日付を順にクリック
  5. 午前/午後それぞれの残席数(「残り○名」)を読み取る
  6. 前回の記録(state.json)と比較し、0名→1名以上になったスロットを検知
  7. 検知したら Discord へ通知
  8. 予約操作(申込・確定ボタンのクリック)は一切行わない

このスクリプトは Playwright (Chromium) で動作する。
サイトの実際のDOM構造は事前に確認できていないため、
テキストベースの柔軟なロケータを優先し、想定外の構造に遭遇した場合は
artifacts/ 以下にスクリーンショットとHTMLを保存してから
「わからないものはスキップする」形で処理を続行する(全体を落とさない)。
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


def click_by_text(page: Page, text: str, timeout_ms: int = None) -> bool:
    """
    画面上の「テキストに一致する要素」をできるだけ柔軟にクリックする。
    予約確定系のボタン(FORBIDDEN_BUTTON_TEXT_HINTS)には絶対に一致させない安全策込み。
    見つからなければ False を返す(例外にしない=処理継続のため)。
    """
    if is_forbidden_text(text):
        raise RuntimeError(f"安全のため '{text}' のクリックは拒否しました(予約確定系の疑いがある文言)")

    timeout_ms = timeout_ms or config.ACTION_TIMEOUT_MS
    candidates = [
        lambda: page.get_by_role("button", name=text, exact=False),
        lambda: page.get_by_role("radio", name=text, exact=False),
        lambda: page.get_by_role("checkbox", name=text, exact=False),
        lambda: page.get_by_role("link", name=text, exact=False),
        lambda: page.get_by_label(text, exact=False),
        lambda: page.get_by_text(text, exact=False),
    ]
    for make_locator in candidates:
        try:
            loc = make_locator().first
            loc.wait_for(state="visible", timeout=timeout_ms)
            el_text = (loc.inner_text() or "").strip()
            if is_forbidden_text(el_text):
                continue
            loc.click(timeout=timeout_ms)
            time.sleep(config.ACTION_DELAY_SEC)
            return True
        except PWTimeout:
            continue
        except Exception:
            continue
    return False


def find_next_month_button(page: Page):
    """カレンダーの「翌月へ」ボタンらしき要素を探す(複数の文言候補を試す)。"""
    label_candidates = ["翌月", "次月", "次へ", "→", ">", "Next"]
    for text in label_candidates:
        try:
            loc = page.get_by_role("button", name=text, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def get_calendar_day_cells(page: Page):
    """
    カレンダー上の「選択可能な日付セル」を返す。
    実際のマークアップが不明なため、複数の候補セレクタを順に試す。
    """
    selector_candidates = [
        "[role='gridcell']:not([aria-disabled='true'])",
        "td.is-selectable, td.selectable, td:not(.disabled):not(.is-disabled)",
        "button.calendar-day:not([disabled])",
        ".calendar-day:not(.disabled):not(.is-disabled)",
    ]
    for sel in selector_candidates:
        try:
            loc = page.locator(sel)
            count = loc.count()
            if count > 0:
                return loc
        except Exception:
            continue
    return None


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

    ok = click_by_text(page, config.STEP_EXAM_TYPE_TEXT)
    if not ok:
        log(f"[{venue}] '{config.STEP_EXAM_TYPE_TEXT}' の選択肢が見つかりませんでした")
        dump_debug(page, f"{venue}_01_exam_type_notfound")
        return results
    dump_debug(page, f"{venue}_01_exam_type")

    ok = click_by_text(page, config.STEP_LICENSE_FORM_TEXT)
    if not ok:
        log(f"[{venue}] '{config.STEP_LICENSE_FORM_TEXT}' の選択肢が見つかりませんでした")
        dump_debug(page, f"{venue}_02_license_form_notfound")
        return results
    dump_debug(page, f"{venue}_02_license_form")

    ok = click_by_text(page, venue)
    if not ok:
        log(f"[{venue}] 試験場の選択肢が見つかりませんでした")
        dump_debug(page, f"{venue}_03_venue_notfound")
        return results
    dump_debug(page, f"{venue}_03_venue_selected")

    for month_offset in range(config.MONTHS_AHEAD):
        if month_offset > 0:
            nxt = find_next_month_button(page)
            if nxt is None:
                log(f"[{venue}] 翌月ボタンが見つからず、{month_offset}ヶ月目までで打ち切ります")
                break
            try:
                nxt.click(timeout=config.ACTION_TIMEOUT_MS)
                time.sleep(config.ACTION_DELAY_SEC)
            except Exception as e:  # noqa: BLE001
                log(f"[{venue}] 翌月ボタンのクリックに失敗: {e}")
                break

        dump_debug(page, f"{venue}_month{month_offset}_calendar")

        cells = get_calendar_day_cells(page)
        if cells is None:
            log(f"[{venue}] 月{month_offset}: カレンダーの日付セルが見つかりませんでした")
            continue

        cell_count = cells.count()
        log(f"[{venue}] 月{month_offset}: 選択可能な日付セル {cell_count} 件を確認")

        for i in range(cell_count):
            cell = cells.nth(i)
            try:
                cell_text = (cell.inner_text() or "").strip()
            except Exception:
                continue
            if is_forbidden_text(cell_text):
                continue
            try:
                cell.click(timeout=config.ACTION_TIMEOUT_MS)
                time.sleep(config.ACTION_DELAY_SEC)
            except Exception:
                continue

            try:
                panel_text = page.locator("body").inner_text()
            except Exception:
                panel_text = ""

            seats = extract_seats_from_panel(panel_text)
            date_str = extract_current_date_label(page, cell_text)

            if date_str and (seats["am"] is not None or seats["pm"] is not None):
                results.setdefault(date_str, {})
                if seats["am"] is not None:
                    results[date_str]["am"] = seats["am"]
                if seats["pm"] is not None:
                    results[date_str]["pm"] = seats["pm"]
            else:
                dump_debug(page, f"{venue}_month{month_offset}_day{i}_unparsed")

    return results


def extract_current_date_label(page: Page, cell_text: str) -> str:
    """
    画面上に表示されている年月見出し + クリックしたセルの日付テキストから
    'YYYY-MM-DD' を組み立てる。見出しの取得方法もサイト構造が不明なため
    複数候補を試す簡易実装。うまく取れない場合は空文字を返す。
    """
    day_match = re.search(r"(\d{1,2})", cell_text)
    if not day_match:
        return ""
    day = int(day_match.group(1))

    header_candidates = [
        "h1", "h2", "h3", ".calendar-header", ".month-label", "[class*='month']",
    ]
    year, month = None, None
    for sel in header_candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            text = loc.inner_text()
            m = re.search(r"(\d{4})\D+(\d{1,2})", text)
            if m:
                year, month = int(m.group(1)), int(m.group(2))
                break
        except Exception:
            continue

    if year is None or month is None:
        now = datetime.now(JST)
        year, month = now.year, now.month

    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except Exception:
        return ""


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
