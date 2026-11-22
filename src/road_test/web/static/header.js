// 全站共用的標題列：右邊依 /api/me 顯示身分。名稱一律用 textContent 放進去，不當 HTML。
// 沒登入時什麼都不放：首頁和標題列都沒有老師的入口（road-test ADR 0003）。
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

function renderSiteHeader(actions, me) {
  if (me.role !== "student" && me.role !== "teacher") {
    actions.replaceChildren();
    return;
  }
  const items = [siteHeaderText(me.display_name)];
  if (me.role === "student" && me.group_name) items.push(siteHeaderText(me.group_name));
  if (me.role === "teacher") items.push(siteHeaderLink("委託", "/brief"), siteHeaderLink("後台", "/admin"));
  actions.replaceChildren(...items, siteHeaderLogout());
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { renderSiteHeader };  // node 測試用（tests/js/header.test.js）
} else {
  const siteActions = document.getElementById("site-actions");
  // 問不到身分時標題列留空，頁面照常可用
  if (siteActions) api("/api/me").then(me => renderSiteHeader(siteActions, me)).catch(() => {});
}
