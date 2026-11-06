"""找出每一張 CamVid 標註對上影片的哪一幀（老師準備路考資料時跑一次）

用法：uv run python -P scripts/align_video.py <影片> --sequence 0006R0 [--camvid DIR] [--csv 結果.csv]

1. 整支影片縮到 96×72 灰階，用相關係數替每張標註原圖找候選幀。
2. 再把候選幀前後幾幀縮到 480×360，跟原圖比平均像素差，挑最小的那幀。
每一張都要對得清楚、位移都一樣，才印出位移（影片幀號 − 標註幀號）。
拿剪好的路考 MP4 來跑，位移應該是 0：MP4 第 n 幀就是標註 f(n)。
"""
import argparse
import csv
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from road_test import paths
from road_test.alignment import best_match, offset
from road_test.answers import label_number

SMALL = (96, 72)
FULL = (480, 360)
WINDOW = 3  # 候選幀前後各看幾幀


def frames(video: Path, size: tuple[int, int], pix_fmt: str):
    """逐幀讀出縮小後的影片。"""
    w, h = size
    channels = 1 if pix_fmt == "gray" else 3
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0", "-vf", f"scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"],
        stdout=subprocess.PIPE,
    )
    n = w * h * channels
    while chunk := proc.stdout.read(n):
        yield np.frombuffer(chunk, np.uint8).reshape(h, w, channels).astype(np.float32)
    proc.wait()


def _norm(a: np.ndarray) -> np.ndarray:
    a = a.reshape(len(a), -1)
    a = a - a.mean(1, keepdims=True)
    return a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-6)


def main(argv=None):
    parser = argparse.ArgumentParser(description="找出每一張標註對上影片的哪一幀")
    parser.add_argument("video", type=Path)
    parser.add_argument("--sequence", required=True)
    parser.add_argument("--camvid", type=Path, help="CamVid 資料夾，預設讀環境變數 CAMVID_DIR")
    parser.add_argument("--csv", type=Path, help="把每一張的結果存成 CSV")
    args = parser.parse_args(argv)
    camvid = args.camvid or paths.camvid_dir()

    images = sorted(
        (f for split in ("train", "val", "test") for f in (camvid / split).glob(f"{args.sequence}_*.png")),
        key=label_number,
    )
    if not images:
        raise SystemExit(f"{camvid} 裡沒有 {args.sequence} 的原圖")
    labels = [label_number(f) for f in images]
    refs = {lab: np.asarray(Image.open(f).convert("RGB"), np.float32) for lab, f in zip(labels, images)}

    small = np.stack([f[..., 0] for f in frames(args.video, SMALL, "gray")])
    print(f"影片 {len(small)} 幀，標註 {len(labels)} 張（f{labels[0]:05d}～f{labels[-1]:05d}）")
    vn = _norm(small)
    wanted: dict[int, list[int]] = {}  # 影片幀號 → 要跟它比的標註
    for lab in labels:
        ref_small = np.asarray(Image.fromarray(refs[lab].astype(np.uint8)).convert("L").resize(SMALL, Image.BILINEAR), np.float32)
        k = int((vn @ _norm(ref_small[None])[0]).argmax())
        for n in range(max(0, k - WINDOW), min(len(small), k + WINDOW + 1)):
            wanted.setdefault(n, []).append(lab)

    diffs: dict[int, dict[int, float]] = {lab: {} for lab in labels}
    for n, frame in enumerate(frames(args.video, FULL, "rgb24")):
        for lab in wanted.get(n, []):
            diffs[lab][n] = float(np.abs(frame - refs[lab]).mean())
    matches = {lab: best_match(d) for lab, d in diffs.items()}

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            out = csv.writer(fh)
            out.writerow(["標註", "影片幀", "位移", "平均像素差", "相鄰幀平均像素差", "對得清楚"])
            for lab, m in sorted(matches.items()):
                out.writerow([f"f{lab:05d}", m.video_frame, m.video_frame - lab, f"{m.diff:.2f}", f"{m.neighbour_diff:.2f}", m.clear])
        print(f"結果存到 {args.csv}")

    d = np.array([m.diff for m in matches.values()])
    nb = np.array([m.neighbour_diff for m in matches.values()])
    print(f"對上的平均像素差 {d.min():.2f}～{d.max():.2f}（中位數 {np.median(d):.2f}）")
    print(f"相鄰幀的平均像素差 {nb.min():.2f}～{nb.max():.2f}（中位數 {np.median(nb):.2f}）")
    shift = offset(matches)
    still = [f"f{lab:05d}（{m.video_frame - lab:+d}）" for lab, m in sorted(matches.items()) if not m.clear]
    print(f"{len(matches) - len(still)} 張對得清楚，位移都是 {shift:+d}：影片幀號 = 標註幀號 {shift:+d}")
    if still:
        print(f"{len(still)} 張分不出是哪一幀（畫面幾乎沒動）：" + "、".join(still))


if __name__ == "__main__":
    main()
