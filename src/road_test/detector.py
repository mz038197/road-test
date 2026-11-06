"""偵測器：讀學生交的模型，對路考影片的指定幀偵測，回傳每一幀的框。

網站只認得 `Detector` 這個介面，測試時換成假的偵測器。
"""
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol

# 標註規範規定的類別名稱；順序就是存偵測結果時的類別編號
CLASSES = ("car", "pedestrian", "bicyclist", "sign")
LOW_CONF = 0.05  # 偵測一律用這個低門檻把框存下來，CONF 在計分時才套用


class ModelRejected(Exception):
    """模型不合格（讀不進來、類別名稱不對）。訊息是給學生看的中文。"""


class Detector(Protocol):
    names: list[str]  # 模型的類別名稱

    def detect(self, video: Path, frames: Iterable[int]) -> Iterator[tuple[int, list[dict]]]:
        """依序偵測影片的這些幀（幀號由小到大），每幀產生 (幀號, [{"cls", "box": [x1,y1,x2,y2], "conf"}])。"""
        ...


class OnnxDetector:
    """用 onnxruntime 跑學生匯出的 YOLO ONNX。

    前處理和後處理照 ultralytics 8.4 的做法（letterbox 灰邊 114、INTER_LINEAR 縮放、BGR→RGB、÷255；
    逐類別 NMS，IoU 0.7、最多 300 個框；框換回原圖座標並裁在畫面內），結果跟 `YOLO(onnx).predict(conf=0.05)` 一樣。
    不直接用 ultralytics 是因為它要裝 torch，網站的 Docker 映像會大好幾倍。
    兩種輸出都支援：(1, 4+類別數, N) 要做 NMS；end2end 的 (1, N, 6) 已經是 [x1, y1, x2, y2, conf, 類別]。
    """
    IOU = 0.7
    MAX_DET = 300
    MAX_NMS = 30000
    MAX_WH = 7680  # 逐類別 NMS：每一類的框平移開來，就不會互相壓掉

    def __init__(self, path: Path, threads: int | None = None):
        import onnxruntime as ort

        options = ort.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
        try:
            # 讀成 bytes 再交給 onnxruntime，Windows 上才不會鎖住檔案（收件後要改名）
            self.session = ort.InferenceSession(Path(path).read_bytes(), options, providers=["CPUExecutionProvider"])
        except Exception as e:
            raise ModelRejected("模型讀不進來，請確認上傳的是訓練程式匯出的 .onnx 檔。") from e
        meta = self.session.get_modelmeta().custom_metadata_map
        if "names" not in meta:
            raise ModelRejected("模型裡沒有類別名稱，請用訓練程式（ultralytics）匯出 ONNX。")
        import ast

        names = ast.literal_eval(meta["names"])
        self.names = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
        inp = self.session.get_inputs()[0]
        self.input = inp.name
        imgsz = ast.literal_eval(meta["imgsz"]) if "imgsz" in meta else inp.shape[2:]
        self.imgsz = (imgsz, imgsz) if isinstance(imgsz, int) else tuple(int(v) for v in imgsz)  # (高, 寬)
        out = self.session.get_outputs()[0].shape
        self.end2end = meta.get("end2end") == "True" or (len(out) == 3 and out[-1] == 6)

    def detect(self, video: Path, frames: Iterable[int]) -> Iterator[tuple[int, list[dict]]]:
        import cv2

        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError(f"打不開路考影片 {video}")
        pos = 0
        try:
            for n in frames:
                # 一直往後讀（grab 只解碼不轉換，整支 0006R0 不到 2 秒），比 seek 準也比 seek 快
                while pos < n:
                    if not cap.grab():
                        raise RuntimeError(f"路考影片 {video.name} 沒有第 {n} 幀")
                    pos += 1
                ok, image = cap.read()
                if not ok:
                    raise RuntimeError(f"路考影片 {video.name} 沒有第 {n} 幀")
                pos += 1
                yield n, self.predict(image)
        finally:
            cap.release()

    def predict(self, image) -> list[dict]:
        """一張 BGR 圖（OpenCV 讀進來的樣子）→ 信心分數 > 0.05 的框，座標是原圖的。"""
        x, gain, pad = self._letterbox(image)
        out = self.session.run(None, {self.input: x})[0][0]
        boxes, conf, cls = self._end2end(out) if self.end2end else self._nms(out)
        boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - pad[0]) / gain[0]).clip(0, image.shape[1])
        boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - pad[1]) / gain[1]).clip(0, image.shape[0])
        return [{"cls": self.names[int(c)], "box": [float(v) for v in b], "conf": float(s)}
                for b, s, c in zip(boxes, conf, cls)]

    def _letterbox(self, image):
        import cv2
        import numpy as np

        h, w = image.shape[:2]
        th, tw = self.imgsz
        r = min(th / h, tw / w)
        nw, nh = max(round(w * r), 1), max(round(h * r), 1)
        dw, dh = (tw - nw) / 2, (th - nh) / 2
        top, bottom = round(dh - 0.1), round(dh + 0.1)
        left, right = round(dw - 0.1), round(dw + 0.1)
        if (w, h) != (nw, nh):
            image = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        x = np.ascontiguousarray(image[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
        return x, (nw / w, nh / h), (left, top)

    def _end2end(self, out):
        keep = out[out[:, 4] > LOW_CONF][: self.MAX_DET]
        return keep[:, :4].copy(), keep[:, 4], keep[:, 5]

    def _nms(self, out):
        import numpy as np

        pred = out.T  # (N, 4 + 類別數)
        scores = pred[:, 4:4 + len(self.names)]
        cls = scores.argmax(1)
        conf = scores[np.arange(len(cls)), cls]
        keep = conf > LOW_CONF
        xywh, conf, cls = pred[keep, :4], conf[keep], cls[keep]
        order = np.argsort(-conf, kind="stable")[: self.MAX_NMS]
        xywh, conf, cls = xywh[order], conf[order], cls[order]
        boxes = np.empty_like(xywh)
        boxes[:, :2] = xywh[:, :2] - xywh[:, 2:] / 2
        boxes[:, 2:] = xywh[:, :2] + xywh[:, 2:] / 2
        shifted = boxes + (cls * self.MAX_WH)[:, None].astype(boxes.dtype)
        kept = _greedy_nms(shifted, self.IOU)[: self.MAX_DET]
        return boxes[kept], conf[kept], cls[kept]


def _greedy_nms(boxes, iou_threshold: float):
    """boxes 已經照信心分數由高到低排好；回傳留下來的索引（跟 torchvision.ops.nms 一樣的規則）。"""
    import numpy as np

    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = np.arange(len(boxes))
    kept = []
    while order.size:
        i = order[0]
        kept.append(i)
        rest = order[1:]
        w = np.clip(np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]), 0, None)
        h = np.clip(np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]), 0, None)
        inter = w * h
        order = rest[inter / (areas[i] + areas[rest] - inter) <= iou_threshold]
    return np.array(kept, dtype=int)
