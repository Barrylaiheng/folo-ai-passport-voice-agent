#!/usr/bin/env python3
"""udp_transport 同局域网冲突自检(情况二:一台设备多台电脑)的单元测试。

不需要真实网卡 / 设备: 直接构造 UdpTransport 并固定本机 IP 集合,
校验 _check_foreign_beacon 的"提醒 / 限频 / 回环忽略 / 已隔离不提醒"四种行为。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))
import udp_transport as ut  # noqa: E402


def _make(isolated):
    t = ut.UdpTransport() if not isolated else ut.UdpTransport(device_ip="192.168.1.9")
    # 固定本机 IP 集合,避免依赖真实网卡
    t._local_ips = {"192.168.1.5"}
    t._foreign_warn_ts = 0.0
    return t


class ForeignBeaconTest(unittest.TestCase):
    def test_foreign_beacon_warns(self):
        import contextlib
        import io
        t = _make(isolated=False)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            t._check_foreign_beacon("10.0.0.99")
        out = buf.getvalue()
        self.assertIn("另一台电脑", out)
        self.assertIn("10.0.0.99", out)

    def test_foreign_loopback_ignored(self):
        t = _make(isolated=False)
        # 本机 IP 当作来源 → 应忽略,不打印任何东西
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            t._check_foreign_beacon("192.168.1.5")
        self.assertEqual(buf.getvalue().strip(), "")

    def test_isolated_no_warn(self):
        t = _make(isolated=True)  # 已单设备隔离,不提醒
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            t._check_foreign_beacon("10.0.0.99")
        self.assertEqual(buf.getvalue().strip(), "")

    def test_throttled(self):
        t = _make(isolated=False)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            t._check_foreign_beacon("10.0.0.99")   # 第一次:打印
            first = buf.getvalue()
            buf.truncate(0)
            buf.seek(0)
            t._check_foreign_beacon("10.0.0.99")   # 15s 内:限频不打印
            second = buf.getvalue()
        self.assertIn("另一台电脑", first)
        self.assertEqual(second.strip(), "")


if __name__ == "__main__":
    unittest.main()
