/* Real-Chrome acceptance for the no-charge local video-editor path.
 * Frontend stays on :1001 while every /api request is redirected to the
 * disposable backend on :2101, so normal user data and paid providers remain untouched.
 */
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("../project/frontend/node_modules/playwright-core");

const projectRoot = path.resolve(__dirname, "..");
const sourceFile = path.join(
  process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE, "AppData", "Local"),
  "Temp",
  "VideoInsight-local-acceptance-20260810-goal",
  "local-acceptance-source.mp4",
);
const screenshotPath = path.join(
  process.env.USERPROFILE,
  ".codex",
  "visualizations",
  "2026",
  "08",
  "08",
  "019fdf9a-f2e1-7a32-872c-5d0d719f9606",
  "local-video-editor-sandbox.png",
);
const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];

async function main() {
  if (!fs.existsSync(sourceFile)) throw new Error(`验收视频不存在：${sourceFile}`);
  const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));
  if (!executablePath) throw new Error("未找到可用于验收的 Chrome 或 Edge。");
  fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });

  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  const failures = [];
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
  const consoleErrors = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.port === "2101" && response.status() >= 400) {
      failures.push(`${response.request().method()} ${url.pathname} -> ${response.status()}`);
    }
  });
  page.on("console", (message) => {
    const text = message.text();
    if (
      message.type() === "error"
      && !text.includes("[antd: message] Static function can not consume context")
    ) {
      consoleErrors.push(text);
    }
  });
  try {
    await page.goto("http://127.0.0.1:1001/login", { waitUntil: "networkidle" });
    await page.getByLabel("激活码").fill("LOCALQA8");
    await page.getByRole("button", { name: "进入工作台" }).click();
    await page.waitForURL(/\/(pipeline|production)/, { timeout: 15_000 });

    const balanceBefore = await page.evaluate(async () => {
      const token = localStorage.getItem("vi_customer_token");
      const response = await fetch("/api/v1/credits", {
        headers: token ? { "X-Customer-Token": token } : {},
      });
      return (await response.json()).balance;
    });

    await page.goto("http://127.0.0.1:1001/video-editor", { waitUntil: "networkidle" });
    const uploadResponsePromise = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/video-editor/uploads"
        && response.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.locator('input[type="file"]').first().setInputFiles(sourceFile);
    const uploadResponse = await uploadResponsePromise;
    if (!uploadResponse.ok()) {
      throw new Error(`上传接口返回 ${uploadResponse.status()}：${await uploadResponse.text()}`);
    }

    const primary = page.getByTestId("primary-action");
    await primary.getByText("免费预览剪辑方案", { exact: false }).waitFor({ timeout: 15_000 });
    await primary.click();
    const confirm = page.getByRole("dialog", { name: "免费体验剪辑方案" });
    await confirm.getByRole("button", { name: "开始免费体验" }).click();
    await primary.getByText("审核字幕、粗剪和配乐", { exact: false }).waitFor({
      timeout: 20_000,
    });
    await primary.click();
    const review = page.getByRole("dialog", { name: "字幕与方案体验" });
    await review.getByRole("button", { name: "保存体验方案" }).click();
    await page.getByText("体验已完成，云端出片待开通", { exact: false }).first().waitFor({
      timeout: 20_000,
    });

    const balanceAfter = await page.evaluate(async () => {
      const token = localStorage.getItem("vi_customer_token");
      const response = await fetch("/api/v1/credits", {
        headers: token ? { "X-Customer-Token": token } : {},
      });
      return (await response.json()).balance;
    });
    await page.screenshot({ path: screenshotPath, fullPage: true });

    if (String(balanceBefore) !== String(balanceAfter)) {
      throw new Error(`免费流程不应扣积分：${balanceBefore} -> ${balanceAfter}`);
    }
    if (failures.length) throw new Error(`API 失败：${failures.join(", ")}`);
    if (consoleErrors.length) throw new Error(`浏览器控制台错误：${consoleErrors.join(" | ")}`);
    process.stdout.write(JSON.stringify({
      ok: true,
      balanceBefore,
      balanceAfter,
      screenshotPath,
      sourceFile,
      projectRoot,
    }, null, 2));
  } catch (error) {
    const failureScreenshot = screenshotPath.replace(/\.png$/, "-failed.png");
    await page.screenshot({ path: failureScreenshot, fullPage: true }).catch(() => undefined);
    error.message = [
      error.message,
      failures.length ? `API 失败：${failures.join(", ")}` : "",
      consoleErrors.length ? `控制台错误：${consoleErrors.join(" | ")}` : "",
      `失败截图：${failureScreenshot}`,
    ].filter(Boolean).join("\n");
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
