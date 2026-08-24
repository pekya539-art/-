# -*- coding: utf-8 -*-
"""
ntfy.sh への通知が正しく設定されているかどうかだけを確認するための
簡易テストスクリプト。実際のサイトへはアクセスしない。
"""
import sys

import notify


def main():
    ok = notify.send_notification(
        "🔔 テスト通知\n"
        "本免学科試験キャンセル監視システムからのテストメッセージです。\n"
        "これがスマホに届いていれば、通知の設定は正常です。",
        priority="default",
    )
    if ok:
        print("[test_notify] 送信に成功しました。ntfyアプリの通知を確認してください。")
    else:
        print("[test_notify] 送信に失敗しました。NTFY_TOPIC の設定を確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
