// 步驟導覽（委託頁、交件頁）：捲到哪一步，左邊導覽的那一項就標成目前位置（aria-current="step"）。
// 判斷方式：畫面上方 1/3 那條線落在哪一張方塊裡，就是那一步。沒有 IntersectionObserver 的瀏覽器就只是不標。
(() => {
  const links = [...document.querySelectorAll('.step-nav a[href^="#"]')];
  const targets = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
  if (!targets.length || !("IntersectionObserver" in window)) return;

  const mark = id => links.forEach(a => {
    if (a.hash === `#${id}`) a.setAttribute("aria-current", "step");
    else a.removeAttribute("aria-current");
  });
  // 點導覽跳過去時直接標那一步：很短的方塊跳到頂端後，1/3 那條線會落在下一張，不能讓捲動的判斷蓋掉
  let clickedAt = 0;
  links.forEach(a => a.addEventListener("click", () => { clickedAt = Date.now(); mark(a.hash.slice(1)); }));
  const observer = new IntersectionObserver(entries => {
    const hit = entries.find(e => e.isIntersecting);
    if (hit && Date.now() - clickedAt > 600) mark(hit.target.id);
  }, { rootMargin: "-33% 0px -66% 0px" });  // 只留畫面上方 1/3 那一條線
  targets.forEach(t => observer.observe(t));
})();
