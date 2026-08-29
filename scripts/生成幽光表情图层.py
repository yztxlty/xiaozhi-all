from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("/Users/yuezhenting/esp/ygsoul-资源/动图/01_Speaking/Speaking_HD.apng")
BASE = ROOT / "apps/h5-demo/assets/mascot/youguang-base.png"
OUTPUT = ROOT / "apps/h5-demo/assets/mascot/layers"
SIZE = (466, 466)
MOUTH_BOX = (190, 190, 280, 245)
FRAME_FOR_STATE = {
    "idle": 0, "listening": 0, "hearing": 0,
    "recognizing": 0, "thinking": 0, "speaking": 1,
    "speaking-a": 1, "speaking-b": 2,
    "happy": 2, "sad": 0, "comforting": 0, "surprised": 2, "error": 0,
}


def extract_mouth_layer(base: Image.Image, frame: Image.Image) -> Image.Image:
    """只保留原始帧相对高清底图的真实嘴部变化，其他像素保持完全透明。"""
    layer = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    base_pixels = base.load()
    frame_pixels = frame.load()
    output_pixels = layer.load()
    left, top, right, bottom = MOUTH_BOX
    for y in range(top, bottom):
        for x in range(left, right):
            source = frame_pixels[x, y]
            original = base_pixels[x, y]
            distance = sum(abs(source[index] - original[index]) for index in range(3))
            if source[3] and distance >= 12:
                output_pixels[x, y] = source
    return layer


def main() -> None:
    base = Image.open(BASE).convert("RGBA")
    if base.size != SIZE:
        raise ValueError(f"底图必须是 {SIZE}，实际为 {base.size}")
    source = Image.open(SOURCE)
    frames = []
    for index in range(source.n_frames):
        source.seek(index)
        frame = source.convert("RGBA")
        if frame.size != SIZE:
            raise ValueError(f"原始帧必须是 {SIZE}，实际为 {frame.size}")
        frames.append(frame)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    for state, frame_index in FRAME_FOR_STATE.items():
        layer = extract_mouth_layer(base, frames[frame_index])
        layer.save(OUTPUT / f"{state}.png", "PNG", optimize=True)


if __name__ == "__main__":
    main()
