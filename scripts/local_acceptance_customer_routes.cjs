/* Real-Chrome customer route and harmless-control acceptance against port 2101. */
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("../project/frontend/node_modules/playwright-core");

const evidenceRoot = path.join(
  process.env.USERPROFILE,
  ".codex", "visualizations", "2026", "08", "08",
  "019fdf9a-f2e1-7a32-872c-5d0d719f9606",
);
const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];
const routes = [
  "/pipeline",
  "/tasks",
  "/crawler",
  "/candidates",
  "/transcription",
  "/ai-copy",
  "/avatar",
  "/video-editor",
  "/publish",
  "/production",
  "/analytics",
  "/feedback",
  "/studio",
  "/help",
];

async function main() {
  const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));
  if (!executablePath) throw new Error("未找到 Chrome 或 Edge。 ");
  fs.mkdirSync(evidenceRoot, { recursive: true });
  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  const failures = [];
  const consoleErrors = [];
  await context.route("http://127.0.0.1:1001/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const headers = { ...request.headers() };
    delete headers.host;
    await route.continue({
      url: `http://127.0.0.1:2101${url.pathname}${url.search}`,
      headers,
    });
  });
  const page = await context.newPage();
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.port === "2101" && response.status() >= 400) {
      failures.push(`${response.request().method()} ${url.pathname} -> ${response.status()}`);
    }
  });
  page.on("console", (message) => {
    const value = message.text();
    if (
      message.type() === "error"
      && !value.includes("[antd: message] Static function can not consume context")
    ) consoleErrors.push(value);
  });

  try {
    await page.goto("http://127.0.0.1:1001/login", { waitUntil: "networkidle" });
    await page.getByLabel("激活码").fill("LOCALQA8");
    await page.getByRole("button", { name: "进入工作台" }).click();
    await page.waitForURL(/\/(pipeline|production)/, { timeout: 15_000 });

    const results = [];
    for (const route of routes) {
      const failureStart = failures.length;
      const consoleStart = consoleErrors.length;
      await page.goto(`http://127.0.0.1:1001${route}`, { waitUntil: "networkidle" });
      await page.locator(".vi-page-title").waitFor({ state: "attached", timeout: 10_000 });
      const bodyText = await page.locator("body").innerText();
      if (/页面(?:加载)?失败|Something went wrong|Cannot read properties/.test(bodyText)) {
        throw new Error(`${route} 显示错误边界。`);
      }
      results.push({
        route,
        title: (await page.locator(".vi-page-title").innerText()).trim(),
        apiFailures: failures.slice(failureStart),
        consoleErrors: consoleErrors.slice(consoleStart),
      });
    }

    await page.goto("http://127.0.0.1:1001/pipeline", { waitUntil: "networkidle" });
    const filterButton = page.getByRole("button", { name: "筛选" });
    await filterButton.click();
    await page.getByRole("combobox", { name: "发布时间筛选" }).waitFor();
    await page.getByRole("combobox", { name: "每平台素材数量" }).waitFor();
    await page.screenshot({
      path: path.join(evidenceRoot, "local-customer-pipeline-filter.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("http://127.0.0.1:1001/help", { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    await page.locator(".vi-mobile-menu-btn").click();
    await page.waitForTimeout(300);
    await page.locator(".vi-sidebar-wrapper.mobile-open").waitFor({ state: "attached" });
    await page.evaluate(() => window.scrollTo(0, 0));
    const mobileMetrics = await page.evaluate(() => {
      const sidebar = document.querySelector(".vi-sidebar");
      const wrapper = document.querySelector(".vi-sidebar-wrapper");
      const sidebarRect = sidebar?.getBoundingClientRect();
      const wrapperRect = wrapper?.getBoundingClientRect();
      return {
        innerWidth: window.innerWidth,
        scrollX: window.scrollX,
        documentWidth: document.documentElement.scrollWidth,
        wrapperClassName: wrapper?.className || null,
        sidebar: sidebarRect ? { left: sidebarRect.left, width: sidebarRect.width } : null,
        wrapper: wrapperRect ? { left: wrapperRect.left, width: wrapperRect.width } : null,
        sidebarTransform: sidebar ? getComputedStyle(sidebar).transform : null,
        wrapperTransform: wrapper ? getComputedStyle(wrapper).transform : null,
      };
    });
    await page.screenshot({
      path: path.join(evidenceRoot, "local-customer-mobile-menu.png"),
      fullPage: false,
    });

    const routeFailures = results.flatMap((item) => item.apiFailures);
    const routeConsoleErrors = results.flatMap((item) => item.consoleErrors);
    if (routeFailures.length) throw new Error(`API 失败：${[...new Set(routeFailures)].join(", ")}`);
    if (routeConsoleErrors.length) throw new Error(`控制台错误：${[...new Set(routeConsoleErrors)].join(" | ")}`);
    process.stdout.write(JSON.stringify({
      ok: true,
      routes: results.map(({ route, title }) => ({ route, title })),
      pipelineFilters: ["发布时间筛选", "每平台素材数量"],
      mobileMenu: "opened",
      mobileMetrics,
    }, null, 2));
  } catch (error) {
    const failureScreenshot = path.join(evidenceRoot, "local-customer-routes-failed.png");
    await page.screenshot({ path: failureScreenshot, fullPage: true }).catch(() => undefined);
    error.message = `${error.message}\n失败截图：${failureScreenshot}`;
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
