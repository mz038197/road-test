// 路考網站共用：呼叫 API、文字格式
const CLASS_NAMES = { car: "車", pedestrian: "行人", bicyclist: "騎車的人", sign: "號誌" };
const STATUS_NAMES = {
  queued: "排隊中", scoring: "偵測計分幀中", filling: "成績出來了，正在補完其餘的幀",
  done: "全部完成", failed: "出錯了",
};

// 學生和老師的身分都在 HttpOnly cookie 裡，JavaScript 碰不到（road-test ADR 0003）。
// 舊版貼金鑰時可能在這個分頁留下金鑰：一載入就清掉，不留在 JavaScript 讀得到的地方。
try { sessionStorage.removeItem("road-test-api-key"); } catch { /* 讀不到就沒有 */ }

// 這一頁是不是老師從後台來的：後台本身、從後台點進來的頁面，或伺服器說過這位能管理這筆路考。
// 學生進不了後台頁，所以不會被判成這種情況，也就看不到老師的入口。
let backOffice = false;
function rememberBackOffice() { backOffice = true; }

function cameFromBackOffice() {
  try {
    const from = new URL(document.referrer);
    return from.origin === location.origin && from.pathname === "/admin";
  } catch {
    return false;  // 沒有 referrer
  }
}

function loginPage(notice) {
  if (backOffice || location.pathname === "/admin" || cameFromBackOffice()) return "/admin";
  return `/?notice=${encodeURIComponent(notice)}`;
}

// 呼叫 API，cookie 由瀏覽器帶上。失敗時丟出伺服器給的中文原因。
// 沒登入或登入失效時，伺服器在 401 帶 X-Login-Notice：老師回 /admin 重新登入，其他人回首頁顯示提示條
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const notice = response.status === 401 && response.headers?.get("X-Login-Notice");
    if (notice) location.replace(loginPage(notice));
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

// node 測試用（tests/js/common.test.js）；瀏覽器裡沒有 module，什麼都不做
if (typeof module !== "undefined" && module.exports) {
  module.exports = { api, rememberBackOffice };
}
