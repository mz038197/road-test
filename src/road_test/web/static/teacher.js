// 老師開關正式路考（完整的管理頁在 #10）。老師密碼只存在這個分頁（sessionStorage），關掉分頁就要重打
const KEY = "road-test-teacher";
const passwordInput = document.getElementById("password");

function password() {
  try { return sessionStorage.getItem(KEY) || passwordInput.value; } catch { return passwordInput.value; }
}

async function teacherApi(path, options = {}) {
  return api(path, { ...options, headers: { ...(options.headers || {}), "X-Teacher-Password": password() } });
}

function show(state) {
  document.getElementById("state").textContent = state.final_open ? "開放中" : "關閉";
  document.getElementById("open").disabled = state.final_open;
  document.getElementById("close").disabled = !state.final_open;
}

async function refresh() {
  const error = document.getElementById("error");
  error.textContent = "";
  try {
    show(await teacherApi("/api/teacher/final"));
  } catch (e) {
    error.textContent = e.message;
    document.getElementById("open").disabled = document.getElementById("close").disabled = true;
  }
}

async function setFinal(open) {
  const error = document.getElementById("error");
  error.textContent = "";
  try {
    show(await teacherApi("/api/teacher/final", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ open }),
    }));
  } catch (e) {
    error.textContent = e.message;
  }
}

document.getElementById("login").addEventListener("submit", event => {
  event.preventDefault();
  try { sessionStorage.setItem(KEY, passwordInput.value); } catch { /* 存不了就只用輸入框的值 */ }
  refresh();
});
document.getElementById("open").addEventListener("click", () => setFinal(true));
document.getElementById("close").addEventListener("click", () => {
  if (window.confirm("確定要關閉正式路考嗎？之後交的正式路考會被退件。")) setFinal(false);
});

if (password()) refresh();
