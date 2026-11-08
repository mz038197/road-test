// 管理頁：老師用個人 API Key 登入，管理自己班級的組別（每組幾次正式路考）、組員、正式路考開關，
// 看這個班級的路考紀錄（每筆是誰交的）並播放任一組的結果（road-test ADR 0001）。
// 金鑰存在這個分頁的 sessionStorage（common.js 的 saveKey），api() 每個請求都帶 Authorization: Bearer。
// 班級來自 router 的 Key Check（admin 是所有班級）；沒有可管理的班級時，伺服器回「你沒有可管理的班級」。
const $ = id => document.getElementById(id);
let classId = null;
let groups = [];
let records = [];

function say(text) {
  $("notice").textContent = text;
  $("error").textContent = "";
}

function fail(e) {
  $("notice").textContent = "";
  $("error").textContent = e.message;
}

function send(path, body, method = "POST") {
  return api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function showLogin(message) {
  $("login").hidden = false;
  $("admin").hidden = true;
  $("login-error").textContent = message || "";
}

// 登入：拿得到班級才進老師畫面
async function login() {
  let mine;
  try {
    mine = await api("/api/teacher/classes");
  } catch (e) {
    saveKey("");
    showLogin(e.message);
    return;
  }
  $("login").hidden = true;
  $("admin").hidden = false;
  $("login-error").textContent = "";
  const select = $("class");
  select.innerHTML = mine.classes.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  await openClass(Number(select.value)).catch(fail);
}

$("login").addEventListener("submit", event => {
  event.preventDefault();
  saveKey($("api-key").value.trim());
  $("api-key").value = "";
  login();
});
$("logout").addEventListener("click", () => {
  saveKey("");
  showLogin("");
});
$("class").addEventListener("change", () => openClass(Number($("class").value)).catch(fail));

// 一個班級：正式路考開關、組別、組員、路考紀錄
async function openClass(id) {
  classId = id;
  showClass(await api(`/api/teacher/classes/${id}`));
  await loadRecords();
}

function showClass(cls) {
  groups = cls.groups;
  $("final-state").textContent = cls.final_open ? "開放中" : "關閉";
  $("final-open").disabled = cls.final_open;
  $("final-close").disabled = !cls.final_open;
  $("groups").querySelector("tbody").innerHTML = cls.groups.map(g => `
    <tr>
      <td>${escapeHtml(g.name)}</td>
      <td>${g.members.map(m => escapeHtml(m.display_name)).join("、") || '<span class="muted">還沒有人</span>'}</td>
      <td class="num"><input type="number" min="1" max="99" step="1" value="${g.final_tries}"
        aria-label="${escapeHtml(g.name)}的正式路考次數" data-tries="${g.id}"></td>
      <td class="actions"><button type="button" class="secondary small" data-save="${g.id}">儲存次數</button></td>
    </tr>`).join("");
  $("groups").hidden = cls.groups.length === 0;
  $("no-groups").hidden = cls.groups.length > 0;
  showMembers();
  const filter = $("filter");
  const chosen = filter.value;
  filter.innerHTML = '<option value="">全部</option>'
    + cls.groups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
  if (cls.groups.some(g => String(g.id) === chosen)) filter.value = chosen;
}

// 組員：每個學生一列，選另一組再按「改組」
function showMembers() {
  const members = groups.flatMap(g => g.members.map(m => ({ ...m, group_id: g.id })));
  $("members").querySelector("tbody").innerHTML = members.map(m => `
    <tr>
      <td>${escapeHtml(m.display_name)}</td>
      <td><select data-group-of="${m.student_id}" aria-label="${escapeHtml(m.display_name)}的組別">
        ${groups.map(g => `<option value="${g.id}"${g.id === m.group_id ? " selected" : ""}>${escapeHtml(g.name)}</option>`).join("")}
      </select></td>
      <td class="actions"><button type="button" class="secondary small" data-move="${m.student_id}">改組</button></td>
    </tr>`).join("");
  $("members").hidden = members.length === 0;
  $("no-members").hidden = members.length > 0;
}

$("members").addEventListener("click", async event => {
  const student = event.target.dataset.move;
  if (!student) return;
  const groupId = Number($("members").querySelector(`[data-group-of="${student}"]`).value);
  try {
    const m = await send(`/api/teacher/classes/${classId}/members/${student}`, { group_id: groupId }, "PUT");
    await openClass(classId);
    say(`「${m.display_name}」改到「${m.group_name}」。`);
  } catch (e) { fail(e); }
});

// 路考紀錄：這個班級所有組，新的在前面；「播放」在同一個分頁打開結果頁（金鑰在這個分頁的 sessionStorage）
function showRecords() {
  const group = $("filter").value;
  const rows = records.filter(r => !group || String(r.group_id) === group);
  $("records").querySelector("tbody").innerHTML = rows.map(r => `
    <tr>
      <td>${escapeHtml(r.group)}</td>
      <td>${escapeHtml(r.display_name || "")}</td>
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
  records = await api(`/api/teacher/classes/${classId}/records`);
  showRecords();
}

$("filter").addEventListener("change", showRecords);
$("reload").addEventListener("click", () => openClass(classId).catch(fail));


$("final-open").addEventListener("click", async () => {
  try {
    showClass(await send(`/api/teacher/classes/${classId}/final`, { open: true }, "PUT"));
    say("這個班級的正式路考開放了。");
  } catch (e) { fail(e); }
});

const closeBox = $("final-close-confirm");
$("final-close").addEventListener("click", () => { closeBox.hidden = false; });
closeBox.querySelector("[data-no]").addEventListener("click", () => { closeBox.hidden = true; });
closeBox.querySelector("[data-yes]").addEventListener("click", async () => {
  closeBox.hidden = true;
  try {
    showClass(await send(`/api/teacher/classes/${classId}/final`, { open: false }, "PUT"));
    say("這個班級的正式路考關閉了。");
  } catch (e) { fail(e); }
});

$("new-group").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const g = await send(`/api/teacher/classes/${classId}/groups`,
      { name: $("group-name").value.trim(), final_tries: Number($("group-tries").value) });
    $("group-name").value = "";
    await openClass(classId);
    say(`新增了「${g.name}」，正式路考 ${g.final_tries} 次。`);
  } catch (e) { fail(e); }
});

$("groups").addEventListener("click", async event => {
  const id = event.target.dataset.save;
  if (!id) return;
  const tries = Number($("groups").querySelector(`[data-tries="${id}"]`).value);
  try {
    const g = await send(`/api/teacher/classes/${classId}/groups/${id}`, { final_tries: tries }, "PUT");
    await openClass(classId);
    say(`「${g.name}」的正式路考改成 ${g.final_tries} 次。`);
  } catch (e) { fail(e); }
});

if (loadKey()) login(); else showLogin("");
