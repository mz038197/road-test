// 金鑰和 API 呼叫（static/common.js）的測試：node --test tests/js/*.test.js
// 金鑰只放在 sessionStorage（關掉分頁就不見），每個請求都帶 Authorization: Bearer（road-test ADR 0001）。
const test = require("node:test");
const assert = require("node:assert/strict");

function fakeStorage() {
  const items = new Map();
  return {
    getItem: k => (items.has(k) ? items.get(k) : null),
    setItem: (k, v) => items.set(k, String(v)),
    removeItem: k => items.delete(k),
    get size() { return items.size; },
  };
}

// 每個測試一個乾淨的瀏覽器：sessionStorage、localStorage、document.cookie、fetch 都是假的
function browser(answer = { status: 200, body: {} }) {
  const requests = [];
  globalThis.sessionStorage = fakeStorage();
  globalThis.localStorage = fakeStorage();
  globalThis.document = { cookie: "" };
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, headers: { ...(options.headers || {}) } });
    return { ok: answer.status < 400, status: answer.status, json: async () => answer.body };
  };
  delete require.cache[require.resolve("../../src/road_test/web/static/common.js")];
  return { C: require("../../src/road_test/web/static/common.js"), requests };
}

const KEY = "vcr_sk_class0" + "c".repeat(40);

test("金鑰存在 sessionStorage，不放 localStorage 或 cookie", () => {
  const { C } = browser();
  C.saveKey(`  ${KEY}  `);

  assert.equal(C.loadKey(), KEY, "前後的空白去掉");
  assert.equal(sessionStorage.getItem("road-test-api-key"), KEY);
  assert.equal(localStorage.size, 0);
  assert.equal(document.cookie, "");
});

test("清掉金鑰之後就不見了", () => {
  const { C } = browser();
  C.saveKey(KEY);
  C.saveKey("");

  assert.equal(C.loadKey(), "");
  assert.equal(sessionStorage.size, 0);
});

test("每個請求都帶 Authorization: Bearer 金鑰", async () => {
  const { C, requests } = browser();
  C.saveKey(KEY);

  await C.api("/api/student");
  await C.api("/api/submissions", { method: "POST", headers: { "X-Other": "1" } });

  assert.equal(requests.length, 2);
  for (const r of requests) assert.equal(r.headers.Authorization, `Bearer ${KEY}`, r.path);
  assert.equal(requests[1].headers["X-Other"], "1", "呼叫的人自己加的標頭也留著");
});

test("被拒時丟出 router 給的訊息", async () => {
  const { C } = browser({ status: 401, body: { detail: "無效的 API 金鑰" } });
  C.saveKey(KEY);

  await assert.rejects(C.api("/api/student"), { message: "無效的 API 金鑰" });
});

test("只帶金鑰，不帶舊的老師密碼或組別代碼標頭", async () => {
  const { C, requests } = browser();
  sessionStorage.setItem("road-test-teacher", "teacher-secret");  // 舊版管理頁留下來的
  C.saveKey(KEY);

  await C.api("/api/teacher/classes");

  assert.deepEqual(Object.keys(requests[0].headers), ["Authorization"]);
});
