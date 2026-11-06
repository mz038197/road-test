// 結果頁播放的計算（純函式，不碰畫面）：時間 ↔ 幀號、某一幀該畫什麼、考官紀錄和分數到哪裡。
// 瀏覽器裡是一般的 <script>（全域 Playback）；node --test 用 require 讀（tests/js/）。
(function (root) {
  // 影格時間（requestVideoFrameCallback 的 mediaTime，= 那一幀的時間戳 n / fps）→ 幀號。
  // 時間戳剛好落在幀的開頭，浮點數乘回來可能是 929.9999，所以四捨五入，不能無條件捨去。
  function frameAt(mediaTime, fps) {
    return Math.max(0, Math.round(mediaTime * fps));
  }

  // 要跳到第 n 幀時設定的 currentTime：跳進那一幀的前四分之一，
  // 不卡在幀的邊界上（卡在邊界，瀏覽器可能顯示前一幀），四捨五入也還是 n。
  function seekTime(frame, fps) {
    return (frame + 0.25) / fps;
  }

  // Python 的 round()（計分模組用的）是四捨六入五成雙，這裡照做，播放中的分數才會跟成績對得上
  function roundHalfEven(x) {
    const r = Math.round(x);
    return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
  }

  // 已扣分 → 成績，規則跟計分模組一樣：max(0, round(100 × (1 − 已扣 ÷ 扣滿)))
  function scoreOf(deducted, full) {
    if (full <= 0) return deducted > 0 ? 0 : 100;
    return Math.max(0, roundHalfEven(100 * (1 - deducted / full)));
  }

  // 影片播到第 frame 幀時，畫面上該有什麼。result 是計分結果（score_result 的輸出，
  // #8 換 CONF 重算後丟新的進來就好）。往回拖也是重算一次，不記前一刻的狀態。
  //   marks：最近一個計分幀（≤ frame）的判定，停留到下一個計分幀；第一個計分幀之前是 []
  //   log：已經結算的考官紀錄（video_frame ≤ frame），照原本的順序
  //   deducted、score：到這一刻為止的已扣分和分數
  function stateAt(result, frame) {
    let marks = [];
    for (const f of result.frames) {
      if (f.video_frame > frame) break;
      marks = f.marks;
    }
    const log = result.log.filter(e => e.video_frame <= frame);
    const deducted = log.reduce((sum, e) => sum + e.penalty, 0);
    return { marks, log, deducted, score: scoreOf(deducted, result.full) };
  }

  // 第 frame 幀的偵測框（/detections 的格式），只留 conf ≥ CONF 的，換成 {cls, box, conf}。
  // 還沒補完（或超出影片）的幀回傳 null，畫面上可以標「這一幀還在偵測」。
  function boxesAt(detections, frame, conf) {
    const raw = detections.frames[frame];
    if (!raw) return raw === undefined ? null : raw;
    return raw.filter(b => b[4] >= conf)
      .map(b => ({ cls: detections.classes[b[5]], box: b.slice(0, 4), conf: b[4] }));
  }

  const Playback = { frameAt, seekTime, stateAt, scoreOf, boxesAt };
  if (typeof module !== "undefined" && module.exports) module.exports = Playback;
  else root.Playback = Playback;
})(this);
