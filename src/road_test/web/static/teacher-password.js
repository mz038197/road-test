// 舊版管理頁（老師密碼）：正式路考開關、組別代碼、還正式路考次數、所有組的路考紀錄（播放）、清空。拿掉組別代碼和老師密碼時（#104）一起刪掉。
// 老師密碼存在這個分頁的 sessionStorage（common.js 的 saveTeacher），api() 會自動帶上，
// 所以在同一個分頁打開 result.html?id=N，結果頁也是老師的身分，看得到任一組。
// 再確認都用頁面裡的確認框，不用 window.confirm()。
const $ = id => document.getElementById(id);
let records = [];

function say(text) {
  $("notice").textContent = text;
  $("error").textContent = "";
}

function fail(e) {
  $("notice").textContent = "";
  $("error").textContent = e.message;
}

function post(path, body, method = "POST") {
  return api(path, body === undefined ? { method } : {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
}

// 頁面裡的確認框：按鈕 → 出現確認框 → 「確定」才做 action
function confirmWith(buttonId, boxId, action, prepare = () => {}) {
  const box = $(boxId);
  $(buttonId).addEventListener("click", () => { prepare(); box.hidden = false; });
  box.querySelector("[data-no]").addEventListener("click", () => { box.hidden = true; });
  box.querySelector("[data-yes]").addEventListener("click", async () => {
    box.hidden = true;
    try { await action(); } catch (e) { fail(e); }
  });
}

// 登入
async function login() {
  try {
    showFinal(await api("/api/teacher/final"));  // 順便驗密碼
  } catch (e) {
    saveTeacher("");
    $("login").hidden = false;
    $("admin").hidden = true;
    $("login-error").textContent = e.message;
    return;
  }
  $("login").hidden = true;
  $("admin").hidden = false;
  $("login-error").textContent = "";
  await Promise.all([loadGroups(), loadRecords()]).catch(fail);
}

$("login").addEventListener("submit", event => {
  event.preventDefault();
  saveTeacher($("password").value);
  login();
});
$("logout").addEventListener("click", () => {
  saveTeacher("");
  $("password").value = "";
  $("admin").hidden = true;
  $("login").hidden = false;
});

// 正式路考開關
function showFinal(state) {
  $("final-state").textContent = state.final_open ? "開放中" : "關閉";
  $("final-open").disabled = state.final_open;
  $("final-close").disabled = !state.final_open;
}

$("final-open").addEventListener("click", async () => {
  try { showFinal(await post("/api/teacher/final", { open: true }, "PUT")); say("正式路考開放了。"); } catch (e) { fail(e); }
});
confirmWith("final-close", "final-close-confirm", async () => {
  showFinal(await post("/api/teacher/final", { open: false }, "PUT"));
  say("正式路考關閉了。");
});

// 組別與組別代碼
function showGroups(table) {
  $("groups").querySelector("tbody").innerHTML = table.map(g => `
    <tr>
      <td>第 ${escapeHtml(g.group)} 組</td>
      <td class="code">${escapeHtml(g.code)}</td>
      <td class="num">${g.finals_left} 次</td>
      <td class="actions">
        <button type="button" class="secondary small" data-reset="${escapeHtml(g.group)}">重設代碼</button>
        <button type="button" class="secondary small" data-return="${escapeHtml(g.group)}">還一次正式路考</button>
      </td>
    </tr>`).join("");
  $("groups").hidden = table.length === 0;
  $("no-groups").hidden = table.length > 0;
  if (table.length) $("group-count").value = table.length;
  const filter = $("filter");
  const chosen = filter.value;
  filter.innerHTML = '<option value="">全部</option>' +
    table.map(g => `<option value="${escapeHtml(g.group)}">第 ${escapeHtml(g.group)} 組</option>`).join("");
  filter.value = table.some(g => g.group === chosen) ? chosen : "";
}

async function loadGroups() {
  showGroups(await api("/api/teacher/groups"));
}

$("groups").addEventListener("click", async event => {
  const reset = event.target.dataset.reset;
  const back = event.target.dataset.return;
  try {
    if (reset) {
      const g = await post(`/api/teacher/groups/${encodeURIComponent(reset)}/code`);
      say(`第 ${g.group} 組的新代碼：${g.code}（舊代碼已失效）`);
    } else if (back) {
      const g = await post(`/api/teacher/groups/${encodeURIComponent(back)}/final-returns`);
      say(`還給第 ${g.group} 組一次正式路考，現在還能交 ${g.finals_left} 次。`);
    } else {
      return;
    }
    await loadGroups();
  } catch (e) { fail(e); }
});

$("set-count").addEventListener("click", async () => {
  try {
    showGroups(await post("/api/teacher/groups", { count: Number($("group-count").value) }, "PUT"));
    say(`組別改成 ${$("group-count").value} 組。`);
  } catch (e) { fail(e); }
});
confirmWith("reset-all", "reset-all-confirm", async () => {
  showGroups(await post("/api/teacher/codes"));
  say("每組都換了新代碼，舊代碼全部失效。");
});

// 路考紀錄
function showRecords() {
  const group = $("filter").value;
  const rows = records.filter(r => !group || r.group === group);
  $("records").querySelector("tbody").innerHTML = rows.map(r => `
    <tr>
      <td>第 ${escapeHtml(r.group)} 組</td>
      <td>${escapeHtml(r.kind_name)}</td>
      <td>${when(r.uploaded_at)}</td>
      <td>${escapeHtml(r.filename)}</td>
      <td class="num">${r.conf}</td>
      <td>${STATUS_NAMES[r.status] || r.status}</td>
      <td class="num">${r.score ?? ""}</td>
      <td><a href="result.html?id=${r.id}">播放</a></td>
    </tr>`).join("");
  $("records").hidden = rows.length === 0;
  $("no-records").hidden = rows.length > 0;
}

async function loadRecords() {
  records = await api("/api/teacher/records");
  showRecords();
}

$("filter").addEventListener("change", showRecords);
$("reload").addEventListener("click", () => Promise.all([loadRecords(), loadGroups()]).catch(fail));

// 清空（再確認一次，伺服器也要 confirm: "清空" 才做）
confirmWith("clear", "clear-confirm", async () => {
  await post("/api/teacher/clear", { confirm: "清空" });
  await Promise.all([loadRecords(), loadGroups()]);
  say("清空了。組別代碼和正式路考開關沒有動。");
}, () => { $("clear-count").textContent = records.length; });

if (loadTeacher()) login();
