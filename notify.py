# -*- coding: utf-8 -*-
"""
Discord Webhook への通知処理。
"""
import json
import urllib.request
import urllib.error

import config


def send_discord_message(text: str) -> bool:
    """Discord Webhook にメッセージを送信する。成功したら True。"""
    if not config.DISCORD_WEBHOOK_URL:
        print("[notify] DISCORD_WEBHOOK_URL が未設定のため通知をスキップします。")
        return False

    payload = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(
        config.DISCORD_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[notify] Discord への送信に失敗しました status={resp.status}")
            return ok
    except urllib.error.HTTPError as e:
        print(f"[notify] Discord への送信でHTTPエラー: {e.code} {e.read()}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[notify] Discord への送信で例外: {e}")
        return False


def build_vacancy_message(venue: str, date_str: str, ampm_label: str, seats: int) -> str:
    """空席発生通知の本文を組み立てる。"""
    return (
        "🚨 **本免学科試験のキャンセル発生**\n"
        f"試験場: {venue}\n"
        f"日付: {date_str}\n"
        f"時間帯: {ampm_label}\n"
        f"残席数: {seats}名\n"
        f"予約サイト: {config.START_URL}\n"
        "※ このシステムは検知のみ行います。予約はご自身で早めに操作してください。"
    )


def build_error_message(detail: str) -> str:
    return f"⚠️ 本免学科試験 監視システムでエラーが発生しました\n{detail}"
