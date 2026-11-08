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
      index.html    首頁＝委託頁：說明、計分規則、注意事項、下載訓練影片和訓練程式、在 Colab 開啟
      submit.html   交件、看自己這組的路考紀錄
      playback.js   結果頁播放的計算（時間 ↔ 幀號、某一幀的判定、考官紀錄、分數），有 node 測試
      player.js     播放器：<video> 疊 canvas、播放控制、考官紀錄
scripts/            老師一次性的腳本
  align_video.py    比對影片和標註，找出位移
  overlay_answers.py  答案框疊在路考影片上，目視檢查
student/            發給學生的訓練程式（跑完輸出 road-test-model.onnx；組別和 CONF 交件時才填）
  train_yolo.py           本機版，委託頁 /downloads/train_yolo.py 下載
  train_yolo_colab.ipynb  Colab 版，委託頁用 colab.research.google.com/github/mz038197/road-test/blob/main/student/… 打開（訓練那一格要跟 train_yolo.py 一樣，有測試）
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

`ROAD_TEST_WORKERS`：同時跑幾段偵測（預設 CPU 核心數 ÷ 2）；`ROAD_TEST_THREADS`：每個 worker 推論用幾個執行緒（預設 CPU 核心數 ÷ worker 數）；`ROAD_TEST_IDLE_MINUTES`：沒人連線、也沒有路考在跑幾分鐘後網站自己結束（預設不結束，Fly 上是 10）；`ROAD_TEST_MAX_UPLOAD_MB`：模型檔大小上限（預設 100，超過回 413）。

`ROAD_TEST_STUDENT`：學生訓練程式的資料夾（預設 repo 的 `student/`；Docker 映像裡是 `/app/student`）。

`ROAD_TEST_TEACHER_PASSWORD`：老師密碼（Fly 上放 secrets），舊版管理頁 `/teacher-password.html` 和組別代碼的 API 用。沒設定的話一律拒絕。組別代碼和老師密碼之後會拿掉。

`VCR_URL`（預設 `https://ai.vanscoding.com`）、`VCR_SERVICE_CREDENTIAL`：網站用 router 的 Key Check 驗課堂金鑰和個人金鑰（`src/road_test/web/key_check.py`，見 `docs/adr/0001-identity-comes-from-router-keys.md`）。通過的結果留 60 秒，最多等 router 3 秒；router 連不上、逾時、5xx、429、401 都回「驗證服務暫時無法使用，請稍後再試」，並送一筆 ERROR 到 vans-signals（只寫金鑰前綴）。本機沒設定 `VCR_SERVICE_CREDENTIAL` 時，router 會回 401，所以金鑰的功能都不能用；測試注入假的驗證器，不用 router。

`VANS_SIGNALS_URL`、`VANS_SIGNALS_TOKEN`：網站的 ERROR 記錄（交件偵測失敗、請求裡沒想到的錯）送到 vans-signals，Slack 上 Source 是 `road-test`（Fly 上放 secrets）。兩個都設定才送，本機不用設定。送的時候不等、約 2 秒逾時、不重送，送失敗不影響使用者。見 `docs/adr/0002-errors-go-to-vans-signals.md`。

### 管理頁 `/teacher.html`

老師貼上自己現有的個人 API Key 登入（不要重新產生，重新產生會讓 VS Code 裡的那把失效）。金鑰只存在這個分頁的 `sessionStorage`，每個請求帶 `Authorization: Bearer`，網站不存金鑰。
看得到的班級是 router 的 Key Check 給的（admin 是所有班級）；沒有可管理的班級時顯示「你沒有可管理的班級」。課堂金鑰不能用管理頁。

- **班級**：選要管理的班級（顯示班級名稱）。
- **正式路考**：這個班級的開放／關閉，別的班級不受影響。
- **組別**：替這個班級新增組別，設定每組幾次正式路考。

### 舊版管理頁 `/teacher-password.html`

用老師密碼登入（密碼只存在這個分頁，關掉分頁就登出），拿掉組別代碼和老師密碼時一起刪掉：

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
  videos/0016E5.mp4    訓練影片（委託頁 /downloads/0016E5.mp4，不用組別代碼）。SAR 已改成 1:1，播放器裡是 4:3
  answers/Seq05VD.json 正式路考的答案（171 個計分幀）
  answers/0006R0.json  模擬路考的答案（101 個計分幀）
  groups.json          網站產生：組別代碼 {"01": "bako-mite", ...}，管理頁產生、重設（組別用英數字，會放在 HTTP 標頭裡）
  road_test_v2.db      網站產生：交件、排隊、偵測結果、成績、組別、正式路考開關（SQLite）。改成依班級分組時重新開始，舊的 road_test.db 不讀、可以刪掉
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


## 部署（Fly.io）

網址：<https://road-test.fly.dev/>。push 到 `main` 就由 GitHub Actions（`.github/workflows/deploy.yml`）跑測試、`flyctl deploy`。也可以在 Actions 頁面按「Run workflow」手動部署。

| 東西 | 在哪裡 |
|---|---|
| 程式、前端、學生訓練程式 | Docker 映像（`Dockerfile`），只有 `pyproject.toml`、`uv.lock`、`src/`、`student/` |
| 路考影片、答案、組別代碼、交件、成績、路考紀錄 | volume `road_test_data`（5 GB，東京 nrt），掛在 `/data` = `ROAD_TEST_DATA`。重新部署不會不見 |
| 老師密碼（舊版，之後拿掉） | Fly secrets `ROAD_TEST_TEACHER_PASSWORD` |
| router 的 Key Check | Fly secrets `VCR_SERVICE_CREDENTIAL`（road-test 的 Service Credential，它的 SHA-256 加進 router 的 `VCR_SERVICE_TOKENS`，名稱 `road-test`）；`VCR_URL` 不設就是 `https://ai.vanscoding.com` |
| 錯誤送到 vans-signals | Fly secrets `VANS_SIGNALS_URL`、`VANS_SIGNALS_TOKEN`（road-test 自己的 token，它的 SHA-256 加進 vans-signals 的 `SIGNALS_TOKENS`，名稱 `road-test`） |
| GitHub Actions 部署用的 token | repo secret `FLY_API_TOKEN`（`flyctl tokens create deploy -a road-test`） |

機器：平常 1 台 `performance-4x`（4 個專用 CPU），2 個 worker × 2 個執行緒。沒人連線、也沒有路考在跑（Fly 的健康檢查打 `/healthz`，不算有人連線），10 分鐘後網站自己結束、機器停下來（按秒計費，停著只收 volume 和映像的儲存費）；有人連進來時 Fly 自動開機，約幾秒。Fly 自己的 auto_stop 是關掉的：它只看連線，學生關掉結果頁時背景還在補幀也會被關掉。

### 常用指令

```powershell
flyctl deploy                                  # 從本機手動部署（在 repo 根目錄）
flyctl logs -a road-test                       # 看紀錄
flyctl machine list -a road-test               # 機器開著還是停著
flyctl secrets set ROAD_TEST_TEACHER_PASSWORD=新密碼 -a road-test   # 換老師密碼（會重開機）
flyctl secrets set VCR_SERVICE_CREDENTIAL=<openssl rand -hex 32 的結果> -a road-test   # 先把它的 SHA-256 加進 router 的 VCR_SERVICE_TOKENS、部署 router
flyctl ssh sftp shell -a road-test             # 上傳／下載 volume 上的檔案（put 本機檔 /data/...）
flyctl ssh console -a road-test                # 進機器
```

換路考影片或答案：`flyctl ssh sftp shell -a road-test` 後 `put 本機檔 /data/videos/Seq05VD.mp4`，再 `flyctl machine restart -a road-test`（影片幀數是開機時讀的）。正式路考影片 Seq05VD 只放 volume，不進映像、不進 git。

### Fly 上的實測（模擬路考 0006R0，yolo26n 四類測試模型）

`performance-4x`（4 核心）：

| worker × 執行緒 | 每個 worker 每幀 | 整台機器每幀 |
|---|---|---|
| 1 × 4 | 52 ms | 52 ms |
| **2 × 2（預設）** | 79 ms | **39 ms** |
| 4 × 1 | 130 ms | 33 ms |

交一次模擬路考（2 × 2）：上傳＋檢查 1 秒，**交件 → 成績 13 秒**，交件 → 3931 幀全部補完 169 秒。4 × 1 整台快一點，但成績要等比較久（計分幀只有一個 worker 在跑），所以預設 2 × 2。

`performance-16x`（16 核心）：

| worker × 執行緒 | 整台機器每幀 |
|---|---|
| 2 × 8 | 24.5 ms |
| 4 × 4 | 16.3 ms |
| **8 × 2（預設）** | **13.0 ms** |

### 正式路考那天：把機器升級成 16 核心

上課前（或開放正式路考前）：

```powershell
flyctl scale vm performance-16x -a road-test    # 約 10 秒，機器會重開一次
```

worker 數會跟著核心數自動變成 8 × 2，不用改設定。下課後改回來：

```powershell
flyctl scale vm performance-4x -a road-test
```

實測（performance-16x，10 組同時交模擬路考 0006R0，3931 幀、101 個計分幀）：**10 組都拿到成績 47 秒，每一幀都補完 520 秒（8.7 分鐘）**；這段時間 CONF 試算（`/rescore`）中位數 191 ms。

換成正式路考（Seq05VD，5101 幀、171 個計分幀，約 1.3 倍）的預估：

| 機器 | 10 組都拿到成績 | 10 組的每一幀都補完（播放完整） | 費用 |
|---|---|---|---|
| performance-4x | 約 2 分鐘 | 10 × 5101 × 39 ms ≈ **34 分鐘** | 平常的價錢 |
| **performance-16x** | 約 1～1.5 分鐘 | 520 秒 × 1.3 ≈ **11～12 分鐘** | 每小時約 0.7 美元，開 2 小時約 1.5 美元 |

不加到 3 台：交件、成績、路考紀錄都在**一台機器的 volume** 上的 SQLite，Fly 的 volume 不能多台共用。多台會各有一份資料，交件落在哪台、查詢就要回哪台，管理頁也看不到全部。升級同一台機器的 CPU 效果一樣（16 核心每幀快 3 倍），資料也不會分散。

注意：`fly.toml` 寫的是 `performance-4x`，**升級期間 push 到 main 的話，部署會把機器改回 4 核心**。正式路考那天不要 push。
