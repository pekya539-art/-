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

# カレンダーは表示された直後にはまだ空き情報が反映されていないことがある。
CALENDAR_RETRY_ATTEMPTS = 6
CALENDAR_RETRY_INTERVAL_SEC = 1.0

# 残席パネル(午前/午後 残り○名)は日付クリック後に非同期で更新される。
# 更新前に読むと「残り0名」と誤判定して通知が出なくなるため、
# 「残り○名」が現れるまで読み直す。
SEAT_PANEL_ATTEMPTS = 8
SEAT_PANEL_INTERVAL_SEC = 0.6


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


def settle(page: Page, timeout_ms: int = 8000) -> None:
    """進行中の通信が落ち着くまで待つ(空き情報の反映待ち)。"""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PWTimeout:
        pass


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


def
