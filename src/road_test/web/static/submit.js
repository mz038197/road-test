// 交件頁（submit.html）：登入的學生（身分在 HttpOnly cookie，road-test ADR 0003）第一次進來選組別（之後鎖定）
// → 交模擬路考或正式路考、看自己這組的路考紀錄（正式、模擬分開列）。
// 沒登入或登入失效時 api() 會回首頁；router 連不上時留在這頁，可以再試一次。
const $ = id => document.getElementById(id);
const form = $("submit");
const kindInputs = [...form.querySelectorAll('input[name="kind"]')];
const confirmBox = $("confirm");

function kind() {
  return (kindInputs.find(i => i.checked) || kindInputs[0]).value;
}

function show(part) {
  for (const id of ["problem", "students-only", "not-open", "join", "work"]) $(id).hidden = id !== part;
}

function showProblem(message) {
  show("problem");
  $("final-banner").hidden = true;
  $("problem-text").textContent = message;
}

// 進來的人現在是什麼狀態：班級沒有組別、還沒選組、已經選好組
async function enter() {
  let me;
  try {
    me = await api("/api/student");
  } catch (e) {
    if (e.studentsOnly) {
      renderStudentsOnly($("students-only"), e.message);
      show("students-only");
      return;
    }
    showProblem(e.message);
    return;
  }
  if (me.groups.length === 0) {
    show("not-open");
    return;
  }
  if (!me.member) {
    $("join-group").innerHTML = '<option value="">請選擇</option>' +
      me.groups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
    show("join");
    return;
  }
  $("me").textContent = `${me.member.group_name}：${me.teammates.join("、")}`;
  show("work");
  showRoadTests(me);
  loadRecords();
}

// 正式路考開放時只能選正式路考，關著時只能選模擬路考（跟伺服器的規則一樣，開關依班級分開）
function showRoadTests(me) {
  for (const input of kindInputs) {
    const info = me.kinds.find(k => k.kind === input.value);
    input.disabled = !info || !info.open;
    if (!input.disabled) input.checked = true;
  }
  $("final-banner").hidden = !me.final_open;
  $("finals-left").textContent = me.finals_left === null ? "" : `正式路考還剩 ${me.finals_left} 次`;
  $("kind-hint").textContent = me.final_open
    ? "正式路考開放中，模擬路考暫停收件。"
    : "正式路考還沒開放，老師在課堂上開放後才能選。";
  updateKind();
}

async function refreshRoadTests() {
  try { showRoadTests(await api("/api/student")); } catch { /* 下次交件再說 */ }
}

function updateKind() {
  const isFinal = kind() === "final";
  $("confirm-row").hidden = !isFinal;
  confirmBox.required = isFinal;
  if (!isFinal) confirmBox.checked = false;
  $("conf-hint").textContent = isFinal
    ? "正式路考用這裡選的 CONF 計分，交出後就鎖定。不知道怎麼選就用 0.35。"
    : "模擬路考的 CONF 交了之後還可以在結果頁調整。";
  form.querySelector("button[type=submit]").textContent = isFinal ? "交正式路考" : "交模擬路考";
}

kindInputs.forEach(i => i.addEventListener("change", updateKind));

$("retry").addEventListener("click", enter);

$("join").addEventListener("submit", async event => {
  event.preventDefault();
  const button = $("join").querySelector("button[type=submit]");
  const name = $("join-group").selectedOptions[0]?.textContent || "";
  if (!window.confirm(`確定選「${name}」嗎？選了就不能自己改。`)) return;
  button.disabled = true;
  try {
    await api("/api/student/group", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: Number($("join-group").value) }),
    });
    $("join-error").textContent = "";
    await enter();
  } catch (e) {
    $("join-error").textContent = e.message;
  } finally {
    button.disabled = false;
  }
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  const label = button.textContent;
  const error = $("submit-error");
  error.textContent = "";
  if (kind() === "final" &&
      !window.confirm(`確定要交正式路考嗎？交了就不能重交，CONF ${form.conf.value} 交出後就不能改。`)) return;
  button.disabled = true;
  button.textContent = "上傳中…";
  try {
    const body = await api("/api/submissions", { method: "POST", body: new FormData(form) });
    location.href = `result.html?id=${body.id}`;
  } catch (e) {
    error.textContent = `退件：${e.message}`;
    refreshRoadTests();  // 老師可能剛開關正式路考
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
});

function recordRows(records) {
  return records.map(r => `
    <tr>
      <td><a href="result.html?id=${r.id}">${when(r.uploaded_at)}</a></td>
      <td>${escapeHtml(r.display_name ?? "")}</td>
      <td>${escapeHtml(r.filename)}</td>
      <td class="num">${r.conf}</td>
      <td>${STATUS_NAMES[r.status] || r.status}</td>
      <td class="num">${r.score ?? ""}</td>
    </tr>`).join("");
}

async function loadRecords() {
  const error = $("records-error");
  const box = $("records");
  error.textContent = "";
  try {
    const records = await api("/api/student/records");
    for (const k of ["final", "mock"]) {
      const mine = records.filter(r => r.kind === k);
      document.querySelector(`#records-${k} tbody`).innerHTML = recordRows(mine);
      $(`records-${k}`).hidden = mine.length === 0;
      $(`${k}-empty`).hidden = mine.length > 0;
    }
    box.hidden = false;
  } catch (e) {
    error.textContent = e.message;
    box.hidden = true;
  }
}

$("show-records").addEventListener("click", loadRecords);

enter();
