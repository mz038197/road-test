// 步驟導覽（委託頁、交件頁）：捲到哪一步，導覽的那一項就標成目前位置（aria-current="step"）。
// 判斷方式：
//   - 畫面上方 1/3 那條線落在哪一張方塊裡，就是那一步。
//   - 捲到頁尾時標最後一項：最後幾張方塊很短，1/3 那條線碰不到。
//   - 點導覽或用 #網址打開時，直接標那一步：很短的方塊跳到頂端後，1/3 那條線會落在下一張。
// 沒有 IntersectionObserver 的瀏覽器就只是不標。
(() => {
  const links = [...document.querySelectorAll('.step-nav a[href^="#"]')];
  const targets = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  if (!targets.length || !("IntersectionObserver" in window)) return;

  const mark = id => links.forEach(a => {
    if (a.hash === `#${id}`) a.setAttribute("aria-current", "step");
    else a.removeAttribute("aria-current");
  });
  // 跳到某一步之後，跳轉造成的捲動不要把標記蓋掉（跳轉是瞬間的，600 毫秒足夠）
  let jumpedAt = 0;
  const jumpTo = id => { jumpedAt = Date.now(); mark(id); };
  const settled = () => Date.now() - jumpedAt > 600;
  const atBottom = () => innerHeight + scrollY >= document.documentElement.scrollHeight - 2;

  links.forEach(a => a.addEventListener("click", () => jumpTo(a.hash.slice(1))));
  addEventListener("hashchange", () => jumpTo(location.hash.slice(1)));
  if (targets.some(t => `#${t.id}` === location.hash)) jumpTo(location.hash.slice(1));

  const observer = new IntersectionObserver(entries => {
    const hit = entries.find(e => e.isIntersecting);
    if (hit && settled() && !atBottom()) mark(hit.target.id);
  }, { rootMargin: "-33% 0px -66% 0px" });  // 只留畫面上方 1/3 那一條線
  targets.forEach(t => observer.observe(t));
  addEventListener("scroll", () => {
    if (settled() && atBottom()) mark(targets.at(-1).id);
  }, { passive: true });
})();
