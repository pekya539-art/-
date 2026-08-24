# -*- coding: utf-8 -*-
"""
ネットワークアクセスを伴わない純粋ロジックの検証用テスト。
(実サイトのDOM構造に依存する部分=collect_venue_results 等は対象外)
"""
import os
import tempfile

import config
import state_store
from scraper import extract_seats_from_panel, is_forbidden_text


def test_extract_seats_basic():
    text = "2026年9月30日\n午前試験 → 残り9名\n午後試験 → 残り0名"
    result = extract_seats_from_panel(text)
    assert result == {"am": 9, "pm": 0}, result


def test_extract_seats_full_both():
    text = "午前試験 従来の免許証 残り0名\n午後試験 従来の免許証 残り0名"
    result = extract_seats_from_panel(text)
    assert result == {"am": 0, "pm": 0}, result


def test_extract_seats_am_only():
    text = "午前試験 残り3名"
    result = extract_seats_from_panel(text)
    assert result["am"] == 3 and result["pm"] is None, result


def test_forbidden_text_guard():
    assert is_forbidden_text("この内容で予約する")
    assert is_forbidden_text("確定")
    assert not is_forbidden_text("府中試験場")
    assert not is_forbidden_text("免許証のみ")


def test_dedup_notifies_on_0_to_1():
    with tempfile.TemporaryDirectory() as d:
        config.STATE_FILE = os.path.join(d, "state.json")
        state = {"slots": {}}

        # 初回: 0名 -> 通知しない
        notify1 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "am", 0)
        assert notify1 is False

        # 0名 -> 9名: 通知する
        notify2 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "am", 9)
        assert notify2 is True

        # 9名 -> 5名(まだ空きがある): 再通知しない
        notify3 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "am", 5)
        assert notify3 is False

        # 5名 -> 0名(埋まった): 通知しない、フラグはリセットされる
        notify4 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "am", 0)
        assert notify4 is False
        assert state["slots"]["府中試験場|2026-09-30|am"]["notified"] is False

        # 再度 0名 -> 3名: 新しいエピソードとして再通知する
        notify5 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "am", 3)
        assert notify5 is True


def test_dedup_independent_slots():
    state = {"slots": {}}
    n1 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "am", 2)
    n2 = state_store.evaluate_slot(state, "府中試験場", "2026-09-30", "pm", 0)
    n3 = state_store.evaluate_slot(state, "鮫洲試験場", "2026-09-30", "am", 1)
    assert n1 is True
    assert n2 is False
    assert n3 is True


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"OK: {t.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")


if __name__ == "__main__":
    run_all()
