"""端到端验证生产代码: trae_cdp.CdpClient.voice_start/voice_finish/state。"""
import asyncio
import trae_cdp


async def main():
    c = trae_cdp.CdpClient(9222)
    print("available:", await c.available())
    print("ensure:", await c.ensure())
    print("起始状态:", await c.state())
    print("→ voice_start():", await c.voice_start())
    await asyncio.sleep(0.8)
    print("开录后状态:", await c.state())
    await asyncio.sleep(1.5)
    print("→ voice_finish():", await c.voice_finish())
    await asyncio.sleep(1.2)
    print("完成后状态:", await c.state())
    # 收尾: 清空输入框
    await c.evaluate("(() => { const b=document.querySelector("
                     "'.chat-input-v2-input-box-editable'); if(b) b.textContent='';"
                     " return 'OK'; })()")
    print("收尾状态:", await c.state())
    await c.close()


asyncio.run(main())
