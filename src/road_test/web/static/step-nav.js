// 步驟導覽（委託頁、交件頁）：捲到哪一步，導覽的那一項就標成目前位置（aria-current="step"）。
// 判斷方式（每次捲動、改變視窗大小、剛打開頁面都重新算一次，不靠「哪張方塊剛進出畫面」，才不會停在舊的標記）：
//   - 捲到頁尾：標最後一項。最後幾張方塊很短，碰不到下面那條線。
//   - 其他時候：最後一張頂端已經過了畫面上方 1/3 那條線的方塊，就是那一步。
//   - 點導覽、用 #網址打開、網址的 # 換成某一步：直接標那一步，而且一直標著，直到使用者自己捲離跳過去的位置。
//     很短的方塊跳過去後，1/3 那條線會落在下一張；靠近頁尾的方塊跳過去會直接到頁尾。
(() => {
  const links = [...document.querySelectorAll('.step-nav a[href^="#"]')];
  const targets = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  if (!targets.length) return;

  const JUMP_SETTLE_MS = 600;  // 跳轉造成的捲動在這段時間內結束（平滑捲動也夠）
  const BOTTOM_SLACK_PX = 2;   // 捲到離頁尾這麼近就算到底（縮放時捲動位置會有小數誤差）

  const mark = id => links.forEach(a => {
    if (a.hash === `#${id}`) a.setAttribute("aria-current", "step");
    else a.removeAttribute("aria-current");
  });
  const isStep = id => targets.some(t => t.id === id);
  const atBottom = () => innerHeight + scrollY >= document.documentElement.scrollHeight - BOTTOM_SLACK_PX;
  const stepOnScreen = () => {
    if (atBottom()) return targets.at(-1).id;
    const line = innerHeight / 3;
    return targets.reduce((found, t) => (t.getBoundingClientRect().top <= line ? t : found), targets[0]).id;
  };

  // 跳到某一步之後：jump 記著跳到哪、跳完停在哪個捲動位置
  let jump = null;
  const jumpTo = id => {
    if (!isStep(id)) return;
    jump = { id, at: Date.now(), y: scrollY };
    mark(id);
  };
  const update = () => {
    if (jump) {
      if (Date.now() - jump.at < JUMP_SETTLE_MS) { jump.y = scrollY; return; }  // 還在跳，記下跳到的位置
      if (Math.abs(scrollY - jump.y) <= BOTTOM_SLACK_PX) return;               // 還停在跳過去的位置
      jump = null;                                                             // 使用者自己捲走了
    }
    mark(stepOnScreen());
  };

  // 點導覽要自己處理：點的是網址上已經是的那一步時，不會有 hashchange
  links.forEach(a => a.addEventListener("click", () => jumpTo(a.hash.slice(1))));
  addEventListener("hashchange", () => jumpTo(location.hash.slice(1)));
  let queued = false;
  const onScroll = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; update(); });
  };
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", onScroll);

  if (isStep(location.hash.slice(1))) jumpTo(location.hash.slice(1));
  else update();
})();
