# -*- coding: utf-8 -*-
"""
ntfy.sh への通知処理。

ntfy はアカウント登録・サーバー設定が不要な通知サービス。
「トピック名」という好きな文字列を1つ決めて、スマホのntfyアプリでその
トピックを購読(subscribe)しておくだけで、ここから https://ntfy.sh/<トピック名>
宛てにPOSTしたメッセージがプッシュ通知として届く。
"""
import urllib.request
import urllib.error

import config


def send_notification(text: str, priority: str = "default") -> bool:
    """
    ntfy.sh にメッセージを送信する。成功したら True。

    priority: "default" / "high" / "urgent" など(ntfyの通知の強さ)。
    """
    if not config.NTFY_TOPIC:
        print("[notify] NTFY_TOPIC が未設定のため通知をスキップします。")
        return False

    url = f"https://ntfy.sh/{config.NTFY_TOPIC}"
    body = text.encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Priority": priority,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[notify] ntfy への送信に失敗しました status={resp.status}")
            return ok
    except urllib.error.HTTPError as e:
        print(f"[notify] ntfy への送信でHTTPエラー: {e.code} {e.read()}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[notify] ntfy への送信で例外: {e}")
        return False


def build_vacancy_message(venue: str, date_str: str, ampm_label: str, seats: int) -> str:
    """空席発生通知の本文を組み立てる。"""
    return (
        "🚨 本免学科試験のキャンセル発生\n"
        f"試験場: {venue}\n"
        f"日付: {date_str}\n"
        f"時間帯: {ampm_label}\n"
        f"残席数: {seats}名\n"
        f"予約サイト: {config.START_URL}\n"
        "※ このシステムは検知のみ行います。予約はご自身で早めに操作してください。"
    )


def build_error_message(detail: str) -> str:
    return f"⚠️ 本免学科試験 監視システムでエラーが発生しました\n{detail}"
