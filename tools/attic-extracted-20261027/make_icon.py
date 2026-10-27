# -*- coding: utf-8 -*-
"""生成「AI Passport 语音」桌面图标: 黑底 + 荧光绿麦克风(Matrix 黑科技风)。"""
from PIL import Image, ImageDraw

SIZE = 512
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

DARK = (16, 18, 16, 255)          # 近黑碳底
GREEN = (0, 230, 118, 255)        # 荧光绿主色
GREEN_DIM = (0, 230, 118, 110)    # 半透明绿(次级声波)
GRID = (0, 230, 118, 26)          # 极淡网格线

# 1) 圆角方形底(黑)
d.rounded_rectangle([28, 28, SIZE - 28, SIZE - 28], radius=104, fill=DARK,
                    outline=(0, 230, 118, 90), width=3)

# 2) 极淡背景网格(Matrix 终端感)
for x in range(88, SIZE - 28, 88):
    d.line([x, 40, x, SIZE - 40], fill=GRID, width=1)
for y in range(88, SIZE - 28, 88):
    d.line([40, y, SIZE - 40, y], fill=GRID, width=1)

# 3) 麦克风主体(胶囊, 绿)
d.rounded_rectangle([206, 104, 306, 268], radius=50, fill=GREEN)

# 4) 支架(下半圆弧) + 杆 + 底座
d.arc([156, 130, 356, 300], start=0, end=180, fill=GREEN, width=20)
d.line([256, 300, 256, 352], fill=GREEN, width=20)
d.line([186, 366, 326, 366], fill=GREEN, width=20)

# 5) 右侧声波(两道弧, 一实一虚)
d.arc([330, 160, 420, 250], start=-55, end=55, fill=GREEN, width=14)
d.arc([356, 130, 470, 280], start=-50, end=50, fill=GREEN_DIM, width=12)

# 6) 左下角终端光标点(小方块, 点缀)
d.rectangle([92, 402, 116, 426], fill=GREEN_DIM)

out = r"C:\Users\Administrator\Desktop\ESP32\folo-ai-passport-voice-app\extracted\windows\passport-voice.ico"
img.save(out, format="ICO",
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                (128, 128), (256, 256)])
img.save(r"C:\Users\Administrator\Desktop\ESP32\icon_preview.png")
print("ICO saved:", out)
