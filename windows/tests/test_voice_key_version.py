# -*- coding: utf-8 -*-
"""版本感知语音热键(_resolve_voice_key)的单元测试。

背景: WorkBuddy 5.8.3 把「语音录音开关」默认热键从 ctrl+d 改成单独 alt,
转发器需按前台 Agent 的 exe 版本自动选择注入键, 用户零操作。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotkey_forwarder as hf  # noqa: E402
from hotkey_forwarder import (  # noqa: E402
    BUILTIN_VOICE_KEY_RULES,
    Forwarder,
    _parse_version,
)


def _stub(version_by_path=None):
    """构造只带版本解析与长按注入的最小宿主。"""
    class _Host:
        _ver_cache = {}

        _exe_version = Forwarder._exe_version
        _resolve_voice_key = Forwarder._resolve_voice_key
        _resolve_voice_key_mode = Forwarder._resolve_voice_key_mode
        _match_voice_rule = staticmethod(Forwarder._match_voice_rule)
        _press_hold_voice_key = Forwarder._press_hold_voice_key
        _release_held_voice_key = Forwarder._release_held_voice_key

    host = _Host()
    host._ver_cache = dict(version_by_path or {})
    return host


class TestParseVersion(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(_parse_version("5.8.3"), (5, 8, 3, 0))

    def test_two_segments(self):
        self.assertEqual(_parse_version("5.8"), (5, 8, 0, 0))

    def test_suffix_ignored(self):
        self.assertEqual(_parse_version("5.8.3-beta"), (5, 8, 3, 0))

    def test_garbage(self):
        self.assertEqual(_parse_version("abc"), (0, 0, 0, 0))
        self.assertEqual(_parse_version(""), (0, 0, 0, 0))
        self.assertEqual(_parse_version(None), (0, 0, 0, 0))

    def test_numeric_compare(self):
        # 逐段数值比较, 不是字符串比较("10" < "9" 的坑)
        self.assertGreater(_parse_version("10.0.0"), _parse_version("9.9.9"))


class TestFileVersion(unittest.TestCase):
    def test_real_system_exe(self):
        from hotkey_forwarder import _file_version
        # notepad.exe 一定带版本资源(windows 系统文件)
        v = _file_version(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                       "System32", "notepad.exe"))
        self.assertTrue(v, "系统文件应能读到版本")
        self.assertEqual(len(v), 4)
        self.assertTrue(all(isinstance(x, int) for x in v))

    def test_missing_file(self):
        from hotkey_forwarder import _file_version
        self.assertEqual(_file_version(r"C:\nope\missing.exe"), ())


class TestResolveVoiceKey(unittest.TestCase):
    WB = {"title_contains": "WorkBuddy", "voice_key": "ctrl+d"}

    def test_old_version_uses_config_key(self):
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 2, 0)})
        vk = host._resolve_voice_key(self.WB, r"C:\Program Files\WorkBuddy\WorkBuddy.exe")
        self.assertEqual(vk, "ctrl+d")

    def test_583_switches_to_alt(self):
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 3, 0)})
        vk = host._resolve_voice_key(self.WB, r"C:\Program Files\WorkBuddy\WorkBuddy.exe")
        self.assertEqual(vk, "alt")

    def test_newer_version_also_switches(self):
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (6, 0, 0, 0)})
        vk = host._resolve_voice_key(self.WB, r"C:\Program Files\WorkBuddy\WorkBuddy.exe")
        self.assertEqual(vk, "alt")

    def test_no_version_falls_back(self):
        # 用不存在的路径: _file_version 返回 () → 回落 voice_key(不猜)
        host = _stub()
        vk = host._resolve_voice_key(self.WB, r"C:\nope\WorkBuddy\WorkBuddy.exe")
        self.assertEqual(vk, "ctrl+d")

    def test_real_workbuddy_exe_adapts(self):
        # 真机验证: 本机 A 机 WorkBuddy 已升 5.8.3 → 应解析出 alt
        from hotkey_forwarder import _file_version
        real = r"C:\Program Files\WorkBuddy\WorkBuddy.exe"
        if not os.path.exists(real):
            self.skipTest("本机无 WorkBuddy 安装")
        ver = _file_version(real)
        host = _stub({real: ver})
        vk = host._resolve_voice_key(self.WB, real)
        expected = "alt" if ver >= (5, 8, 3, 0) else "ctrl+d"
        self.assertEqual(vk, expected)

    def test_empty_path_falls_back(self):
        host = _stub()
        vk = host._resolve_voice_key(self.WB, "")
        self.assertEqual(vk, "ctrl+d")

    def test_explicit_rules_win_over_builtin(self):
        app = dict(self.WB, voice_key_rules=[{"since": "5.9.0", "key": "f9"}])
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 3, 0)})
        # 5.8.3 < 5.9.0 → 规则未命中 → 回落 voice_key
        self.assertEqual(host._resolve_voice_key(app, r"C:\Program Files\WorkBuddy\WorkBuddy.exe"),
                         "ctrl+d")
        host2 = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 9, 0, 0)})
        self.assertEqual(host2._resolve_voice_key(app, r"C:\Program Files\WorkBuddy\WorkBuddy.exe"),
                         "f9")

    def test_empty_rules_disables_adaptation(self):
        # 逃生舱: 用户在应用里改过热键的, 配 "voice_key_rules": [] 锁定 voice_key
        app = dict(self.WB, voice_key_rules=[])
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 3, 0)})
        self.assertEqual(host._resolve_voice_key(app, r"C:\Program Files\WorkBuddy\WorkBuddy.exe"),
                         "ctrl+d")

    def test_non_builtin_app_untouched(self):
        # Trae 无内置规则, 任何版本都用配置键
        trae = {"title_contains": "Trae", "voice_key": "alt+v"}
        host = _stub({r"C:\Program Files\Trae CN\Trae CN.exe": (9, 9, 9, 9)})
        self.assertEqual(host._resolve_voice_key(trae, r"C:\Program Files\Trae CN\Trae CN.exe"),
                         "alt+v")

    def test_builtin_table_covers_workbuddy(self):
        self.assertIn("workbuddy.exe", BUILTIN_VOICE_KEY_RULES)
        rule = BUILTIN_VOICE_KEY_RULES["workbuddy.exe"][0]
        self.assertEqual(rule["key"], "alt")
        self.assertEqual(rule["mode"], "hold")  # 5.8.3 是"长按"手势, 非 tap


class TestVoiceKeyMode(unittest.TestCase):
    WB = {"title_contains": "WorkBuddy", "voice_key": "ctrl+d"}

    def test_583_mode_is_hold(self):
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 3, 0)})
        self.assertEqual(host._resolve_voice_key_mode(
            self.WB, r"C:\Program Files\WorkBuddy\WorkBuddy.exe"), "hold")

    def test_old_version_mode_is_tap(self):
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 2, 0)})
        self.assertEqual(host._resolve_voice_key_mode(
            self.WB, r"C:\Program Files\WorkBuddy\WorkBuddy.exe"), "tap")

    def test_explicit_mode_overrides(self):
        app = dict(self.WB, voice_key_mode="tap")
        host = _stub({r"C:\Program Files\WorkBuddy\WorkBuddy.exe": (5, 8, 3, 0)})
        self.assertEqual(host._resolve_voice_key_mode(
            app, r"C:\Program Files\WorkBuddy\WorkBuddy.exe"), "tap")


class TestHoldInjection(unittest.TestCase):
    """长按型语音键: 开录按下保持, 停录抬起; 断链兜底释放。"""

    def setUp(self):
        self.sent = []
        p1 = mock.patch.object(hf, "_send_key",
                               side_effect=lambda *a, **kw: self.sent.append((a, kw)))
        p2 = mock.patch.object(hf.time, "sleep")
        p3 = mock.patch.object(hf, "attach_to_default_desktop")
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        self.host = _stub()
        self.host._vk_held = None

    def test_start_holds_key_down(self):
        self.host._press_hold_voice_key("alt", "开始语音(UP按下)")
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.sent[0][1]["up"])
        self.assertIsNotNone(self.host._vk_held)

    def test_stop_releases_key_up(self):
        self.host._press_hold_voice_key("alt", "开始语音(UP按下)")
        n = len(self.sent)
        self.host._release_held_voice_key("停止语音(UP松开)")
        self.assertEqual(len(self.sent), n + 1)
        self.assertTrue(self.sent[-1][1]["up"])
        self.assertIsNone(self.host._vk_held)

    def test_duplicate_start_ignored(self):
        self.host._press_hold_voice_key("alt", "开始语音(UP按下)")
        n = len(self.sent)
        self.host._press_hold_voice_key("alt", "开始语音(UP按下)")
        self.assertEqual(len(self.sent), n)  # 已按住不重复

    def test_stop_without_hold_noop(self):
        self.host._release_held_voice_key("停止语音(UP松开)")
        self.assertEqual(self.sent, [])

    def test_start_action_with_stop_word_releases(self):
        # press_hold 内部再收"停止"动作时转释放(防御性)
        self.host._press_hold_voice_key("alt", "开始语音(UP按下)")
        self.host._press_hold_voice_key("alt", "停止残留语音")
        self.assertTrue(self.sent[-1][1]["up"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
