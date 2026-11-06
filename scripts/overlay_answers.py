"""把答案框疊在路考 MP4 的計分幀上，目視檢查框有沒有偏（老師準備路考資料時跑一次）

用法：uv run python -P scripts/overlay_answers.py <路考.mp4> <答案.json> <輸出.png> [--frames 930 2430 ...]
綠框是必看，灰框是非必看。沒給 --frames 就挑開頭、結尾，和必看最多的兩幀，拼成 2×2。
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def read_frame(video: Path, n: int, width: int, height: int) -> Image.Image:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0", "-vf", f"select=eq(n\\,{n})",
         "-vsync", "0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return Image.fromarray(np.frombuffer(raw, np.uint8).reshape(height, width, 3))


def main(argv=None):
    parser = argparse.ArgumentParser(description="答案框疊在路考影片的計分幀上")
    parser.add_argument("video", type=Path)
    parser.add_argument("answers", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--frames", type=int, nargs="+", help="要畫的計分幀（MP4 幀號）")
    args = parser.parse_args(argv)
    answers = json.loads(args.answers.read_text(encoding="utf-8"))
    w, h = answers["width"], answers["height"]
    by_frame = {f["frame"]: f for f in answers["frames"]}

    picks = args.frames
    if not picks:
        ranked = sorted(by_frame.values(), key=lambda f: -sum(o["relevant"] for o in f["objects"]))
        picks = [answers["frames"][0]["frame"], answers["frames"][-1]["frame"]]
        picks += [f["frame"] for f in ranked if f["frame"] not in picks][:2]
        picks.sort()

    tiles = []
    for n in picks:
        img = read_frame(args.video, n, w, h)
        draw = ImageDraw.Draw(img)
        for o in by_frame[n]["objects"]:
            color = "#00e676" if o["relevant"] else "#bdbdbd"
            draw.rectangle(o["box"], outline=color, width=3 if o["relevant"] else 1)
            if o["relevant"]:
                draw.text((o["box"][0] + 2, o["box"][1] - 12), o["cls"], fill=color)
        draw.text((10, 10), f"{answers['sequence']} frame {n}", fill="yellow")
        tiles.append(img)

    cols = 2 if len(tiles) > 1 else 1
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * w, (i // cols) * h))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out)
    print(f"幀 {picks}，存到 {args.out}")


if __name__ == "__main__":
    main()
