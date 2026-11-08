# -*- coding: utf-8 -*-
"""keepalive 真值记账回归锁。

锁定前序(2026-11-04)修复的核心不变量：
  - 保活成功只认「消息真实经 iLink 送达」(channel=="ILINK")；
  - 当 iLink 凭证失效、仅 PushPlus 兜底成功时，禁止把兜底成功记成 iLink 成功
    （旧实现曾用整体 ok 误记，导致 iLink 会话已死却显示 OK 的「生产者自欺」）。

全程使用 unittest.mock，不触达真实 send / 不写真实 push_keepalive_state 文件。
可用 `python research/test_keepalive_truth.py` 或 `python -m unittest` 运行。
"""
import os
import sys
import types
import unittest
from contextlib import contextmanager

# 让脚本在 research/ 子目录下也能 import 仓库根的 push_channel
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import push_channel  # noqa: E402


class _KasMock:
    """记录 record_ok / record_fail 的调用，替代真实状态写回。"""
    def __init__(self):
        self.ok_calls = []
        self.fail_calls = []

    def record_ok(self, ts, channel=None):
        self.ok_calls.append(channel)

    def record_fail(self, channel=None):
        self.fail_calls.append(channel)

    def gap_hours(self, channel=None):
        return 99.0  # 强制进入「需要保活」分支

    def fail_streak(self, channel=None):
        return 0


@contextmanager
def _mocks(send_return):
    """把 send 与 push_keepalive_state 模块同时替换为 mock，退出时还原。"""
    kas = _KasMock()
    fake_mod = types.ModuleType("push_keepalive_state")
    fake_mod.record_ok = kas.record_ok
    fake_mod.record_fail = kas.record_fail
    fake_mod.gap_hours = kas.gap_hours
    fake_mod.fail_streak = kas.fail_streak

    orig_send = push_channel.send
    orig_mod = sys.modules.get("push_keepalive_state")
    push_channel.send = lambda *a, **k: send_return
    sys.modules["push_keepalive_state"] = fake_mod
    try:
        yield kas
    finally:
        push_channel.send = orig_send
        if orig_mod is None:
            sys.modules.pop("push_keepalive_state", None)
        else:
            sys.modules["push_keepalive_state"] = orig_mod


class TestKeepaliveTruth(unittest.TestCase):

    def test_ilink_ok_records_ilink_success(self):
        with _mocks((True, "ILINK", "ok", 1)) as kas:
            status, _ = push_channel.keepalive(wechat=True)
        self.assertEqual(status, "ok")
        self.assertIn("ILINK", kas.ok_calls)
        self.assertEqual(kas.fail_calls, [])

    def test_pushplus_fallback_must_not_impersonate_ilink(self):
        """核心不变量：兜底(PushPlus)成功不得记成 iLink 成功。"""
        with _mocks((True, "PUSHPLUS", "fallback only", 1)) as kas:
            status, detail = push_channel.keepalive(wechat=True)
        self.assertEqual(status, "fail")
        self.assertNotIn("ILINK", kas.ok_calls)   # 关键：不得记 iLink 成功
        self.assertIn("ILINK", kas.fail_calls)     # 必须计为 iLink 失败
        self.assertIn("PUSHPLUS", detail)          # 必须暴露「仅兜底」事实

    def test_send_failure_records_ilink_fail(self):
        with _mocks((False, "", "network error", 1)) as kas:
            status, _ = push_channel.keepalive(wechat=True)
        self.assertEqual(status, "fail")
        self.assertNotIn("ILINK", kas.ok_calls)
        self.assertIn("ILINK", kas.fail_calls)

    def test_wechat_disabled_skips(self):
        with _mocks((True, "ILINK", "ok", 1)) as kas:
            status, _ = push_channel.keepalive(wechat=False)
        self.assertEqual(status, "skipped")
        self.assertEqual(kas.ok_calls, [])
        self.assertEqual(kas.fail_calls, [])


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestKeepaliveTruth)
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)
