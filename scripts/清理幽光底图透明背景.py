"""把底图外围烘焙的棋盘背景恢复为真实透明 alpha。"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "apps/h5-demo/assets/mascot/youguang-base.png"


def main() -> None:
    image = Image.open(PATH).convert("RGBA")
    pixels = image.load()
    background: set[tuple[int, int]] = set()
    queue = deque()
    for x in range(image.width):
        queue.extend(((x, 0), (x, image.height - 1)))
    for y in range(image.height):
        queue.extend(((0, y), (image.width - 1, y)))
    while queue:
        x, y = queue.popleft()
        if (x, y) in background or not (0 <= x < image.width and 0 <= y < image.height):
            continue
        r, g, b, _ = pixels[x, y]
        # 棋盘格是中性浅灰；彩色主体不会进入此连通区域。
        if max(r, g, b) - min(r, g, b) > 8 or min(r, g, b) < 215:
            continue
        background.add((x, y))
        queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    for x, y in background:
        r, g, b, _ = pixels[x, y]
        pixels[x, y] = (r, g, b, 0)
    image.save(PATH, "PNG", optimize=True)
    print(f"已清理底图背景：{len(background)} 个透明像素")


if __name__ == "__main__":
    main()
