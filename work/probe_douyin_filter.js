const { chromium } = require('C:/Users/zeng/Desktop/video/project/frontend/node_modules/playwright-core');

(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:19222', { timeout: 5000 });
  const page = browser.contexts()
    .flatMap((c) => c.pages())
    .find((p) => p.url().includes('douyin.com'));
  if (!page) {
    console.log(JSON.stringify({ error: 'no_douyin_page' }));
    return process.exit(0);
  }
  const data = await page.evaluate(() => {
    const visible = (e) => {
      const s = getComputedStyle(e);
      const r = e.getBoundingClientRect();
      return (
        s.display !== 'none' &&
        s.visibility !== 'hidden' &&
        s.opacity !== '0' &&
        r.width > 0 &&
        r.height > 0
      );
    };
    const labels = ['视频', '全部', '综合', '用户', '直播', '图文', '经验'];
    const items = [];
    for (const e of document.querySelectorAll('a, button, [role="tab"], [role="menuitem"], span, div, li')) {
      const t = (e.innerText || e.textContent || '').trim();
      if (!labels.includes(t) || t.length > 8 || !visible(e)) continue;
      const p =
        e.closest('[role="tablist"],nav,.search-tabs,.tab-list,.filter-bar') ||
        e.parentElement;
      items.push({
        tag: e.tagName.toLowerCase(),
        text: t,
        cls: typeof e.className === 'string' ? e.className.slice(0, 200) : '',
        role: e.getAttribute('role'),
        dataKey: e.getAttribute('data-key'),
        dataType: e.getAttribute('data-type'),
        ariaSelected: e.getAttribute('aria-selected'),
        href: e.getAttribute('href'),
        parentTag: p?.tagName?.toLowerCase(),
        parentCls: typeof p?.className === 'string' ? p.className.slice(0, 200) : '',
        parentRole: p?.getAttribute?.('role'),
        outer: e.outerHTML.slice(0, 600),
      });
    }
    return {
      url: location.href,
      oldSelectorCount: document.querySelectorAll('span[data-key="video"]').length,
      total: items.length,
      items: items.slice(0, 30),
    };
  });
  console.log(JSON.stringify(data, null, 2));
  process.exit(0);
})().catch((e) => {
  console.error('ERR', e.message);
  process.exit(1);
});
