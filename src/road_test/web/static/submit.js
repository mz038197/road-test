// 交件頁（submit.html）：交模擬路考或正式路考、看自己這組的路考紀錄（正式、模擬分開列）
const form = document.getElementById("submit");
const groupSelect = document.getElementById("group");
const codeInput = document.getElementById("code");
const kindInputs = [...form.querySelectorAll('input[name="kind"]')];
const confirmBox = document.getElementById("confirm");

function kind() {
  return (kindInputs.find(i => i.checked) || kindInputs[0]).value;
}

// 正式路考開放時只能選正式路考，關著時只能選模擬路考（跟伺服器的規則一樣）
async function loadRoadTests() {
  let state;
  try {
    state = await api("/api/road-tests");
  } catch (e) {
    document.getElementById("submit-error").textContent = e.message;
    return;
  }
  for (const input of kindInputs) {
    const info = state.kinds.find(k => k.kind === input.value);
    input.disabled = !info || !info.open;
    if (!input.disabled) input.checked = true;
  }
  document.getElementById("final-banner").hidden = !state.final_open;
  document.getElementById("kind-hint").textContent = state.final_open
    ? "正式路考開放中，模擬路考暫停收件。"
    : "正式路考還沒開放，老師在課堂上開放後才能選。";
  updateKind();
}

function updateKind() {
  const isFinal = kind() === "final";
  document.getElementById("confirm-row").hidden = !isFinal;
  confirmBox.required = isFinal;
  if (!isFinal) confirmBox.checked = false;
  document.getElementById("conf-hint").textContent = isFinal
    ? "正式路考用這裡選的 CONF 計分，交出後就鎖定。不知道怎麼選就用 0.35。"
    : "模擬路考的 CONF 交了之後還可以在結果頁調整。";
  form.querySelector("button[type=submit]").textContent = isFinal ? "交正式路考" : "交模擬路考";
}

async function init() {
  const saved = loadGroup();
  try {
    const groups = await api("/api/groups");
    groupSelect.innerHTML = '<option value="">請選擇</option>' +
      groups.map(g => `<option value="${escapeHtml(g)}">第 ${escapeHtml(g)} 組</option>`).join("");
  } catch (e) {
    document.getElementById("submit-error").textContent = e.message;
  }
  if (saved.group) groupSelect.value = saved.group;
  if (saved.code) codeInput.value = saved.code;
  await loadRoadTests();
}

kindInputs.forEach(i => i.addEventListener("change", updateKind));

form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button[type=submit]");
  const label = button.textContent;
  const error = document.getElementById("submit-error");
  error.textContent = "";
  if (kind() === "final" &&
      !window.confirm(`確定要交正式路考嗎？每組只能交一次，CONF ${form.conf.value} 交出後就不能改。`)) return;
  button.disabled = true;
  button.textContent = "上傳中…";
  try {
    const body = await api("/api/submissions", { method: "POST", body: new FormData(form) });
    saveGroup(groupSelect.value, codeInput.value);
    location.href = `result.html?id=${body.id}`;
  } catch (e) {
    error.textContent = `退件：${e.message}`;
    loadRoadTests();  // 老師可能剛開關正式路考
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
});

function recordRows(records) {
  return records.map(r => `
    <tr>
      <td><a href="result.html?id=${r.id}">${when(r.uploaded_at)}</a></td>
      <td>${escapeHtml(r.filename)}</td>
      <td class="num">${r.conf}</td>
      <td>${STATUS_NAMES[r.status] || r.status}</td>
      <td class="num">${r.score ?? ""}</td>
    </tr>`).join("");
}

document.getElementById("show-records").addEventListener("click", async () => {
  const error = document.getElementById("records-error");
  const box = document.getElementById("records");
  error.textContent = "";
  saveGroup(groupSelect.value, codeInput.value);
  try {
    const records = await api(`/api/groups/${encodeURIComponent(groupSelect.value)}/records`);
    document.getElementById("records-hint").textContent = "";
    for (const k of ["final", "mock"]) {
      const mine = records.filter(r => r.kind === k);
      document.querySelector(`#records-${k} tbody`).innerHTML = recordRows(mine);
      document.getElementById(`records-${k}`).hidden = mine.length === 0;
      document.getElementById(`${k}-empty`).hidden = mine.length > 0;
    }
    box.hidden = false;
  } catch (e) {
    error.textContent = e.message;
    box.hidden = true;
  }
});

init();
