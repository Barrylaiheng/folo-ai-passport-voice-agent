#!/usr/bin/env python3
"""beacon_gate 接入集成测试：验证 udp_transport 在多机协调模式下的持票/让位行为。

覆盖：
  - set_beacon_gate 能写入门控回调；
  - 非持票方(write_gatt_char 时 _device 为 None)静默丢弃、不抛错(避免重连风暴)；
  - _check_foreign_beacon 在 auto_switch(beacon_gate 已设)下跳过"其他电脑 beacon"误报；
  - _check_foreign_beacon 在单台/未隔离且来着陌生 IP 时正常告警。
"""

import asyncio
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from udp_transport import UdpTransport  # noqa: E402


def _make(gate=None, device_ip=None):
    t = UdpTransport(device_wait_timeout=0.1, beacon_gate=gate,
                     device_ip=device_ip)
    return t


class BeaconGateTest(unittest.TestCase):
    def test_set_beacon_gate_stores_callable(self):
        t = _make()
        called = {"n": 0}

        def gate():
            called["n"] += 1
            return True

        t.set_beacon_gate(gate)
        self.assertTrue(t._beacon_gate())
        self.assertEqual(called["n"], 1)

    def test_write_gatt_char_silent_when_no_device(self):
        # 非持票方尚未连上设备(_device=None)，下行应静默丢弃而非抛 UdpError
        t = _make(gate=lambda: False)
        try:
            t.write_gatt_char("uuid-x", b"\x01\x02")
        except Exception as e:  # pragma: no cover
            self.fail(f"write_gatt_char 不应抛异常: {e}")

    def test_foreign_beacon_suppressed_in_auto_switch(self):
        # auto_switch 模式(beacon_gate 已设)：收到陌生源 beacon 不应误报冲突
        t = _make(gate=lambda: True)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            t._check_foreign_beacon("10.99.99.99")
        self.assertNotIn("检测到同一局域网内另一台电脑", buf.getvalue())

    def test_foreign_beacon_warns_when_isolated_off_and_no_gate(self):
        # 单台/未隔离且无门控：陌生源 beacon 应告警(节流已重置)
        t = _make(gate=None, device_ip=None)
        foreign = "10.88.77.66"
        if foreign in t._local_ips:
            self.skipTest("测试机恰占用该 IP,跳过")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            t._check_foreign_beacon(foreign)
        self.assertIn("检测到同一局域网内另一台电脑", buf.getvalue())

    def test_gate_decorates_transport_flag(self):
        t = _make(gate=lambda: False)
        self.assertIsNotNone(t._beacon_gate)
        self.assertFalse(t._beacon_gate())


if __name__ == "__main__":
    unittest.main(verbosity=2)
