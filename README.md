# road-test

卷積神經網路的路考網站。術語見 `CONTEXT.md`；為什麼從 Machine-Learning-Lab 拆出來，見那邊的 [ADR-0001](https://github.com/mz038197/Machine-Learning-Lab/blob/main/docs/adr/0001-road-test-own-repo.md)。

## 目錄

```
src/road_test/      Python 套件，網站和老師工具都從這裡 import
  paths.py          資料夾（讀環境變數）
  answers.py        從 CamVid 標註圖產生路考答案（make-answers）
  scoring.py        計分：score(answers, detections, conf) → 考官紀錄、每個計分幀的判定、成績
student/            發給學生的訓練程式
  train_yolo.py           本機版
  train_yolo_colab.ipynb  Colab 版
tests/              pytest
```

## 安裝與測試

```powershell
uv sync
uv run pytest
```

學生訓練程式要用 ultralytics，裝起來很大，另外放在 `train` 群組：

```powershell
uv sync --group train
```

## 資料夾

答案、路考影片、交件、結果、組別代碼和老師密碼都不進 git（這是公開 repo）。資料放在 repo 外，用環境變數指過去：

| 環境變數 | 內容 | 誰用 |
|---|---|---|
| `ROAD_TEST_DATA` | 路考資料夾：路考影片、答案（`answers/`）、交件、結果、組別代碼。部署時就是 Fly 的 volume。 | 網站、老師 |
| `CAMVID_DIR` | CamVid（SegNet 版 480×360）資料夾，裡面有 `testannot/`。 | 只有產生答案時 |

PowerShell：

```powershell
$env:CAMVID_DIR = "G:\我的雲端硬碟\影像資料集\CamVid"
$env:ROAD_TEST_DATA = "D:\road-test-data"
```

## 產生答案

```powershell
uv run make-answers                      # 正式路考 Seq05VD，存到 $env:ROAD_TEST_DATA\answers\Seq05VD.json
uv run make-answers --sequence 0006R0    # 其他段街道
uv run make-answers --camvid <CamVid資料夾> --out <答案.json>
```
