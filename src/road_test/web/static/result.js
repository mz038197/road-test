// 結果頁：result.html?id=N。每秒問一次進度；成績出來就開始播放（player.js）。
// 身分標頭由 common.js 的 authHeaders() 決定，所以學生和老師（#10）用的是同一頁。
// 資料都經過 source，#8 的 CONF 滑桿重算時，拿到新的結果呼叫 player.setResult() 就好。
const id = new URLSearchParams(location.search).get("id");
const source = {
  submission: () => api(`/api/submissions/${encodeURIComponent(id)}`),
  detections: () => api(`/api/submissions/${encodeURIComponent(id)}/detections`),
};
let player = null;
let lastFill = 0;

async function poll() {
  let s;
  try {
    s = await source.submission();
  } catch (e) {
    document.getElementById("status").textContent = "";
    document.getElementById("error").textContent = e.message;
    return;  // 看不到就不再問
  }
  document.getElementById("title").textContent = `第 ${s.group} 組 ${s.kind_name}`;
  document.getElementById("meta").textContent = `${when(s.uploaded_at)} 上傳 ${s.filename}`;
  showStatus(s);

  if (s.result) {
    showScore(s.result, s);
    if (!player) {
      player = createPlayer(document.getElementById("player"),
        { videoUrl: s.video_url, detections: await source.detections(), result: s.result });
      window.resultPlayer = player;  // 方便在主控台檢查（window.player 會是 id="player" 的元素）
      lastFill = Date.now();
    } else if (s.status === "done" || Date.now() - lastFill > 15000) {
      // 補幀中每 15 秒更新一次框（0006R0 整份約 3 MB），全部補完再更新最後一次
      lastFill = Date.now();
      player.setDetections(await source.detections());
    }
  }
  if (s.status !== "done" && s.status !== "failed") setTimeout(poll, 1000);
  else document.getElementById("status-card").hidden = s.status === "done";
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
  document.getElementById("score-detail").textContent =
    `已扣 ${+r.deducted.toFixed(1)} 分，扣滿 ${r.full} 分，考官紀錄 ${r.log.length} 條` +
    `（CONF ${r.conf}${s.conf_locked ? "，正式路考交件時鎖定" : ""}）`;
}

if (id) poll(); else document.getElementById("error").textContent = "網址少了路考編號。";
