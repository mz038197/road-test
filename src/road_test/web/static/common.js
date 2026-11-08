// 路考網站共用：記住組別和組別代碼、呼叫 API、文字格式
const CLASS_NAMES = { car: "車", pedestrian: "行人", bicyclist: "騎車的人", sign: "號誌" };
const STATUS_NAMES = {
  queued: "排隊中", scoring: "偵測計分幀中", filling: "成績出來了，正在補完其餘的幀",
  done: "全部完成", failed: "出錯了",
};

function loadGroup() {
  try { return JSON.parse(localStorage.getItem("road-test-group")) || {}; } catch { return {}; }
}

function saveGroup(group, code) {
  try { localStorage.setItem("road-test-group", JSON.stringify({ group, code })); } catch { /* 無痕視窗存不了，沒關係 */ }
}

// 老師密碼：舊版管理頁（teacher-password.html）登入後只存在這個分頁（sessionStorage），關掉分頁就要重打。
// 管理頁在同一個分頁打開 result.html，結果頁就帶老師密碼，看得到任一組。
const TEACHER_KEY = "road-test-teacher";

function loadTeacher() {
  try { return sessionStorage.getItem(TEACHER_KEY) || ""; } catch { return ""; }
}

function saveTeacher(password) {
  try {
    if (password) sessionStorage.setItem(TEACHER_KEY, password); else sessionStorage.removeItem(TEACHER_KEY);
  } catch { /* 存不了就每次重打 */ }
}

// router 的金鑰（課堂金鑰或個人金鑰，road-test ADR 0001）：只存在這個分頁（sessionStorage），關掉分頁就不見；
// 不放 cookie、localStorage 或網址。api() 每個請求都帶 Authorization: Bearer。
const API_KEY = "road-test-api-key";

function loadKey() {
  try { return sessionStorage.getItem(API_KEY) || ""; } catch { return ""; }
}

function saveKey(key) {
  try {
    if (key) sessionStorage.setItem(API_KEY, key); else sessionStorage.removeItem(API_KEY);
  } catch { /* 存不了就每次重貼 */ }
}

// 呼叫 API 時帶的身分標頭：有金鑰就帶 Authorization；
// 舊的身分（組別代碼、老師密碼，拿掉前還在用）：老師登入了就帶老師密碼，不然帶記住的組別和組別代碼。
function authHeaders() {
  const key = loadKey();
  const bearer = key ? { Authorization: `Bearer ${key}` } : {};
  const teacher = loadTeacher();
  if (teacher) return { ...bearer, "X-Teacher-Password": teacher };
  const { group, code } = loadGroup();
  return group ? { ...bearer, "X-Group": group, "X-Group-Code": code || "" } : bearer;
}

// 呼叫 API；帶上 authHeaders()。失敗時丟出伺服器給的中文原因
async function api(path, options = {}) {
  const headers = { ...authHeaders(), ...(options.headers || {}) };
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : `伺服器回應 ${response.status}`);
  }
  return body;
}

function clock(seconds) {
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// 考官紀錄一條的文字，例如「行人 漏看 慢 1 秒」
function describe(entry) {
  const cls = CLASS_NAMES[entry.cls] || entry.cls;
  if (entry.kind === "誤判") return `${cls} 誤判`;
  return entry.reaction === null ? `${cls} 漏看（一直沒看見）` : `${cls} 漏看 慢 ${entry.reaction} 秒`;
}

function penalty(p) {
  return `−${Number.isInteger(p) ? p : p.toFixed(1)}`;
}

function when(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW", { hour12: false }) : "";
}

function escapeHtml(s) {
  const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  return String(s).replace(/[&<>"']/g, c => map[c]);
}
