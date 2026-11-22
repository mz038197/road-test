// API 呼叫（static/common.js）的測試：node --test tests/js/*.test.js
// 身分都在 HttpOnly cookie 裡（road-test ADR 0003），JavaScript 不碰金鑰，也不帶 Authorization。
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

// 每個測試一個乾淨的瀏覽器：sessionStorage、fetch、location 都是假的；oldKey 是舊版留在分頁裡的金鑰
function browser(answer = { status: 200, body: {} }, oldKey = "") {
  const requests = [];
  globalThis.sessionStorage = fakeStorage();
  if (oldKey) sessionStorage.setItem("road-test-api-key", oldKey);
  const visited = [];
  globalThis.location = { replace: url => visited.push(url) };
  const answerHeaders = new Map(Object.entries(answer.headers || {}));
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, headers: { ...(options.headers || {}) } });
    return {
      ok: answer.status < 400, status: answer.status, json: async () => answer.body,
      headers: { get: name => answerHeaders.get(name) ?? null },
    };
  };
  delete require.cache[require.resolve("../../src/road_test/web/static/common.js")];
  return { C: require("../../src/road_test/web/static/common.js"), requests, visited };
}

const KEY = "vcr_sk_teacher" + "t".repeat(40);

test("舊版留在分頁裡的金鑰一載入就清掉，也不會送出去", async () => {
  const { C, requests } = browser({ status: 200, body: {} }, KEY);

  await C.api("/api/teacher/classes");

  assert.equal(sessionStorage.getItem("road-test-api-key"), null);
  assert.deepEqual(requests[0].headers, {});
});

test("呼叫的人自己加的標頭照樣送出", async () => {
  const { C, requests } = browser();

  await C.api("/api/submissions", { method: "POST", headers: { "X-Other": "1" } });

  assert.deepEqual(requests[0].headers, { "X-Other": "1" });
});

test("被拒時丟出伺服器給的訊息", async () => {
  const { C } = browser({ status: 403, body: { detail: "你沒有可管理的班級" } });

  await assert.rejects(C.api("/api/teacher/classes"), { message: "你沒有可管理的班級" });
});

for (const [who, path, notice] of [["學生", "/api/student", "expired"], ["老師", "/api/teacher/classes", "login_required"]]) {
  test(`${who}的登入失效時回首頁，帶伺服器給的提示代碼`, async () => {
    const { C, visited } = browser({ status: 401, body: { detail: "請重新登入" }, headers: { "X-Login-Notice": notice } });

    await assert.rejects(C.api(path));

    assert.deepEqual(visited, [`/?notice=${notice}`]);
  });
}

test("401 沒帶提示代碼時留在原頁", async () => {
  const { C, visited } = browser({ status: 401, body: { detail: "不能用" } });

  await assert.rejects(C.api("/api/teacher/classes"));

  assert.deepEqual(visited, []);
});

test("router 連不上時留在原頁", async () => {
  const { C, visited } = browser({ status: 503, body: { detail: "驗證服務暫時無法使用，請稍後再試" } });

  await assert.rejects(C.api("/api/student"), { message: "驗證服務暫時無法使用，請稍後再試" });

  assert.deepEqual(visited, []);
});
