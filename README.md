# road-test

卷積神經網路的路考網站。術語見 `CONTEXT.md`；為什麼從 Machine-Learning-Lab 拆出來，見那邊的 [ADR-0001](https://github.com/mz038197/Machine-Learning-Lab/blob/main/docs/adr/0001-road-test-own-repo.md)。

## 目錄

```
src/road_test/      Python 套件，網站和老師工具都從這裡 import
  paths.py          資料夾（讀環境變數）
  answers.py        從 CamVid 標註圖產生路考答案（make-answers）
  alignment.py      影片幀和標註幀怎麼對齊的判斷規則
  scoring.py        計分：score(answers, detections, conf) → 考官紀錄、每個計分幀的判定、成績
  detector.py       偵測器：OnnxDetector 用 onnxruntime 跑學生的 ONNX（結果跟 ultralytics 一樣）
  web/              路考網站（FastAPI）
    app.py          HTTP API；create_app(資料夾, 偵測器, 路考) 讓測試換成假偵測器
    store.py        交件、偵測結果、成績存在 SQLite
    worker.py       排隊：先偵測計分幀算成績，再補完其餘的幀
    results.py      偵測框 → 成績（加上影片幀號）
    main.py         road-test-site 啟動指令
    static/         前端：純 HTML、JS、CSS
      playback.js   結果頁播放的計算（時間 ↔ 幀號、某一幀的判定、考官紀錄、分數），有 node 測試
      player.js     播放器：<video> 疊 canvas、播放控制、考官紀錄
scripts/            老師一次性的腳本
  align_video.py    比對影片和標註，找出位移
  overlay_answers.py  答案框疊在路考影片上，目視檢查
student/            發給學生的訓練程式
  train_yolo.py           本機版
  train_yolo_colab.ipynb  Colab 版
tests/              pytest；tests/js/ 是前端的 node --test
docs/alignment.md   兩場路考的影片怎麼對齊、怎麼轉成 MP4
```

## 安裝與測試

```powershell
uv sync
uv run pytest
node --test "tests/js/*.test.js"   # 前端播放邏輯（Node 18 以上）
```

學生訓練程式要用 ultralytics，裝起來很大，另外放在 `train` 群組：

```powershell
uv sync --group train
```

## 啟動路考網站

```powershell
$env:ROAD_TEST_DATA = "D:\road-test-data"
uv run road-test-site                 # http://127.0.0.1:8000
uv run road-test-site --host 0.0.0.0 --port 8080
```

`ROAD_TEST_WORKERS`：同時跑幾件（預設 2）；`ROAD_TEST_THREADS`：每個 worker 推論用幾個執行緒（預設 CPU 核心數 ÷ worker 數）。

`ROAD_TEST_TEACHER_PASSWORD`：老師密碼（Fly 上放 secrets）。沒設定的話，管理頁和老師的 API 一律拒絕。

### 管理頁 `/teacher.html`

用老師密碼登入（密碼只存在這個分頁，關掉分頁就登出）：

- **正式路考**：開放／關閉。開放期間模擬路考暫停收件，排隊中的正式路考先跑。
- **組別與組別代碼**：填組數按「套用」產生 01…N 組的代碼（原本的組保留代碼），每組可以「重設代碼」，也可以「全部重設代碼」。代碼像 `bako-mite`，課堂上唸給各組抄；學生輸入時不分大小寫，空白、連字號打錯也收。
- **還一次正式路考**：某組的正式路考出了問題要重交時用。
- **路考紀錄**：所有組的每一次路考，點「播放」在同一個分頁打開結果頁，按播放器的「全螢幕」給全班看。
- **清空**：學期結束時用，刪掉交件、結果、路考紀錄、還的正式路考次數和模型檔，要在頁面上再確認一次。組別代碼和正式路考開關不動。

## 資料夾

答案、路考影片、交件、結果、組別代碼和老師密碼都不進 git（這是公開 repo）。資料放在 repo 外，用環境變數指過去：

| 環境變數 | 內容 | 誰用 |
|---|---|---|
| `ROAD_TEST_DATA` | 路考資料夾：路考影片、答案、交件、結果、組別代碼。部署時就是 Fly 的 volume。 | 網站、老師 |
| `CAMVID_DIR` | CamVid（SegNet 版 480×360）資料夾，裡面有 `train/`、`trainannot/`、`test/`、`testannot/`。 | 只有產生答案、對齊影片時 |

路考資料夾的佈局：

```
$ROAD_TEST_DATA/
  videos/Seq05VD.mp4   正式路考，960×720，5101 幀
  videos/0006R0.mp4    模擬路考，960×720，3931 幀
  videos/0016E5.mp4    訓練影片
  answers/Seq05VD.json 正式路考的答案（171 個計分幀）
  answers/0006R0.json  模擬路考的答案（101 個計分幀）
  groups.json          網站產生：組別代碼 {"01": "bako-mite", ...}，管理頁產生、重設（組別用英數字，會放在 HTTP 標頭裡）
  road_test.db         網站產生：交件、排隊、偵測結果、成績、正式路考開關（SQLite）
  submissions/         網站產生：交上來的模型，改名成 組別_路考種類_上傳時間_編號.onnx
```

兩支路考 MP4 都剪成「第 n 幀就是標註 f(n)」，見 `docs/alignment.md`。原始影片（MXF）和 MP4 的備份在老師的 Google Drive `影像資料集/CamVid/raw_video`。

PowerShell：

```powershell
$env:CAMVID_DIR = "G:\我的雲端硬碟\影像資料集\CamVid"
$env:ROAD_TEST_DATA = "D:\road-test-data"
```

## 產生答案

```powershell
uv run make-answers                      # 正式路考 Seq05VD，存到 $env:ROAD_TEST_DATA\answers\Seq05VD.json
uv run make-answers --sequence 0006R0    # 模擬路考
uv run make-answers --camvid <CamVid資料夾> --out <答案.json>
```

答案的座標是路考影片的 960×720（標註圖的座標乘 2），每個計分幀帶著它在 MP4 的幀號：

```json
{"sequence": "0006R0", "width": 960, "height": 720, "fps": 29.97,
 "frames": [{"name": "0006R0_f00930.png", "frame": 930,
             "objects": [{"cls": "sign", "box": [788, 300, 804, 334], "relevant": true}]}]}
```

`box` 是 `[x1, y1, x2, y2]`，`relevant` 是不是必看。

