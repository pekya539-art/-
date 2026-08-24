# -*- coding: utf-8 -*-
"""
前回チェック結果を保存し、「0名 -> 1名以上」の遷移だけを検知して
重複通知を防ぐための状態管理。

state.json の構造:
{
  "slots": {
    "府中試験場|2026-09-30|am": {
      "seats": 9,          # 直近で観測した残席数
      "notified": true     # このエピソード(0からの空き)について通知済みか
    },
    ...
  },
  "updated_at": "2026-08-24T12:00:00+09:00"
}
"""
import json
import os
from datetime import datetime, timezone, timedelta

import config

JST = timezone(timedelta(hours=9))


def load_state() -> dict:
    if not os.path.exists(config.STATE_FILE):
        return {"slots": {}, "updated_at": None}
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("slots", {})
            return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[state] state.json の読み込みに失敗したため初期化します: {e}")
        return {"slots": {}, "updated_at": None}


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.now(JST).isoformat()
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def slot_key(venue: str, date_str: str, ampm: str) -> str:
    return f"{venue}|{date_str}|{ampm}"


def evaluate_slot(state: dict, venue: str, date_str: str, ampm: str, seats: int) -> bool:
    """
    最新の残席数を state に反映し、「今回通知すべきかどうか」を返す。

    ロジック:
      - 前回 seats == 0 (or 未観測) かつ 今回 seats >= 1  -> 新規の空き発生
        - まだ notified フラグが立っていなければ通知対象とし、notified=True にする
      - 今回 seats == 0                                    -> notified フラグを解除
        (次に空きが出たときにまた通知できるようにするため)
      - それ以外(空きが続いている等)                        -> 通知しない
    """
    slots = state["slots"]
    key = slot_key(venue, date_str, ampm)
    prev = slots.get(key, {"seats": 0, "notified": False})
    prev_seats = prev.get("seats", 0)
    prev_notified = prev.get("notified", False)

    should_notify = False

    if seats <= 0:
        # 満席(0名)に戻った -> 次の空きに備えてリセット
        slots[key] = {"seats": 0, "notified": False}
    else:
        if prev_seats <= 0 and not prev_notified:
            should_notify = True
        slots[key] = {"seats": seats, "notified": True if should_notify or prev_notified else prev_notified}
        # 通知した場合、または既に通知済みで空きが継続している場合は notified=True を維持
        if should_notify:
            slots[key]["notified"] = True

    return should_notify
