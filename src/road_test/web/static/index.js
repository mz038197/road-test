// 交件頁：交模擬路考、看自己這組的路考紀錄
const form = document.getElementById("submit");
const groupSelect = document.getElementById("group");
const codeInput = document.getElementById("code");

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
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const button = form.querySelector("button");
  const error = document.getElementById("submit-error");
  error.textContent = "";
  button.disabled = true;
  button.textContent = "上傳中…";
  try {
    const body = await api("/api/submissions", { method: "POST", body: new FormData(form) });
    saveGroup(groupSelect.value, codeInput.value);
    location.href = `result.html?id=${body.id}`;
  } catch (e) {
    error.textContent = `退件：${e.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "交件";
  }
});

document.getElementById("show-records").addEventListener("click", async () => {
  const error = document.getElementById("records-error");
  const table = document.getElementById("records");
  error.textContent = "";
  saveGroup(groupSelect.value, codeInput.value);
  try {
    const records = await api(`/api/groups/${encodeURIComponent(groupSelect.value)}/records`);
    document.getElementById("records-hint").textContent = records.length ? "" : "還沒有路考紀錄。";
    table.querySelector("tbody").innerHTML = records.map(r => `
      <tr>
        <td><a href="result.html?id=${r.id}">${when(r.uploaded_at)}</a></td>
        <td>${r.kind_name}</td>
        <td>${escapeHtml(r.filename)}</td>
        <td>${STATUS_NAMES[r.status] || r.status}</td>
        <td class="num">${r.score ?? ""}</td>
      </tr>`).join("");
    table.hidden = records.length === 0;
  } catch (e) {
    error.textContent = e.message;
    table.hidden = true;
  }
});

init();
