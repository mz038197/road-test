// 結果頁播放邏輯（static/playback.js）的測試：node --test tests/js
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../../src/road_test/web/static/playback.js");

const FPS = 29.97;
const NTSC = 30000 / 1001;  // 影片實際的幀率，MP4 第 n 幀的時間戳 = n × 1001 / 30000

test("影格時間換成幀號不會差一幀（開頭、中段、結尾）", () => {
  for (const n of [0, 1, 2, 929, 930, 931, 1999, 2000, 3929, 3930, 5100]) {
    assert.equal(P.frameAt(n / NTSC, FPS), n, `第 ${n} 幀的時間戳要換回 ${n}`);
  }
});

test("影格時間有一點誤差也還是同一幀", () => {
  assert.equal(P.frameAt(930 / NTSC + 0.004, FPS), 930, "稍微晚一點（不到半幀）還是 930");
  assert.equal(P.frameAt(930 / NTSC - 0.004, FPS), 930, "稍微早一點（不到半幀）還是 930");
  assert.equal(P.frameAt(-0.001, FPS), 0, "負的時間當第 0 幀");
});

test("跳到某一幀時，設定的時間會顯示那一幀", () => {
  // 瀏覽器顯示「時間戳 ≤ currentTime」的最後一幀，所以要跳進那一幀的中間，不是剛好卡在邊界
  for (const n of [0, 930, 2000, 3930]) {
    const t = P.seekTime(n, FPS);
    assert.ok(t > n / NTSC && t < (n + 1) / NTSC, `跳到第 ${n} 幀的時間 ${t} 要落在第 ${n} 幀裡`);
    assert.equal(P.frameAt(t, FPS), n, "跳過去之後換回來還是同一幀");
  }
});

// 手寫的結果：計分幀在影片第 930、960、990 幀；扣滿 10 分
const RED = { color: "red", cls: "pedestrian", box: [1, 2, 3, 4], waiting: 0 };
const GREEN = { color: "green", cls: "pedestrian", box: [1, 2, 3, 4] };
const GHOST = { color: "orange", cls: "car", box: [5, 6, 7, 8] };
const RESULT = {
  score: 65, deducted: 3.5, full: 10, conf: 0.35,
  frames: [
    { frame: 0, video_frame: 930, marks: [RED] },
    { frame: 1, video_frame: 960, marks: [GREEN, GHOST] },
    { frame: 2, video_frame: 990, marks: [] },
  ],
  log: [
    { frame: 1, video_frame: 960, cls: "pedestrian", kind: "漏看", reaction: 1, penalty: 2.5 },
    { frame: 1, video_frame: 960, cls: "car", kind: "誤判", reaction: null, penalty: 1 },
  ],
};

test("第一個計分幀之前：沒有判定、沒有紀錄、100 分", () => {
  const s = P.stateAt(RESULT, 929);
  assert.deepEqual(s.marks, []);
  assert.deepEqual(s.log, []);
  assert.equal(s.score, 100);
  assert.equal(s.deducted, 0);
});

test("判定停留到下一個計分幀", () => {
  assert.deepEqual(P.stateAt(RESULT, 930).marks, [RED], "計分幀那一幀就畫判定");
  assert.deepEqual(P.stateAt(RESULT, 959).marks, [RED], "下一個計分幀之前都還是同一份判定");
  assert.deepEqual(P.stateAt(RESULT, 960).marks, [GREEN, GHOST], "到了下一個計分幀就換掉");
  assert.deepEqual(P.stateAt(RESULT, 3000).marks, [], "最後一個計分幀的判定（這裡是空的）留到影片結束");
});

test("考官紀錄在結算那一幀出現，分數跟著往下掉", () => {
  assert.equal(P.stateAt(RESULT, 959).log.length, 0, "結算之前還沒出現");
  const s = P.stateAt(RESULT, 960);
  assert.deepEqual(s.log, RESULT.log, "結算那一幀兩條都出現");
  assert.equal(s.deducted, 3.5);
  assert.equal(s.score, 65, "round(100 × (1 − 3.5 / 10)) = 65");
});

test("往回拖：紀錄和分數回到那一刻", () => {
  P.stateAt(RESULT, 3000);
  const back = P.stateAt(RESULT, 935);
  assert.deepEqual(back.log, []);
  assert.equal(back.score, 100);
});

test("分數不會低於 0；整場沒有必看的東西時，有扣分就是 0", () => {
  const heavy = { ...RESULT, full: 2 };
  assert.equal(P.stateAt(heavy, 960).score, 0, "扣超過扣滿也是 0 分");
  const empty = { ...RESULT, full: 0 };
  assert.equal(P.stateAt(empty, 959).score, 100, "還沒扣分是 100");
  assert.equal(P.stateAt(empty, 960).score, 0, "full = 0 時有扣分是 0（跟計分模組一樣）");
});

test("分數的四捨五入跟計分模組（Python round）一樣", () => {
  assert.equal(P.scoreOf(3, 8), 62, "62.5 → 62");
  assert.equal(P.scoreOf(1, 8), 88, "87.5 → 88");
  assert.equal(P.scoreOf(1, 3), 67);
});

const DETECTIONS = {
  classes: ["car", "pedestrian", "bicyclist", "sign"],
  frames: [[[10, 20, 30, 40, 0.9, 1], [50, 60, 70, 80, 0.2, 0]], [], null],
};

test("每一幀的偵測框只畫過 CONF 的", () => {
  assert.deepEqual(P.boxesAt(DETECTIONS, 0, 0.35), [{ cls: "pedestrian", box: [10, 20, 30, 40], conf: 0.9 }]);
  assert.equal(P.boxesAt(DETECTIONS, 0, 0.1).length, 2, "CONF 調低，框就變多（#8 的滑桿）");
  assert.equal(P.boxesAt(DETECTIONS, 0, 0.2).length, 2, "剛好等於 CONF 也算");
  assert.deepEqual(P.boxesAt(DETECTIONS, 1, 0.35), [], "沒框到");
  assert.equal(P.boxesAt(DETECTIONS, 2, 0.35), null, "還沒補完的幀是 null");
  assert.equal(P.boxesAt(DETECTIONS, 99, 0.35), null, "超出影片的幀也是 null");
});
