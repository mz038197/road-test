"""路考答案框（老師用，不發給學生）

把 CamVid 的像素標註轉成物件框，存成路考用的答案檔。
用法：uv run make-answers [--camvid <CamVid資料夾>] [--out <答案.json>] [--sequence Seq05VD]
沒給 --camvid 就讀環境變數 CAMVID_DIR；沒給 --out 就存到 ROAD_TEST_DATA/answers/<sequence>.json。

標註圖是 480×360，路考影片是 960×720，所以框的座標乘 2。
路考 MP4 已經剪好，第 n 幀就是標註 f(n)（見 docs/alignment.md），所以每個計分幀的 frame 就是標註幀號。
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from road_test import paths

# SegNet 版 CamVid 的類別編號 → 標註規範的類別名稱
CLASS_IDS = {"sign": 6, "car": 8, "pedestrian": 9, "bicyclist": 10}
MIN_AREA = 10  # 像素；更小的當成標註雜訊
# 框高到這個像素數才算「近到必須看見」，沒看見要扣分；比它小的看見不加分、沒看見也不扣
# （以 480×360 的標註圖來算）
MIN_HEIGHT = {"pedestrian": 25, "bicyclist": 25, "car": 30, "sign": 12}

VIDEO_WIDTH, VIDEO_HEIGHT, FPS = 960, 720, 29.97
SCALE = 2  # 480×360 → 960×720
SPLITS = ("trainannot", "valannot", "testannot")  # Seq05VD 在 test，0006R0 在 train


def boxes_from_mask(mask):
    """一張標註圖 → [{"cls", "box": [x1, y1, x2, y2], "relevant"}]，相連的同類像素算一個物件。座標是標註圖的像素。"""
    objects = []
    for name, cid in CLASS_IDS.items():
        pixels = mask == cid
        # 被電線桿切開的同一台車先接回來；接的時候多出來的像素不算面積
        labeled, _ = ndimage.label(ndimage.binary_closing(pixels, iterations=3) | pixels)
        for idx, sl in enumerate(ndimage.find_objects(labeled), start=1):
            area = int(((labeled[sl] == idx) & pixels[sl]).sum())
            if area < MIN_AREA:
                continue
            y1, y2, x1, x2 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
            # 夠高、而且露出的面積夠大才算必須看見；被擋到只剩一條的不算
            relevant = y2 - y1 >= MIN_HEIGHT[name] and area >= MIN_HEIGHT[name] ** 2 / 4
            objects.append({"cls": name, "box": [int(x1), int(y1), int(x2), int(y2)], "relevant": relevant})
    return objects


def label_number(path: Path) -> int:
    """0006R0_f00930.png → 930（標註幀號）。"""
    return int(path.stem.split("_")[-1].lstrip("f"))


def _to_video(objects):
    return [{**o, "box": [v * SCALE for v in o["box"]]} for o in objects]


def main(argv=None):
    parser = argparse.ArgumentParser(description="從 CamVid 標註圖產生路考答案")
    parser.add_argument("--camvid", type=Path, help="CamVid 資料夾，預設讀環境變數 CAMVID_DIR")
    parser.add_argument("--out", type=Path, help="答案檔，預設是 ROAD_TEST_DATA/answers/<sequence>.json")
    parser.add_argument("--sequence", default="Seq05VD", help="Seq05VD 是正式路考，0006R0 是模擬路考")
    args = parser.parse_args(argv)
    args.camvid = args.camvid or paths.camvid_dir()
    args.output = args.out or paths.answers_dir() / f"{args.sequence}.json"
    args.output.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(
        (f for split in SPLITS for f in (args.camvid / split).glob(f"{args.sequence}_*.png")), key=label_number
    )
    if not files:
        raise SystemExit(f"{args.camvid} 的 {'、'.join(SPLITS)} 裡都沒有 {args.sequence} 的標註圖")
    frames = [
        {"name": f.name, "frame": label_number(f), "objects": _to_video(boxes_from_mask(np.array(Image.open(f))))}
        for f in files
    ]
    answers = {"sequence": args.sequence, "width": VIDEO_WIDTH, "height": VIDEO_HEIGHT, "fps": FPS, "frames": frames}
    args.output.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")

    print(f"{len(frames)} 張計分幀，存到 {args.output}")
    for name in CLASS_IDS:
        found = [o for fr in frames for o in fr["objects"] if o["cls"] == name]
        print(f"{name:10s} 物件 {len(found):4d}，其中必看 {sum(o['relevant'] for o in found):4d}")


if __name__ == "__main__":
    main()
