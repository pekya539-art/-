# -*- coding: utf-8 -*-
"""
監視対象・設定値をまとめたファイル。
値の変更はここだけで完結するようにしてある。
"""

import os

# 対象サイト(本免学科試験 予約確認カレンダー・ログイン不要)
START_URL = "https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/calendar/01/html/main.html?lang=ja"

# サイト側が「リファラ(参照元)が空だとブロックする」仕様のため、
# 同一サイト内のURLをリファラとして明示的に送る。
REFERER = "https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/"

# 画面遷移で選択するテキスト(サイト上の表示文言そのまま)
STEP_EXAM_TYPE_TEXT = "教習所卒業等"          # 受験する項目を選択
STEP_LICENSE_FORM_TEXT = "免許証のみ"          # 希望する免許保有形態

# 監視する試験場(表示名はサイト上の文言と一致させること)
VENUES = [
    "府中試験場",
    "鮫洲試験場",
    "江東試験場",
]

# 何ヶ月先まで見るか(現状は1ヶ月先まで埋まっている想定なので少し余裕を持たせる)
MONTHS_AHEAD = 2

# 残席テキストのパターン 例:「従来の免許証 残り9名」「残り0名」
SEAT_TEXT_REGEX = r"残り\s*(\d+)\s*名"

# 午前/午後の判定に使うテキスト
AM_TEXT = "午前"
PM_TEXT = "午後"

# 状態保存ファイル(0→1以上の遷移を検知するための前回結果を保存する)
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# 通知先 (Discord Webhook URL は GitHub Actions の Secrets 経由で環境変数に渡す)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# デバッグモード: 1 にするとステップごとにスクリーンショット/HTMLを artifacts/ に保存する
DEBUG = os.environ.get("DEBUG", "0") == "1"

# 1操作ごとに入れる待機時間(秒)。サイトへの負荷を抑えるための最低限のマナー。
ACTION_DELAY_SEC = float(os.environ.get("ACTION_DELAY_SEC", "1.5"))

# 試験場を切り替えるたびに入れる待機時間(秒)
VENUE_DELAY_SEC = float(os.environ.get("VENUE_DELAY_SEC", "2.0"))

# Playwright のタイムアウト(ミリ秒)
NAV_TIMEOUT_MS = 20000
ACTION_TIMEOUT_MS = 8000

# 予約操作は一切行わない(このシステムの絶対条件)
# -> クリックするのは「画面遷移用の選択肢」と「日付セル」のみ。
#    「予約する」「進む」に類する確定系ボタンは絶対にクリックしないこと。
FORBIDDEN_BUTTON_TEXT_HINTS = ["予約する", "確定", "申込", "この内容で予約"]
