# -*- coding: utf-8 -*-
"""一次性工具: 把传入路径送进 Windows 回收站(非永久删除)。
用法: python recycle_to_bin.py <path1> [path2 ...]
"""
import ctypes
from ctypes import wintypes
import sys

FO_DELETE = 3
FOF_ALLOWUNDO = 0x40       # 允许撤销 = 进回收站
FOF_NOCONFIRMATION = 0x10  # 不弹确认框
FOF_SILENT = 0x4
FOF_NOERRORUI = 0x400


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_short),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p)]


def recycle(paths):
    # pFrom 要求: 多路径用 \0 分隔, 整体以 \0\0 结尾
    src = "\0".join(paths) + "\0\0"
    buf = ctypes.create_unicode_buffer(src)
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_wchar_p)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc, op.fAnyOperationsAborted


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.strip()]
    if not args:
        print("no paths given")
        sys.exit(2)
    rc, aborted = recycle(args)
    for p in args:
        print(("still-exists: " if __import__("os").path.exists(p)
               else "recycled: ") + p)
    print("rc=" + str(rc) + " aborted=" + str(bool(aborted)))
    sys.exit(rc)
