# 錯誤送到 vans-signals

狀態：已採用。做法照 vans_coding_router 的 ADR 0033（router 把 `src` 底下的 ERROR 送到 vans-signals）。

路考網站在 Fly 上閒置就關機，關機之後記錄就看不到了。所以 `road_test` 底下每一筆 ERROR 記錄送一筆 Signal 到 vans-signals，Slack 上 Source 是 `road-test`。Signal 的內容跟 router 一樣：記錄時間、logger 名稱、等級、訊息、source；有例外時訊息後面接 traceback。請求裡沒想到的錯由網站自己記一筆 ERROR（方法和路徑，不含查詢字串，因為影片網址帶簽章），回 `{"detail": "Internal server error"}`。uvicorn 自己的記錄、WARNING 都不送。

`VANS_SIGNALS_URL` 和 `VANS_SIGNALS_TOKEN` 兩個都設定才送，本機開發不設定就什麼都不送。token 是 road-test 自己的，不跟 router 共用。送的時候開背景執行緒，使用者的請求不等；大約 2 秒逾時、不重送；送失敗只記在 `vans_signals_forwarder`（不在 `road_test` 底下，不會再送一次）。網站結束時等還在送的 Signal 送完。用標準函式庫的 `urllib` 送，網站不多一個套件。之後 key-check 用戶端失敗的 Signal 也走這條。

## 考慮過的做法

- **跟 router 共用 token**：不採用。Slack 上分不出是誰的錯，撤銷也會一起撤。
- **做成共用套件**：不採用。程式只有幾十行，router ADR 0082 也選了各自寫。
- **請求等 vans-signals 回應、送失敗重送**：不採用，理由跟 router ADR 0033 相同：vans-signals 掛了會拖慢上課、堆積工作。
