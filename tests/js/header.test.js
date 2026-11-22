// 全站標題列（static/header.js）：學生、老師、沒登入各看到什麼。
const test = require("node:test");
const assert = require("node:assert/strict");

// 只做標題列用得到的 DOM。innerHTML 一碰就失敗：顯示名稱只能當文字放進去
function fakeElement(tag) {
  const element = {
    tag, children: [], text: "",
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    set textContent(value) { this.text = String(value); },
    get textContent() { return this.text; },
    set innerHTML(_value) { throw new Error("標題列不能用 innerHTML"); },
  };
  return element;
}

function walk(element) {
  return [element, ...element.children.flatMap(walk)];
}

function show(me) {
  globalThis.document = { createElement: fakeElement };
  const file = require.resolve("../../src/road_test/web/static/header.js");
  delete require.cache[file];
  const { renderSiteHeader } = require(file);
  const actions = fakeElement("nav");
  renderSiteHeader(actions, me);
  const all = walk(actions);
  return {
    texts: all.filter(e => e.text).map(e => e.text),
    links: all.filter(e => e.tag === "a").map(e => [e.text, e.href]),
    logout: all.find(e => e.tag === "form"),
  };
}

test("學生：顯示名稱、組別和登出，沒有老師的連結", () => {
  const { texts, links, logout } = show({ role: "student", display_name: "小明", group_name: "第 3 組" });

  assert.deepEqual(texts, ["小明", "第 3 組", "登出"]);
  assert.deepEqual(links, []);
  assert.equal(logout.method, "post");
  assert.equal(logout.action, "/auth/logout");
});

test("還沒選組的學生：只顯示名稱和登出", () => {
  const { texts } = show({ role: "student", display_name: "小明", group_name: null });

  assert.deepEqual(texts, ["小明", "登出"]);
});

test("老師：顯示名稱、委託、後台和登出", () => {
  const { texts, links, logout } = show({ role: "teacher", display_name: "陳老師" });

  assert.deepEqual(texts, ["陳老師", "委託", "後台", "登出"]);
  assert.deepEqual(links, [["委託", "/brief"], ["後台", "/admin"]]);
  assert.equal(logout.action, "/auth/logout");
});

test("沒登入：不顯示身分，也沒有老師入口", () => {
  const { texts, links, logout } = show({ role: null });

  assert.deepEqual(texts, []);
  assert.deepEqual(links, []);
  assert.equal(logout, undefined);
});

test("老師開交件頁：說明只有學生能交件，並有回後台的連結", () => {
  globalThis.document = { createElement: fakeElement };
  const file = require.resolve("../../src/road_test/web/static/header.js");
  delete require.cache[file];
  const { renderStudentsOnly } = require(file);
  const box = fakeElement("section");

  renderStudentsOnly(box, "只有學生能交件");

  const all = walk(box);
  assert.ok(all.some(e => e.text === "只有學生能交件"));
  assert.deepEqual(all.filter(e => e.tag === "a").map(e => [e.text, e.href]), [["回後台", "/admin"]]);
});

test("顯示名稱裡的 HTML 只當文字", () => {
  const name = '<img src=x onerror="alert(1)">';

  const { texts } = show({ role: "student", display_name: name, group_name: "<b>組</b>" });

  assert.deepEqual(texts, [name, "<b>組</b>", "登出"]);
});
