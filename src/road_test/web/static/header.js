// 全站共用的標題列：右邊依 /api/me 顯示身分。名稱一律用 textContent 放進去，不當 HTML。
// 沒登入時什麼都不放：首頁和標題列都沒有老師的入口（road-test ADR 0003）。
// router 故障時 /api/me 帶 message：顯示原因，不要看起來像被登出。
function siteHeaderText(text) {
  const span = document.createElement("span");
  span.textContent = text;
  return span;
}

function siteHeaderLink(text, href) {
  const link = document.createElement("a");
  link.textContent = text;
  link.href = href;
  return link;
}

function siteHeaderLogout() {
  const form = document.createElement("form");
  form.method = "post";
  form.action = "/auth/logout";
  const button = document.createElement("button");
  button.className = "secondary small";
  button.textContent = "登出";
  form.append(button);
  return form;
}

function renderSiteHeader(actions, me, onHome = false) {
  if (me.role !== "student" && me.role !== "teacher") {
    actions.replaceChildren(...(me.message ? [siteHeaderText(me.message)] : []));
    return;
  }
  const items = [siteHeaderText(me.display_name)];
  if (me.role === "student" && me.group_name) items.push(siteHeaderText(me.group_name));
  if (me.role === "teacher") items.push(siteHeaderLink("委託", "/brief"), siteHeaderLink(onHome ? "進入後台" : "後台", "/admin"));
  actions.replaceChildren(...items, siteHeaderLogout());
}

// 伺服器確認是登入的老師才會呼叫：後台連結不寫在 submit.html 裡，學生看不到
function renderStudentsOnly(box, message) {
  const text = document.createElement("p");
  text.textContent = message;
  const back = siteHeaderLink("回後台", "/admin");
  back.className = "button";
  box.replaceChildren(text, back);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { renderSiteHeader, renderStudentsOnly };  // node 測試用（tests/js/header.test.js）
} else {
  const siteActions = document.getElementById("site-actions");
  // 問不到身分時標題列留空，頁面照常可用
  if (siteActions) api("/api/me").then(me => renderSiteHeader(siteActions, me, location.pathname === "/")).catch(() => {});
}
