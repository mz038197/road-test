// 結果頁：result.html?id=N。每秒問一次進度；成績出來就開始播放（player.js）。
// 身分標頭由 common.js 的 authHeaders() 決定，所以學生和老師（#10）用的是同一頁。
// 資料都經過 source。模擬路考有 CONF 滑桿：拖動時呼叫 /rescore 重算，拿到新結果就 player.setResult()。
const id = new URLSearchParams(location.search).get("id");
const source = {
  submission: () => api(`/api/submissions/${encodeURIComponent(id)}`),
  detections: () => api(`/api/submissions/${encodeURIComponent(id)}/detections`),
  rescore: conf => api(`/api/submissions/${encodeURIComponent(id)}/rescore?conf=${conf}`),
};
let player = null;
let lastFill = 0;
let shown = null;  // 畫面上的成績：交件的成績，或滑桿試算的成績

async function poll() {
  let s;
  try {
    s = await source.submission();
  } catch (e) {
    document.getElementById("status").textContent = "";
    document.getElementById("error").textContent = e.message;
    return;  // 看不到就不再問
  }
  // 組別的名稱是老師取的（例如「第 1 組」）
  document.getElementById("title").textContent = `${s.group} ${s.kind_name}`;
  document.getElementById("meta").textContent = `${when(s.uploaded_at)} 上傳 ${s.filename}`;
  showStatus(s);

  if (s.result) {
    if (!shown) shown = s.result;
    showScore(shown, s);
    if (!player) {
      player = createPlayer(document.getElementById("player"),
        { videoUrl: s.video_url, detections: await source.detections(), result: shown });
      window.resultPlayer = player;  // 方便在主控台檢查（window.player 會是 id="player" 的元素）
      lastFill = Date.now();
      if (!s.conf_locked) setupSlider(s);  // 正式路考的 CONF 鎖定，沒有滑桿
    } else if (s.status === "done" || Date.now() - lastFill > 15000) {
      // 補幀中每 15 秒更新一次框（0006R0 整份約 3 MB），全部補完再更新最後一次
      lastFill = Date.now();
      player.setDetections(await source.detections());
    }
  }
  if (s.status !== "done" && s.status !== "failed") setTimeout(poll, 1000);
  else document.getElementById("status-card").hidden = s.status === "done";
}

// CONF 滑桿：一次只送一個重算請求，拖動中途的值只留最新的一個，回來後再送最新的。
// 這樣拖動時畫面一直跟著更新（每次約一個來回的時間），又不會塞一堆請求給伺服器。
function setupSlider(s) {
  const slider = document.getElementById("conf");
  const value = document.getElementById("conf-value");
  const reset = document.getElementById("conf-reset");
  const error = document.getElementById("conf-error");
  let wanted = s.conf, busy = false;

  const label = conf => {
    value.textContent = conf.toFixed(2);
    reset.hidden = conf === s.conf;
  };

  async function send() {
    if (busy) return;
    busy = true;
    try {
      while (+shown.conf !== wanted) {
        const conf = wanted;
        const r = conf === s.conf ? s.result : await source.rescore(conf);
        shown = r;
        player.setResult(r);
        showScore(r, s);
        error.textContent = "";
      }
    } catch (e) {
      error.textContent = e.message;
    } finally {
      busy = false;
    }
  }

  const change = conf => { wanted = conf; label(conf); send(); };
  slider.value = s.conf;
  label(s.conf);
  slider.addEventListener("input", () => change(+(+slider.value).toFixed(2)));
  reset.addEventListener("click", () => { slider.value = s.conf; change(s.conf); });
  document.getElementById("conf-slider").hidden = false;
}

function showStatus(s) {
  let status = STATUS_NAMES[s.status] || s.status;
  if (s.status === "queued" && s.position) status += `，排第 ${s.position} 位`;
  document.getElementById("status").textContent = status;
  document.getElementById("error").textContent = s.error || "";
  const bar = document.getElementById("progress");
  bar.hidden = s.status === "queued";
  bar.max = s.progress.total;
  bar.value = s.progress.done;
  document.getElementById("progress-text").textContent =
    s.status === "queued" ? "" : `已偵測 ${s.progress.done} / ${s.progress.total} 幀` +
      (s.status === "filling" ? "（還沒偵測到的幀先不畫框，計分幀的判定都已經有了）" : "");
}

function showScore(r, s) {
  document.getElementById("score-card").hidden = false;
  document.getElementById("score").textContent = `${r.score} 分`;
  const trial = r.conf !== s.conf ? `，試算；交件的成績是 CONF ${s.conf}、${s.result.score} 分` : "";
  document.getElementById("score-detail").textContent =
    `已扣 ${+r.deducted.toFixed(1)} 分，扣滿 ${r.full} 分，考官紀錄 ${r.log.length} 條` +
    `（CONF ${r.conf}${s.conf_locked ? "，正式路考交件時鎖定" : ""}${trial}）`;
}

if (id) poll(); else document.getElementById("error").textContent = "網址少了路考編號。";
