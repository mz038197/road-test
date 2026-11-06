# 路考（老師用，不發給學生）
# 把各組上傳的模型輪流放上同一段街道，一張一張播出來，邊播邊扣分，最後排名。
# 用法：uv run --group train python scripts/road_test.py <CamVid資料夾> <answers.json> <上傳檔資料夾> [--fps 4] [--out 路考結果] [--no-show]
# 上傳檔名要是 groupXX_confY.onnx，例如 group03_conf0.35.onnx。
# 空白鍵暫停、→ 跳過這組剩下的畫面、Esc 結束。
# 計分：漏看或誤判一次扣 MISS_PENALTY / FALSE_ALARM_PENALTY 的分；什麼都沒看見的司機算 0 分，全對算 100 分。
import argparse
import csv
import json
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

CLASSES = ("car", "pedestrian", "bicyclist", "sign")
LABELS = {"car": "汽車", "pedestrian": "行人", "bicyclist": "騎車的人", "sign": "號誌"}
MISS_PENALTY = {"pedestrian": 5, "bicyclist": 3, "car": 2, "sign": 1}
FALSE_ALARM_PENALTY = 1
SUBMISSION = re.compile(r"group(\d+)_conf([0-9.]+)\.onnx$", re.IGNORECASE)
FONTS = ["C:/Windows/Fonts/msjh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/PingFang.ttc"]


# %% 評分
def iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def inside(det, gt):
    """偵測框有多少比例落在答案框裡。答案框把黏在一起的幾台車算成一塊，分開框的每一台都要算對。"""
    w = min(det[2], gt[2]) - max(det[0], gt[0])
    h = min(det[3], gt[3]) - max(det[1], gt[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h / ((det[2] - det[0]) * (det[3] - det[1]))


def matches(det, gt):
    return det["cls"] == gt["cls"] and (iou(det["box"], gt["box"]) >= 0.3 or inside(det["box"], gt["box"]) >= 0.5)


def same_object(a, b):
    """前後兩張裡是不是同一個東西：同類、而且位置接近。連續好幾張都沒看見同一個行人，只扣一次。"""
    if a["cls"] != b["cls"]:
        return False
    if iou(a["box"], b["box"]) > 0:
        return True
    ca = ((a["box"][0] + a["box"][2]) / 2, (a["box"][1] + a["box"][3]) / 2)
    cb = ((b["box"][0] + b["box"][2]) / 2, (b["box"][1] + b["box"][3]) / 2)
    size = max(a["box"][2] - a["box"][0], a["box"][3] - a["box"][1], b["box"][2] - b["box"][0], b["box"][3] - b["box"][1])
    return np.hypot(ca[0] - cb[0], ca[1] - cb[1]) < size


def score(answer_frames, detections):
    """逐張比對答案框和偵測框，回傳每張的 {"missed", "false_alarms", "events"}。

    漏看：必須看見的答案框，沒有任何同類的偵測框對得上。
    誤判：偵測框對不上任何同類的答案框（遠處小東西也算答案，框到不算誤判）。
    同一個東西連續幾張都漏看或誤判，只在第一張扣分。
    """
    results, prev_missed, prev_false = [], [], []
    for index, (answer, dets) in enumerate(zip(answer_frames, detections)):
        objects = answer["objects"]
        missed = [o for o in objects if o["relevant"] and not any(matches(d, o) for d in dets)]
        false_alarms = [d for d in dets if not any(matches(d, o) for o in objects)]
        events = []
        for o in missed:
            if not any(same_object(o, p) for p in prev_missed):
                events.append({"frame": index, "kind": "漏看", "cls": o["cls"], "box": o["box"], "penalty": MISS_PENALTY[o["cls"]]})
        for d in false_alarms:
            if not any(same_object(d, p) for p in prev_false):
                events.append({"frame": index, "kind": "誤判", "cls": d["cls"], "box": d["box"], "penalty": FALSE_ALARM_PENALTY})
        results.append({"missed": missed, "false_alarms": false_alarms, "events": events})
        prev_missed, prev_false = missed, false_alarms
    return results


def full_deduction(answer_frames):
    """什麼都沒看見的司機會被扣的分數，當成 0 分的基準。"""
    return sum(e["penalty"] for r in score(answer_frames, [[] for _ in answer_frames]) for e in r["events"])


def to_score(deducted, full):
    """全部看見、沒有誤判是 100 分；扣到跟什麼都沒看見一樣多是 0 分。"""
    return max(0, round(100 * (1 - deducted / full)))


# %% 偵測
def detect(path, conf, images):
    model = YOLO(str(path), task="detect")
    lacking = set(CLASSES) - set(model.names.values())
    if lacking:
        raise ValueError(f"模型沒有這些類別：{', '.join(sorted(lacking))}（有的是 {', '.join(model.names.values())}）")
    detections = []
    for image in images:
        boxes = model.predict(str(image), conf=conf, verbose=False)[0].boxes
        detections.append([
            {"cls": model.names[int(c)], "box": [float(v) for v in xyxy], "conf": float(p)}
            for xyxy, c, p in zip(boxes.xyxy.tolist(), boxes.cls.tolist(), boxes.conf.tolist())
            if model.names[int(c)] in CLASSES
        ])
    return detections


# %% 畫面
class Screen:
    SCALE = 2
    PANEL = 380

    def __init__(self, font_path):
        self.font = lambda size: ImageFont.truetype(font_path, size)

    def frame(self, image, dets, result, group, deducted, full, log, index, count):
        total = to_score(deducted, full)
        img = Image.open(image).convert("RGB")
        img = img.resize((img.width * self.SCALE, img.height * self.SCALE))
        canvas = Image.new("RGB", (img.width + self.PANEL, img.height), "#1e1e1e")
        canvas.paste(img)
        draw = ImageDraw.Draw(canvas)
        small = self.font(18)
        box = lambda b: [v * self.SCALE for v in b]

        false_ids = {id(d) for d in result["false_alarms"]}
        for d in dets:
            if id(d) not in false_ids:
                draw.rectangle(box(d["box"]), outline="#4caf50", width=2)
        for d in result["false_alarms"]:
            draw.rectangle(box(d["box"]), outline="#ff9800", width=3)
            draw.text((d["box"][0] * self.SCALE, d["box"][1] * self.SCALE - 22), f"誤判{LABELS[d['cls']]}", font=small, fill="#ff9800")
        for o in result["missed"]:
            draw.rectangle(box(o["box"]), outline="#f44336", width=4)
            draw.text((o["box"][0] * self.SCALE, o["box"][3] * self.SCALE + 2), f"漏看{LABELS[o['cls']]}", font=small, fill="#f44336")
        for e in result["events"]:
            draw.text((e["box"][2] * self.SCALE + 4, e["box"][1] * self.SCALE), f"-{e['penalty']}", font=self.font(34), fill="#ffeb3b")

        x = img.width + 20
        draw.text((x, 20), f"第 {group} 組", font=self.font(40), fill="white")
        draw.text((x, 80), f"{total}", font=self.font(96), fill="#ffeb3b" if total >= 60 else "#f44336")
        draw.text((x, 200), f"第 {index + 1} / {count} 張　扣 {deducted} / 滿扣 {full}", font=small, fill="#bbbbbb")
        draw.text((x, 228), "綠框看見  紅框漏看  橘框誤判", font=small, fill="#bbbbbb")
        for i, line in enumerate(log[-16:]):
            draw.text((x, 270 + i * 26), line, font=small, fill="white")
        return canvas

    def card(self, title, lines, size=(1340, 720)):
        canvas = Image.new("RGB", size, "#1e1e1e")
        draw = ImageDraw.Draw(canvas)
        draw.text((60, 40), title, font=self.font(56), fill="white")
        for i, line in enumerate(lines):
            draw.text((60, 140 + i * 48), line, font=self.font(36), fill="#ffeb3b" if i == 0 else "white")
        return canvas


def show(canvas, wait):
    cv2.imshow("road test", cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR))
    return cv2.waitKeyEx(wait)


# %% 主程式
def summary_lines(events):
    lines = []
    for kind in ("漏看", "誤判"):
        for cls in CLASSES:
            hit = [e for e in events if e["kind"] == kind and e["cls"] == cls]
            if hit:
                lines.append(f"{kind}{LABELS[cls]} {len(hit)} 次，扣 {sum(e['penalty'] for e in hit)} 分")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("camvid", type=Path)
    parser.add_argument("answers", type=Path)
    parser.add_argument("submissions", type=Path)
    parser.add_argument("--fps", type=float, default=4)
    parser.add_argument("--out", type=Path, default=Path("路考結果"))
    parser.add_argument("--no-show", action="store_true", help="不放映，只算分數和寫紀錄")
    parser.add_argument("--font", help="中文字型檔；沒給就找系統內建的")
    args = parser.parse_args()

    answers = json.loads(args.answers.read_text(encoding="utf-8"))["frames"]
    full = full_deduction(answers)
    images = [args.camvid / "test" / a["name"] for a in answers]
    submissions = sorted((int(m[1]), float(m[2]), p) for p in args.submissions.iterdir() if (m := SUBMISSION.search(p.name)))
    skipped = [p.name for p in args.submissions.iterdir() if not SUBMISSION.search(p.name)]
    if skipped:
        print("檔名不對、沒有參加：", ", ".join(skipped))
    args.out.mkdir(parents=True, exist_ok=True)

    screen = None
    if not args.no_show:
        font = args.font or next((f for f in FONTS if Path(f).exists()), None)
        if font is None:
            raise SystemExit("找不到中文字型，請用 --font 指定一個 .ttf 或 .ttc")
        screen = Screen(font)
        cv2.namedWindow("road test", cv2.WINDOW_NORMAL)

    board = []
    for group, conf, path in submissions:
        print(f"第 {group} 組（門檻 {conf}）偵測中…")
        try:
            detections = detect(path, conf, images)
        except Exception as error:
            print(f"  無法路考：{error}")
            board.append((group, conf, None, None, str(error)))
            continue
        results = score(answers, detections)
        events = [e for r in results for e in r["events"]]
        deducted = sum(e["penalty"] for e in events)
        total = to_score(deducted, full)

        with open(args.out / f"group{group:02d}.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["張", "檔名", "事件", "類別", "扣分", "x1", "y1", "x2", "y2"])
            for e in events:
                writer.writerow([e["frame"] + 1, answers[e["frame"]]["name"], e["kind"], LABELS[e["cls"]], e["penalty"],
                                 *(round(v) for v in e["box"])])

        if screen:
            log, running, skip = [], 0, False
            for index, (image, dets, result) in enumerate(zip(images, detections, results)):
                for e in result["events"]:
                    running += e["penalty"]
                    log.append(f"{index + 1:3d}  {e['kind']}{LABELS[e['cls']]}  -{e['penalty']}")
                if skip:
                    continue
                key = show(screen.frame(image, dets, result, group, running, full, log, index, len(images)), int(1000 / args.fps))
                if key == ord(" "):
                    key = cv2.waitKeyEx(0)
                if key == 27:
                    return
                if key in (2555904, 65363):  # →
                    skip = True
            show(screen.card(f"第 {group} 組 路考結束", [f"{total} 分（扣 {deducted} / 滿扣 {full}）", *summary_lines(events)]), 0)
        print(f"  {total} 分（扣 {deducted} / 滿扣 {full}）；" + "；".join(summary_lines(events)))
        board.append((group, conf, total, deducted, ""))

    board.sort(key=lambda row: (row[2] is None, -(row[2] or 0)))
    with open(args.out / "排行榜.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["名次", "組別", "門檻", "分數", "扣分", "滿扣", "備註"])
        for rank, (group, conf, total, deducted, note) in enumerate(board, start=1):
            writer.writerow([rank, group, conf, "" if total is None else total, "" if deducted is None else deducted, full, note])
    print(f"紀錄存在 {args.out.resolve()}")
    if screen:
        lines = [f"{rank}.  第 {g} 組  {'無法路考' if t is None else f'{t} 分'}" for rank, (g, _, t, _, _) in enumerate(board, start=1)]
        show(screen.card("排行榜", lines[:11]), 0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
