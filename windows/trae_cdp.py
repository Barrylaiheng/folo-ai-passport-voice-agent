"""AI Passport · TRAE(Chromium/Electron webview) 专用 CDP 驱动。

为什么需要它: TRAE 是 VS Code 分支, AI 聊天框是 Chromium webview。
它的语音键(Alt+V)只监听"聊天输入框已聚焦"的上下文, 而我们自动切回窗口时
只把窗口提到前台、没把输入框 DOM 焦点拉回来 → 注入 Alt+V 无效(UIA 在
Chromium 上取不到控件)。SendInput/UIA 对 webview 都是 no-op(社区与 dev.to
的 Chromium 输入专题都印证此坑)。

正解(CDP, 与 Cursor/VS Code 自动化工具同构): 用 --remote-debugging-port
启动 TRAE, 连上后直接点聊天框里的「麦克风」按钮 —— 按钮点击不受焦点上下文
限制, 一次点开录、再点一次停。彻底绕开焦点问题。
"""
import asyncio
import json
import urllib.request
import websockets


def _pick_target(targets):
    """从 /json 列表里挑出 TRAE 工作台主页面。

    实测(2026-10-14) TRAE 的 /json 只有 2 个 page:
      - floatingButton.html  (小悬浮按钮窗, 陷阱!)
      - Agent [管理员]        (真正的工作台, url=vscode-file://vscode-app/...)
    必须连工作台, 否则 Runtime.evaluate 跑在错误页面里, 找不到聊天框麦克风。

    教训(2026-10-15): 其他 Chromium 程序会占用同一个调试端口(如 PyQt 应用
    写死 --remote-debugging-port=9222), /json 里会出现 file:// 预览页; 旧的
    "任意非 webview page"兜底会误连它, 点不到麦克风 → 严格只认工作台特征。
    """
    pages = [t for t in targets
             if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    for t in pages:
        u = (t.get("url") or "").lower()
        if "floatingbutton" in u or "floatingbutton" in (t.get("title") or "").lower():
            continue
        if u.startswith("vscode-file://vscode-app") or "workbench" in u:
            return t
    return None


class CdpClient:
    def __init__(self, port=9222):
        self.port = port
        self.ws = None
        self._id = 0
        self._pending = {}
        self._lock = asyncio.Lock()
        self._reader = None
        self.mic_log = ""  # 最近一次点麦克风的诊断信息

    async def available(self):
        """探测 TRAE 是否以调试端口启动(同步探测一次 /json)。"""
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json", timeout=1.5) as r:
                json.loads(r.read().decode())
            return True
        except Exception:
            return False

    async def ensure(self):
        """(重)连接 TRAE 工作台页面, 成功返回 True。"""
        async with self._lock:
            if self.ws is not None:
                return True
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
                    targets = json.loads(r.read().decode())
                t = _pick_target(targets)
                if not t:
                    print("[cdp] 未找到 TRAE 工作台页面目标")
                    return False
                ws_url = t["webSocketDebuggerUrl"]
                self.ws = await websockets.connect(ws_url, max_size=None,
                                                  open_timeout=4, ping_timeout=8)
                self._reader = asyncio.ensure_future(self._pump())
                await self._cmd("Runtime.enable", {})
                print(f"[cdp] 已连接 TRAE 工作台: {t.get('title','')[:40]!r}")
                return True
            except Exception as e:
                print(f"[cdp] 连接失败(确认 TRAE 以 --remote-debugging-port="
                      f"{self.port} 启动?): {e}")
                self.ws = None
                return False

    async def _pump(self):
        try:
            async for msg in self.ws:
                try:
                    d = json.loads(msg)
                except Exception:
                    continue
                i = d.get("id")
                if i is not None and i in self._pending:
                    self._pending.pop(i).set_result(d)
        except Exception:
            # 连接断开: 清空, 下次 ensure 重连
            self.ws = None

    async def _cmd(self, method, params):
        if self.ws is None:
            raise RuntimeError("not connected")
        self._id += 1
        i = self._id
        fut = asyncio.get_event_loop().create_future()
        self._pending[i] = fut
        await self.ws.send(json.dumps({"id": i, "method": method,
                                       "params": params}))
        return await asyncio.wait_for(fut, timeout=6)

    async def evaluate(self, expr):
        return await self._cmd("Runtime.evaluate",
                               {"expression": expr, "returnByValue": True,
                                "awaitPromise": True})

    # ---- 实测选中的 TRAE 语音 DOM(2026-10-14 用 CDP 逐层 dump 出来的) ----
    # 聊天输入框 = div.chat-input-v2-input-box-editable (在"主文档"里, 不是 iframe)
    # 麦克风按钮 = button.rtcVoicePluginButton  (内含 svg.trae-icon-microphone)
    # 开录后弹出录音工具栏 .rtcVoiceRecordingToolbar, 内含:
    #   取消 = button.rtcVoiceToolbarBtn (带 codicon-close)
    #   完成 = button.rtcVoiceToolbarBtnFinish (带 codicon-icube-Check)
    SEL_MIC = "button.rtcVoicePluginButton"
    SEL_FINISH = "button.rtcVoiceToolbarBtnFinish"
    SEL_CANCEL = "button.rtcVoiceToolbarBtn:not(.rtcVoiceToolbarBtnFinish)"

    async def _eval_json(self, js):
        """跑一段返回 JSON 字符串的 JS, 解析成 dict。"""
        res = await self.evaluate(js)
        val = (res.get("result", {}).get("result", {}).get("value")
               if isinstance(res, dict) else None)
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return {"ok": False, "raw": val}
        return {"ok": False, "raw": str(val)}

    async def voice_start(self):
        """点 TRAE 聊天框麦克风按钮开始录音(先清掉可能残留的录音工具栏)。

        返回 {ok:True} / {ok:False, reason:...}。
        """
        js = r"""
        (() => {
          const q = s => document.querySelector(s);
          const vis = el => el && el.offsetParent !== null &&
                            getComputedStyle(el).display !== 'none';
          const dis = el => !el || el.disabled ||
                            (el.classList && el.classList.contains('disabled'));
          const MIC = 'button.rtcVoicePluginButton';
          const CAN = 'button.rtcVoiceToolbarBtn:not(.rtcVoiceToolbarBtnFinish)';
          // 1) 若已有残留录音工具栏, 先取消, 回到空闲
          if (vis(q('.rtcVoiceRecordingToolbar'))){
            const c = q(CAN); if (c) c.click();
          }
          const mic = q(MIC);
          if (!mic) return JSON.stringify({ok:false, reason:'no-mic-button'});
          if (dis(mic)) return JSON.stringify({ok:false, reason:'mic-disabled'});
          mic.click();
          return JSON.stringify({ok:true, action:'start'});
        })()
        """
        try:
            return await self._eval_json(js)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def voice_finish(self):
        """点录音工具栏的 "✓ 完成" —— 停止录音并把转写文字落进输入框。"""
        js = r"""
        (() => {
          const q = s => document.querySelector(s);
          const dis = el => !el || el.disabled ||
                            (el.classList && el.classList.contains('disabled'));
          const fin = q('button.rtcVoiceToolbarBtnFinish');
          if (!fin) return JSON.stringify({ok:false, reason:'no-finish-button'});
          if (dis(fin)) return JSON.stringify({ok:false, reason:'finish-disabled'});
          fin.click();
          return JSON.stringify({ok:true, action:'finish'});
        })()
        """
        try:
            return await self._eval_json(js)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def voice_cancel(self):
        """取消当前录音(丢弃, 不落字)。"""
        js = r"""
        (() => {
          const c = document.querySelector(
            'button.rtcVoiceToolbarBtn:not(.rtcVoiceToolbarBtnFinish)');
          if (!c) return JSON.stringify({ok:false, reason:'no-cancel-button'});
          c.click();
          return JSON.stringify({ok:true, action:'cancel'});
        })()
        """
        try:
            return await self._eval_json(js)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def state(self):
        """返回当前语音控件的可见状态(供日志/自检)。"""
        js = r"""
        (() => {
          const vis = el => el && el.offsetParent !== null &&
                            getComputedStyle(el).display !== 'none';
          const rec = document.querySelector('.rtcVoiceRecordingToolbar');
          const box = document.querySelector('.chat-input-v2-input-box-editable');
          return JSON.stringify({
            recording: vis(rec),
            timer: rec ? (rec.textContent||'').trim().slice(0,12) : '',
            micBtn: !!document.querySelector('button.rtcVoicePluginButton'),
            finishBtn: vis(document.querySelector('button.rtcVoiceToolbarBtnFinish')),
            boxText: box ? (box.textContent||'').slice(0,40) : ''
          });
        })()
        """
        try:
            return await self._eval_json(js)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def close(self):
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
