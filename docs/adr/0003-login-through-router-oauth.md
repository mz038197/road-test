# 登入改走 router 的 OAuth：學生的 cookie 存課堂金鑰，老師的 cookie 自己簽章

狀態：已採用。修改 ADR 0001（不再貼金鑰、顯示名稱改用 router 給的、老師不再用個人金鑰）。對應 vans_coding_router 的 ADR 0094、0095、0096。

路考網站改成跟 club-booking-board 一樣有首頁登入，學生和老師不再貼金鑰。路考網站在 router 註冊自己的 OAuth Client，允許 `classroom` 和 `openid teacher` 兩種 scope。

- **學生**：首頁只有一顆「登入」，走 `classroom`，一定要輸入邀請碼，再用暱稱或 Google。router 回來的 access_token 就是這位學生這堂課的課堂金鑰，路考網站把它原樣放進 HttpOnly、Secure、SameSite=Lax 的 cookie，有效到 `expires_in`，也就是這堂課結束，用不到 refresh token。之後每個請求從 cookie 取金鑰，跟以前一樣經 Key Check 驗、留 60 秒。所以學生這邊仍然是「網站不自己發身分」，網站閒置重啟也不受影響；老師在 router 提早結束課堂或停用學生，一分鐘內生效。
- **老師**：沒有任何入口出現在學生看得到的地方，老師直接開 `/admin`，沒登入就轉到 router 的 Google 登入（`openid teacher`，只有教師和 admin 能過）。router 這時不給金鑰，只給一張 ID token，而且只有 5 分鐘效力，存進 cookie 會讓老師每 5 分鐘重登。所以這是 ADR 0001「不發自己的 cookie」的例外：登入時驗過 ID token，路考網站發一張自己簽章的 cookie，12 小時有效，簽章密語放在 Fly secret。能管理哪些班級，每次用 Service Credential 向 router 的 teacher-classes 查，留 60 秒。
- **顯示名稱**：用 Key Check 回的顯示名稱（router ADR 0095），每次驗金鑰時更新。學生選組時不再填名字；是不是同一個人只看 router 的學生 id。
- 沒登入只看得到首頁。委託和訓練影片下載要登入，學生或老師都可以（老師要投影委託頁）；交件只有學生能做。學生的登入只在課堂時間內有效。

## 考慮過的做法

- **學生也發網站自己簽章的 cookie**：不採用。router 給的就是課堂金鑰，直接存它就好，網站少一個要保管的身分，停用也照舊一分鐘生效。
- **老師存 ID token**：不採用。只有 5 分鐘效力。
- **首頁放「老師登入」按鈕，或頁尾放小字連結**：不採用。學生不該看到老師的入口；老師把 `/admin` 存成書籤即可。學生就算找到 `/admin`，router 也不會讓他過。
- **一顆登入，由 router 判斷學生或老師**：不採用。兩種 scope 問的東西不同，要改 router 的登入頁，會影響其他服務。
- **保留貼金鑰當備案**：不採用。兩條路要維護，也容易混淆。
