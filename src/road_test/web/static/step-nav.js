// 步驟導覽（委託頁、交件頁）：捲到哪一步，導覽的那一項就標成目前位置（aria-current="step"）。
// 判斷方式（每次捲動、改變視窗大小、剛打開頁面都重新算一次，不靠「哪張方塊剛進出畫面」，才不會停在舊的標記）：
//   - 捲到頁尾：標最後一項。最後幾張方塊很短，碰不到下面那條線。
//   - 其他時候：最後一張頂端已經過了 CURRENT_LINE 那條線的方塊，就是那一步。
//   - 點導覽、用 #網址打開、網址的 # 換成某一步：直接標那一步。跳完確認真的跳到了（那一步在畫面頂端，或到了頁尾），
//     就一直標著，直到使用者自己捲離跳過去的位置。很短的方塊跳過去後，那條線會落在下一張；靠近頁尾的方塊跳過去會直接到頁尾。
//     沒跳到（例如重新整理時瀏覽器捲回上次的位置）就照畫面上的位置標。
(() => {
  const links = [...document.querySelectorAll('.step-nav a[href^="#"]')];
  const targets = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  if (!targets.length) return;

  const CURRENT_LINE = 1 / 3;         // 畫面上方 1/3 那條線落在哪一張方塊，就是目前那一步
  const JUMP_SETTLE_MS = 600;         // 跳轉造成的捲動在這段時間內結束
  const AT_BOTTOM_SLACK_PX = 2;       // 離頁尾這麼近就算到底（縮放時捲動位置會有小數誤差）
  const JUMP_POSITION_SLACK_PX = 2;   // 離跳過去的位置這麼近，就算還停在那裡
  const LANDED_SLACK_PX = 4;          // 那一步的頂端離它該停的位置這麼近，就算跳到了

  const mark = id => links.forEach(a => {
    if (a.hash === `#${id}`) a.setAttribute("aria-current", "step");
    else a.removeAttribute("aria-current");
  });
  const isStep = id => targets.some(t => t.id === id);
  const atBottom = () => innerHeight + scrollY >= document.documentElement.scrollHeight - AT_BOTTOM_SLACK_PX;
  const stepOnScreen = () => {
    if (atBottom()) return targets.at(-1).id;
    const line = innerHeight * CURRENT_LINE;
    return targets.reduce((found, t) => (t.getBoundingClientRect().top <= line ? t : found), targets[0]).id;
  };
  // 跳到某一步之後，那一步的頂端會停在它的 scroll-margin-top；到了頁尾就停不到那裡，也算跳到了
  const landedOn = id => {
    const step = document.getElementById(id);
    const stopAt = parseFloat(getComputedStyle(step).scrollMarginTop) || 0;
    return atBottom() || Math.abs(step.getBoundingClientRect().top - stopAt) <= LANDED_SLACK_PX;
  };

  // jump：跳到哪一步；y 是確認跳到之後停在的捲動位置（還在跳的時候是 null）
  let jump = null;
  const jumpTo = id => {
    if (!isStep(id)) return;
    const thisJump = { id, y: null };
    jump = thisJump;
    mark(id);
    setTimeout(() => {
      if (jump !== thisJump) return;          // 這段時間又跳到別的地方了
      if (landedOn(id)) thisJump.y = scrollY;
      else { jump = null; mark(stepOnScreen()); }
    }, JUMP_SETTLE_MS);
  };
  const update = () => {
    if (jump) {
      if (jump.y === null) return;                                       // 還在跳
      if (Math.abs(scrollY - jump.y) <= JUMP_POSITION_SLACK_PX) return;  // 還停在跳過去的位置
      jump = null;                                                       // 使用者自己捲走了
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
