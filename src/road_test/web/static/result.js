// 結果頁：每秒問一次進度；成績出來就列出成績和考官紀錄（#7 再加上影片播放）
const id = new URLSearchParams(location.search).get("id");
let shownResult = false;

async function poll() {
  let s;
  try {
    s = await api(`/api/submissions/${encodeURIComponent(id)}`);
  } catch (e) {
    document.getElementById("status").textContent = "";
    document.getElementById("error").textContent = e.message;
    return;  // 看不到就不再問
  }
  document.getElementById("title").textContent = `第 ${s.group} 組 ${s.kind_name}`;
  document.getElementById("meta").textContent = `${when(s.uploaded_at)} 上傳 ${s.filename}`;

  let status = STATUS_NAMES[s.status] || s.status;
  if (s.status === "queued" && s.position) status += `，排第 ${s.position} 位`;
  document.getElementById("status").textContent = status;
  document.getElementById("error").textContent = s.error || "";

  const bar = document.getElementById("progress");
  bar.hidden = s.status === "queued";
  bar.max = s.progress.total;
  bar.value = s.progress.done;
  document.getElementById("progress-text").textContent =
    s.status === "queued" ? "" : `已偵測 ${s.progress.done} / ${s.progress.total} 幀`;

  if (s.result && !shownResult) {
    showResult(s.result, s);
    shownResult = true;
  }
  if (s.status !== "done" && s.status !== "failed") setTimeout(poll, 1000);
}

function showResult(r, s) {
  document.getElementById("score-card").hidden = false;
  document.getElementById("log-card").hidden = false;
  document.getElementById("score").textContent = `${r.score} 分`;
  document.getElementById("score-detail").textContent =
    `已扣 ${+r.deducted.toFixed(1)} 分，扣滿 ${r.full} 分（CONF ${r.conf}${s.conf_locked ? "，正式路考交件時鎖定" : ""}）`;
  document.getElementById("log-empty").hidden = r.log.length > 0;
  document.getElementById("log").innerHTML = r.log.map(e => `
    <li><span class="t">${clock(e.video_time)}</span>
        <span class="${e.kind === "誤判" ? "false-alarm" : "miss"}">${describe(e)}</span>
        <span class="p">${penalty(e.penalty)}</span></li>`).join("");
}

if (id) poll(); else document.getElementById("error").textContent = "網址少了路考編號。";
