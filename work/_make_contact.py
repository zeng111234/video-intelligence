"""4 MP4 关键帧 contact sheet。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path('C:/Users/zeng/Desktop/video/work/frames')
ETIDS = [
    'edit-local-6e03f0e1cd',  # r8
    'edit-local-0b06adfccb',  # 教培
    'edit-local-df9a50d53c',  # SaaS
    'edit-local-4619aecf4c',  # 物流
]
NAMES = ['r8 / 烧烤店', 'stranger_education / 教培', 'stranger_saas / SaaS', 'stranger_logistics / 物流']

# 每个 mp4 抽 10 帧
thumbs = []
for etid in ETIDS:
    folder = ROOT / etid
    files = sorted(folder.glob('f*.jpg'))
    row_imgs = [Image.open(f).resize((180, 320), Image.Resampling.LANCZOS) for f in files]
    thumbs.append((NAMES[ETIDS.index(etid)], etid, row_imgs))

# 拼 4 行 10 列
thumb_w, thumb_h = 180, 320
label_h = 50
title_h = 60
gap = 5
total_w = thumb_w * 10 + gap * 11
total_h = (thumb_h + label_h + gap) * 4 + title_h + gap * 2

canvas = Image.new('RGB', (total_w, total_h), (24, 28, 36))
draw = ImageDraw.Draw(canvas)
try:
    title_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)
    label_font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)
except OSError:
    title_font = ImageFont.load_default()
    label_font = ImageFont.load_default()

draw.text((20, 15), 'VideoInsight P0 v3 - 4 MP4 Contact Sheet (10s/frame)', fill=(255, 255, 255), font=title_font)

y = title_h
for name, etid, row_imgs in thumbs:
    draw.text((20, y), name, fill=(220, 220, 220), font=label_font)
    draw.text((20, y + 18), etid, fill=(140, 140, 140), font=label_font)
    y += label_h
    for i, img in enumerate(row_imgs):
        x = gap + i * (thumb_w + gap)
        canvas.paste(img, (x, y))
    y += thumb_h + gap

out = Path('C:/Users/zeng/Desktop/video/work/contact_sheet_4mp4.jpg')
canvas.save(out, quality=85)
print(f'wrote {out} ({out.stat().st_size} bytes, {total_w}x{total_h})')
