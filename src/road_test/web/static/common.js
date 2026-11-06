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

// 呼叫 API；帶上記住的組別和組別代碼。失敗時丟出伺服器給的中文原因
async function api(path, options = {}) {
  const { group, code } = loadGroup();
  const headers = { ...(options.headers || {}) };
  if (group) { headers["X-Group"] = group; headers["X-Group-Code"] = code || ""; }
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
