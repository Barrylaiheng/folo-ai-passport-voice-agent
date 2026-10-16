#!/usr/bin/env python3
"""peer_coordinator 选举逻辑单元测试（纯函数，无网络）。"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from peer_coordinator import elect_winner, _ip_key  # noqa: E402


def _peer(focus_ai, idle, last_seen=None):
    # last_seen 默认"刚刚见过"（在线）；传 -999 表示离线（被 COORD_TIMEOUT 过滤）
    if last_seen is None:
        last_seen = time.monotonic()
    return {"focus_ai": focus_ai, "title": "", "idle": idle,
            "last_seen": last_seen}


class ElectWinnerTest(unittest.TestCase):
    def test_only_self_is_winner(self):
        # 表里只有自己（others 都已离线）
        peers = {"192.168.1.5": _peer(False, 100, last_seen=-999)}
        self.assertEqual(elect_winner("192.168.1.5", peers, None, False),
                         "192.168.1.5")

    def test_local_focus_wins_over_idle_other(self):
        peers = {
            "192.168.1.5": _peer(True, 0.1),   # 本地焦点 + 低 idle
            "192.168.1.6": _peer(False, 100),  # 远端非焦点
        }
        self.assertEqual(elect_winner("192.168.1.5", peers, None, False),
                         "192.168.1.5")

    def test_focus_ai_smallest_idle_wins(self):
        # 两台都焦点，但 .5 的 idle 更小（最近操作）→ .5 赢
        peers = {
            "192.168.1.5": _peer(True, 0.7),
            "192.168.1.6": _peer(True, 30.0),
        }
        self.assertEqual(elect_winner("192.168.1.6", peers, None, False),
                         "192.168.1.5")

    def test_tie_idle_breaks_by_smallest_ip(self):
        peers = {
            "192.168.1.6": _peer(True, 5.0),
            "192.168.1.5": _peer(True, 5.0),   # 同 idle，IP 更小 → 赢
        }
        self.assertEqual(elect_winner("192.168.1.9", peers, None, False),
                         "192.168.1.5")

    def test_no_focus_sticky_keeps_last_winner(self):
        peers = {
            "192.168.1.5": _peer(False, 100),
            "192.168.1.6": _peer(False, 100),
        }
        # 上一任赢家是 .6 且仍在线 → sticky 保持 .6
        self.assertEqual(elect_winner("192.168.1.5", peers, "192.168.1.6",
                                      False), "192.168.1.6")

    def test_no_focus_no_last_winner_picks_min_ip(self):
        peers = {
            "192.168.1.6": _peer(False, 100),
            "192.168.1.5": _peer(False, 100),
        }
        # 无人焦点且无记忆赢家 → IP 最小那台发 beacon（设备至少连一台）
        self.assertEqual(elect_winner("192.168.1.6", peers, None, False),
                         "192.168.1.5")

    def test_no_focus_last_winner_offline_falls_to_min_ip(self):
        # 上一任赢家 .6 已离线（last_seen 过期）→ 退回 min IP
        peers = {
            "192.168.1.6": _peer(False, 100, last_seen=-999),
            "192.168.1.5": _peer(False, 100),
        }
        self.assertEqual(elect_winner("192.168.1.5", peers, "192.168.1.6",
                                      False), "192.168.1.5")

    def test_recording_protects_local_when_was_winner(self):
        # 本机正在录音且曾是赢家 → 即便远端 idle 更小也保住本机
        peers = {
            "192.168.1.5": _peer(True, 0.1),
            "192.168.1.6": _peer(True, 0.05),   # 远端更近操作
        }
        self.assertEqual(elect_winner("192.168.1.5", peers, "192.168.1.5",
                                      recording=True), "192.168.1.5")

    def test_recording_no_protect_when_not_last_winner(self):
        # 本机录音但上一任赢家是别人 → 不保（防御：实际不会发生，因为非赢家不会收到设备事件）
        peers = {
            "192.168.1.5": _peer(True, 0.1),
            "192.168.1.6": _peer(True, 0.05),
        }
        self.assertEqual(elect_winner("192.168.1.5", peers, "192.168.1.6",
                                      recording=True), "192.168.1.6")

    def test_ip_key_ordering(self):
        self.assertLess(_ip_key("192.168.1.5"), _ip_key("192.168.1.6"))
        self.assertLess(_ip_key("10.0.0.2"), _ip_key("192.168.1.1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
