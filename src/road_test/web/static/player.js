// 路考播放器：<video> 上疊一層 canvas，每一幀畫偵測框，計分幀畫判定；旁邊是分數和考官紀錄。
// 只管畫面，不管資料從哪裡來：
//   const player = createPlayer(容器, { videoUrl, detections, result });
//   player.setResult(新的計分結果)    // #8 換 CONF 重算後呼叫，框也會照新的 CONF 篩
//   player.setDetections(新的偵測框)  // 補幀時更新
//   player.seekFrame(幀號)            // 跳到那一幀並暫停
// 時間、幀號、該畫什麼的計算都在 playback.js（有測試），這裡只負責畫。
const MARK_STYLE = {
  green: { color: "#22c55e", label: () => "看見" },
  red: { color: "#ef4444", label: m => `還沒看見 ${m.waiting} 秒` },
  orange: { color: "#f59e0b", label: () => "誤判" },
};
const FONT = 'system-ui, "Microsoft JhengHei", "PingFang TC", "Noto Sans TC", sans-serif';
const SPEEDS = [0.5, 1, 2];

function createPlayer(root, { videoUrl, detections, result }) {
  root.classList.add("player");
  root.innerHTML = `
    <div class="stage">
      <video preload="auto" playsinline muted></video>
      <canvas></canvas>
    </div>
    <aside class="panel">
      <div class="live-score"><span class="score-now"></span><span class="unit">分</span></div>
      <p class="muted score-sub"></p>
      <h2>考官紀錄</h2>
      <p class="muted log-none">還沒有扣分。</p>
      <ol class="log live-log"></ol>
    </aside>
    <div class="controls">
      <button type="button" class="play" aria-label="播放">▶</button>
      <span class="clock">0:00 / 0:00</span>
      <input type="range" class="seek" min="0" value="0" step="1" aria-label="時間軸">
      <span class="speeds" role="group" aria-label="播放速度">
        ${SPEEDS.map(s => `<button type="button" class="speed" data-speed="${s}">${s}×</button>`).join("")}
      </span>
      <button type="button" class="fullscreen secondary">全螢幕</button>
    </div>`;
  const $ = sel => root.querySelector(sel);
  const video = $("video"), canvas = $("canvas"), seek = $(".seek"), playBtn = $(".play");
  const ctx = canvas.getContext("2d");
  let frame = 0, dragging = false, shownLog = -1;

  const fps = () => detections.fps;
  const lastFrame = () => detections.frames.length - 1;
  seek.max = lastFrame();
  // 路考 MP4 存的是 960×720，但標成寬螢幕（SAR 4:3，瀏覽器會拉成 1280×720）。
  // 模型和答案看的都是存下來的 960×720，所以畫面也照這個比例顯示（CSS object-fit: fill）。
  $(".stage").style.aspectRatio = `${detections.width} / ${detections.height}`;
  video.src = videoUrl;

  // ---- 畫面 ----
  function render(n) {
    frame = Math.min(Math.max(n, 0), lastFrame());
    const state = Playback.stateAt(result, frame);
    draw(state);
    if (!dragging) seek.value = frame;
    $(".clock").textContent = `${clock(frame / fps())} / ${clock(lastFrame() / fps())}`;
    $(".score-now").textContent = state.score;
    $(".score-sub").textContent = `已扣 ${+state.deducted.toFixed(1)}／扣滿 ${result.full}（CONF ${result.conf}）`;
    if (state.log.length !== shownLog) showLog(state.log);
  }

  function draw(state) {
    // canvas 跟 <video> 一樣大。影片一律拉成答案的寬高比（960×720，見 .stage），
    // 所以 x、y 各自縮放就好，沒有黑邊要扣。
    const dpr = window.devicePixelRatio || 1;
    const w = video.clientWidth, h = video.clientHeight;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const sx = w / detections.width, sy = h / detections.height;
    ctx.setTransform(dpr * sx, 0, 0, dpr * sy, 0, 0);  // 之後都用 960×720 的座標畫
    const px = 1 / Math.min(sx, sy);  // 螢幕上的 1 px（約略）

    const boxes = Playback.boxesAt(detections, frame, result.conf);
    if (boxes === null) {
      label("這一幀還在偵測，等一下重新整理就有框", 12, 12, "rgba(0,0,0,.65)", 14 * px);
    } else {
      for (const b of boxes) {
        rect(b.box, "rgba(255,255,255,.9)", 1.5 * px);
        label(`${CLASS_NAMES[b.cls] || b.cls} ${b.conf.toFixed(2)}`, b.box[0], b.box[1], "rgba(0,0,0,.55)", 11 * px, true);
      }
    }
    for (const m of state.marks) {
      const style = MARK_STYLE[m.color];
      rect(m.box, style.color, 3.5 * px);
      label(`${CLASS_NAMES[m.cls] || m.cls} ${style.label(m)}`, m.box[0], m.box[3], style.color, 13 * px);
    }
  }

  function rect([x1, y1, x2, y2], color, width) {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  }

  // 文字標籤：above = 貼在框的上緣外面，否則貼在下緣外面
  function label(text, x, y, bg, size, above = false) {
    ctx.font = `600 ${size}px ${FONT}`;
    const pad = size * 0.3, tw = ctx.measureText(text).width, th = size * 1.35;
    const top = above ? y - th : y;
    ctx.fillStyle = bg;
    ctx.fillRect(x, top, tw + pad * 2, th);
    ctx.fillStyle = "#fff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x + pad, top + th / 2);
  }

  function showLog(log) {
    shownLog = log.length;
    $(".log-none").hidden = log.length > 0;
    // 新的在最上面，播放中不用捲動就看得到剛扣的分
    $(".live-log").innerHTML = log.map((e, i) => ({ e, i })).reverse().map(({ e, i }) => `
      <li><button type="button" class="jump" data-i="${i}" title="跳到這一刻並暫停">
        <span class="t">${clock(e.video_time)}</span>
        <span class="${e.kind === "誤判" ? "false-alarm" : "miss"}">${escapeHtml(describe(e))}</span>
        <span class="p">${penalty(e.penalty)}</span></button></li>`).join("");
  }

  // ---- 跟影片同步：requestVideoFrameCallback 給的是「現在顯示的這一幀」的時間戳 ----
  if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) {
    const onFrame = (_now, meta) => {
      render(Playback.frameAt(meta.mediaTime, fps()));
      video.requestVideoFrameCallback(onFrame);
    };
    video.requestVideoFrameCallback(onFrame);
  } else {
    // 沒有 rVFC 的瀏覽器：退回 currentTime（可能差一幀）
    const tick = () => { render(Math.floor(video.currentTime * fps() + 1e-3)); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  }
  video.addEventListener("loadeddata", () => render(frame));
  window.addEventListener("resize", () => render(frame));
  document.addEventListener("fullscreenchange", () => setTimeout(() => render(frame), 50));

  // ---- 控制 ----
  function seekFrame(n, pause = true) {
    if (pause) video.pause();
    n = Math.min(Math.max(n, 0), lastFrame());
    video.currentTime = Playback.seekTime(n, fps());
    render(n);  // 先把判定和紀錄換到那一刻；影格出來後 rVFC 會再畫一次
  }

  playBtn.addEventListener("click", () => (video.paused || video.ended ? video.play() : video.pause()));
  video.addEventListener("play", () => { playBtn.textContent = "❚❚"; playBtn.setAttribute("aria-label", "暫停"); });
  video.addEventListener("pause", () => { playBtn.textContent = "▶"; playBtn.setAttribute("aria-label", "播放"); });
  video.addEventListener("click", () => playBtn.click());
  canvas.addEventListener("click", () => playBtn.click());

  seek.addEventListener("input", () => { dragging = true; seekFrame(+seek.value, false); });
  seek.addEventListener("change", () => { dragging = false; });

  function setSpeed(s) {
    video.playbackRate = s;
    root.querySelectorAll(".speed").forEach(b => b.classList.toggle("on", +b.dataset.speed === s));
  }
  root.querySelectorAll(".speed").forEach(b => b.addEventListener("click", () => setSpeed(+b.dataset.speed)));
  setSpeed(1);

  $(".live-log").addEventListener("click", ev => {
    const btn = ev.target.closest(".jump");
    if (btn) seekFrame(Playback.stateAt(result, frame).log[+btn.dataset.i].video_frame);
  });

  $(".fullscreen").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else root.requestFullscreen?.().catch(() => { /* 瀏覽器不准（例如不是使用者點的），就維持原樣 */ });
  });

  // 空白鍵播放／暫停，← → 一次一幀（暫停時檢查框對不對得上很好用）
  root.tabIndex = 0;
  root.addEventListener("keydown", ev => {
    if (ev.target.closest("input, button") && ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    if (ev.key === " ") { ev.preventDefault(); playBtn.click(); }
    else if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      ev.preventDefault();
      seekFrame(frame + (ev.key === "ArrowRight" ? 1 : -1));
    }
  });

  render(0);
  return {
    video,
    get frame() { return frame; },
    setResult(r) { result = r; shownLog = -1; render(frame); },
    setDetections(d) { detections = d; seek.max = lastFrame(); render(frame); },
    seekFrame,
  };
}
