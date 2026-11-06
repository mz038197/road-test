"""路考計分（純函式）。

輸入一場路考的答案、每個計分幀的偵測結果和 CONF，輸出考官紀錄、每個計分幀的判定和成績。
計分幀每秒一幀，所以第 i 個計分幀就是第 i 秒；反應時間只有整數秒。
座標的單位不限（480×360 或 960×720 都可以），答案和偵測結果用同一種就好。
"""
import math

WEIGHTS = {"pedestrian": 5, "bicyclist": 3, "car": 2, "sign": 1}
MISS, FALSE_ALARM = "漏看", "誤判"


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
    """對得上：同類，而且 IoU ≥ 0.3，或偵測框有一半以上落在答案框內。"""
    return det["cls"] == gt["cls"] and (iou(det["box"], gt["box"]) >= 0.3 or inside(det["box"], gt["box"]) >= 0.5)


def same_object(a, b):
    """相鄰兩個計分幀裡是不是同一個東西：同類，而且框有重疊或中心夠近。"""
    if a["cls"] != b["cls"]:
        return False
    if iou(a["box"], b["box"]) > 0:
        return True
    ca = ((a["box"][0] + a["box"][2]) / 2, (a["box"][1] + a["box"][3]) / 2)
    cb = ((b["box"][0] + b["box"][2]) / 2, (b["box"][1] + b["box"][3]) / 2)
    size = max(a["box"][2] - a["box"][0], a["box"][3] - a["box"][1], b["box"][2] - b["box"][0], b["box"][3] - b["box"][1])
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1]) < size


def _center_gap(a, b):
    ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1])


def _pair(prev, boxes):
    """相鄰兩個計分幀的框一對一配對：same_object 的配對中，重疊越多（IoU 大）、中心越近的先配。

    回傳 {這一幀的框: 前一幀的框}。同一幀的兩個框不會配到同一個框，所以永遠是兩個東西。
    """
    pairs = [(-iou(b["box"], a["box"]), _center_gap(b["box"], a["box"]), j, k)
             for j, b in enumerate(boxes) for k, a in enumerate(prev) if same_object(b, a)]
    used_now, used_prev, link = set(), set(), {}
    for _, _, j, k in sorted(pairs):
        if j not in used_now and k not in used_prev:
            used_now.add(j)
            used_prev.add(k)
            link[j] = k
    return link


def _group(frames):
    """把每幀的框串成東西：相鄰幀之間一對一配對，配上的是同一個東西。回傳每個框所屬東西的編號（跟 frames 同形狀）。"""
    ids, count = [], 0
    for i, boxes in enumerate(frames):
        link = _pair(frames[i - 1], boxes) if i else {}
        row = []
        for j in range(len(boxes)):
            if j in link:
                row.append(ids[i - 1][link[j]])
            else:
                row.append(count)
                count += 1
        ids.append(row)
    return ids


def _miss_penalty(cls, reaction):
    """0 秒不扣，1 秒扣一半，2 秒以上或一直沒看見（None）扣滿。"""
    if reaction == 0:
        return 0
    if reaction == 1:
        return WEIGHTS[cls] / 2
    return WEIGHTS[cls]


def score(answers: dict, detections: list[list[dict]], conf: float) -> dict:
    """計分一場路考。

    answers：{"frames": [{"name", "objects": [{"cls", "box": [x1, y1, x2, y2], "relevant"}]}]}，每個計分幀一個。
    detections：跟 answers["frames"] 一樣長，每個計分幀一串 {"cls", "box", "conf"}（可以含低於 CONF 的框）。
    回傳（可以直接 json.dumps）：
      log：考官紀錄，照結算時間排；每條 {"frame", "time", "cls", "kind": "漏看"|"誤判", "reaction", "penalty"}
      frames：每個計分幀 {"frame", "time", "name", "marks": [{"color": "green"|"red"|"orange", "cls", "box", "waiting"?}]}
      deducted、full（扣滿）、score（0–100）
    """
    frames = [f["objects"] for f in answers["frames"]]
    if len(detections) != len(frames):
        raise ValueError(f"偵測結果有 {len(detections)} 幀，答案有 {len(frames)} 個計分幀，數目要一樣")
    last = len(frames) - 1
    kept = [[d for d in dets if d["conf"] >= conf] for dets in detections]

    # 必看的東西：第一次變成必看、第一次被框到、最後出現在哪一幀
    ids = _group(frames)
    things = {}
    for i, objs in enumerate(frames):
        for obj, oid in zip(objs, ids[i]):
            t = things.setdefault(oid, {"cls": obj["cls"], "relevant": None, "seen": None, "last": i})
            t["last"] = i
            if obj["relevant"] and t["relevant"] is None:
                t["relevant"] = i
            if t["seen"] is None and any(matches(d, obj) for d in kept[i]):
                t["seen"] = i

    log, full = [], 0
    for t in things.values():
        if t["relevant"] is None:
            continue
        full += WEIGHTS[t["cls"]]
        if t["seen"] is None:
            reaction, at = None, min(t["last"] + 1, last)
        else:
            reaction, at = max(0, t["seen"] - t["relevant"]), t["seen"]
        penalty = _miss_penalty(t["cls"], reaction)
        if penalty:
            log.append({"frame": at, "time": at, "cls": t["cls"], "kind": MISS, "reaction": reaction, "penalty": penalty})

    # 誤判：對不上任何答案框（含非必看的東西）的偵測框。串成東西時用全部的偵測框、不管 CONF，
    # 這樣 CONF 提高只會讓某些東西整個不算誤判，不會把一個東西拆成兩段、反而多扣分。
    false = [[d for d in dets if not any(matches(d, o) for o in objs)] for dets, objs in zip(detections, frames)]
    flagged = set()
    for i, (dets, gids) in enumerate(zip(false, _group(false))):
        for d, gid in zip(dets, gids):
            if d["conf"] >= conf and gid not in flagged:
                flagged.add(gid)
                log.append({"frame": i, "time": i, "cls": d["cls"], "kind": FALSE_ALARM, "reaction": None, "penalty": 1})

    # 每個計分幀的判定：必看的東西看見了是綠、還沒看見是紅（附已經等了幾秒）；誤判是橘
    marked = []
    for i, (objs, oids, dets) in enumerate(zip(frames, ids, false)):
        marks = []
        for obj, oid in zip(objs, oids):
            t = things[oid]
            if t["relevant"] is None or t["relevant"] > i:
                continue
            if t["seen"] is not None and t["seen"] <= i:
                marks.append({"color": "green", "cls": obj["cls"], "box": list(obj["box"])})
            else:
                marks.append({"color": "red", "cls": obj["cls"], "box": list(obj["box"]), "waiting": i - t["relevant"]})
        marks += [{"color": "orange", "cls": d["cls"], "box": list(d["box"])} for d in dets if d["conf"] >= conf]
        marked.append({"frame": i, "time": i, "name": answers["frames"][i]["name"], "marks": marks})

    log.sort(key=lambda e: e["frame"])
    deducted = sum(e["penalty"] for e in log)
    if full == 0:  # 整場沒有必看的東西
        total = 0 if deducted else 100
    else:
        total = max(0, round(100 * (1 - deducted / full)))
    return {"log": log, "frames": marked, "deducted": deducted, "full": full, "score": total}
