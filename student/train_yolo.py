# 訓練路考用的 YOLO
# 自己電腦和 Colab 跑的是同一支程式。要改只改下面「設定」這一段。
# 跑完會得到 road-test-model.onnx，就是要上傳到路考網站的檔案。組別和 CONF 交件時再填。
from pathlib import Path
import shutil

import yaml

# 設定
DATA = "dataset/data.yaml"  # Roboflow 匯出（YOLO 格式）解壓後裡面的 data.yaml
MODEL = "yolo26n.pt"        # 從哪個預訓練模型開始：n 最小最快，s、m 越來越大
EPOCHS = 100                # 沒有 NVIDIA 顯卡只能用 CPU，一輪要一分鐘以上；先用 3 確認資料沒問題，正式訓練到 Colab
IMGSZ = 640                 # 訓練時圖片縮放成的大小；號誌很小，調大看得比較清楚，但比較慢
BATCH = -1                  # -1 讓程式自己決定；記憶體不夠時改成 8 或 4

# 標註規範規定的類別名稱，大小寫要一樣，順序不限
CLASSES = {"car", "pedestrian", "bicyclist", "sign"}
UPLOAD = "road-test-model.onnx"  # 要上傳的檔案


def check_classes(data):
    names = yaml.safe_load(Path(data).read_text(encoding="utf-8"))["names"]
    names = set(names.values() if isinstance(names, dict) else names)
    if names != CLASSES:
        raise SystemExit(f"類別名稱跟標註規範不一樣：多了 {names - CLASSES or '無'}，少了 {CLASSES - names or '無'}。"
                         "類別名稱要是 car、pedestrian、bicyclist、sign，請回 Roboflow 改類別名稱後重新匯出。")


def train(data, model, epochs, imgsz, batch):
    check_classes(data)
    from ultralytics import YOLO  # 先檢查類別再載入，類別不對馬上就知道

    run = YOLO(model)
    run.train(data=data, epochs=epochs, imgsz=imgsz, batch=batch, name="road-test")

    best = YOLO(run.trainer.best)
    print("\n用驗證資料看各類別的表現（mAP50：0 到 1，越高越好）")
    metrics = best.val(data=data, imgsz=imgsz)
    for i, value in enumerate(metrics.box.ap50):
        print(f"  {best.names[metrics.box.ap_class_index[i]]:10s} {value:.2f}")

    upload = Path(UPLOAD)
    shutil.copy(best.export(format="onnx", imgsz=imgsz), upload)
    print(f"\n要上傳的檔案：{upload.resolve()}")
    return upload


if __name__ == "__main__":
    train(DATA, MODEL, EPOCHS, IMGSZ, BATCH)
