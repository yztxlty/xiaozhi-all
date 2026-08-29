"""将直接生成的透明表情条裁切为统一的幽光差分层。"""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "apps/h5-demo/assets/mascot"
OUT = TARGET / "layers"
SIZE = (466, 466)
# 与底图固定坐标系一致；整体上移，保证眼睛/嘴巴落在主体脸部。
FACE_BOX = (136, 110, 330, 250)
ANCHOR = ((FACE_BOX[0] + FACE_BOX[2]) / 2, (FACE_BOX[1] + FACE_BOX[3]) / 2)


def clean_generated_background(image: Image.Image) -> Image.Image:
    """清理偶发烘焙的浅灰棋盘格，不从主体提取表情。"""
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    mask = Image.new("L", rgba.size, 0)
    mp = mask.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if not a:
                continue
            luminance = (r * 299 + g * 587 + b * 114) // 1000
            chroma = max(r, g, b) - min(r, g, b)
            if luminance < 185 or (chroma >= 14 and luminance < 248):
                mp[x, y] = a
    rgba.putalpha(mask)
    return rgba


def crop_three_frames(source: Path) -> list[Image.Image]:
    image = clean_generated_background(Image.open(source))
    panel = image.width // 3
    if panel <= 0:
        raise ValueError(f"无法按三等分裁切: {source}")
    frames = []
    for index in range(3):
        left = index * panel
        right = image.width if index == 2 else (index + 1) * panel
        cell = image.crop((left, 0, right, image.height))
        side = min(cell.width, cell.height)
        top = max(0, (cell.height - side) // 2)
        left_in_cell = max(0, (cell.width - side) // 2)
        cell = cell.crop((left_in_cell, top, left_in_cell + side, top + side))
        frames.append(cell.resize(SIZE, Image.Resampling.LANCZOS))
    return frames


def save_frames(name: str, source: Path) -> dict[str, dict[str, int]]:
    layout = {}
    for index, frame in enumerate(crop_three_frames(source), 1):
        bbox = frame.getchannel("A").getbbox()
        if bbox is None:
            raise ValueError(f"表情帧没有可见内容: {source} 第 {index} 帧")
        content = frame.crop(bbox)
        box_width = FACE_BOX[2] - FACE_BOX[0]
        box_height = FACE_BOX[3] - FACE_BOX[1]
        scale = min(box_width / content.width, box_height / content.height)
        content = content.resize(
            (max(1, round(content.width * scale)), max(1, round(content.height * scale))),
            Image.Resampling.LANCZOS,
        )
        # 缩放会产生一圈全透明抗锯齿边，先移除它，再按可见内容居中。
        resized_bbox = content.getchannel("A").getbbox()
        if resized_bbox is None:
            raise ValueError(f"表情帧缩放后没有可见内容: {source} 第 {index} 帧")
        content = content.crop(resized_bbox)
        aligned = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        # 不按包围盒左上角对齐，改按统一锚点居中，避免帧切换时跳动。
        x = round(ANCHOR[0] - content.width / 2)
        y = round(ANCHOR[1] - content.height / 2)
        aligned.alpha_composite(content, (x, y))
        full_bbox = aligned.getchannel("A").getbbox()
        assert full_bbox is not None
        packed = aligned.crop(full_bbox)
        filename = f"{name}-{index}.png"
        packed.save(OUT / filename, "PNG", optimize=True)
        layout[filename] = {
            "x": full_bbox[0], "y": full_bbox[1],
            "width": packed.width, "height": packed.height,
            "canvasWidth": SIZE[0], "canvasHeight": SIZE[1],
        }
    return layout


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：生成幽光标准表情层.py <直接生成的三帧表情条目录>")
    source_dir = Path(sys.argv[1]).expanduser().resolve()
    sources = {
        "neutral": "neutral.png", "listening": "listening.png", "speaking": "speaking.png",
        "laughing": "laughing.png", "crying": "crying.png", "shy": "shy.png",
        "surprised": "surprised.png", "sad": "sad.png",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    all_layout = {}
    for name, filename in sources.items():
        source = source_dir / filename
        if not source.exists():
            raise SystemExit(f"缺少直接生成的三帧表情条: {source}")
        all_layout.update(save_frames(name, source))
    layout_path = ROOT / "apps/h5-demo/mascot-layer-layout.js"
    layout_path.write_text(
        "// 由生成幽光标准表情层.py 生成；裁剪图层仍按 466×466 原坐标定位。\n"
        "const MASCOT_LAYER_LAYOUT = " + repr(all_layout).replace("'", '"') + ";\n"
        "if (typeof globalThis !== 'undefined') globalThis.MASCOT_LAYER_LAYOUT = MASCOT_LAYER_LAYOUT;\n"
        "if (typeof module !== 'undefined' && module.exports) module.exports = MASCOT_LAYER_LAYOUT;\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
