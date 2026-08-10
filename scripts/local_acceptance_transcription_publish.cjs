/* Real-Chrome acceptance for sandbox transcription and safe publish intake. */
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("../project/frontend/node_modules/playwright-core");

const sourceFile = path.join(
  process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE, "AppData", "Local"),
  "Temp",
  "VideoInsight-local-acceptance-20260810-goal",
  "local-acceptance-source.mp4",
);
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

async function main() {
  const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));
  if (!executablePath || !fs.existsSync(sourceFile)) throw new Error("验收浏览器或视频不存在。");
  fs.mkdirSync(evidenceRoot, { recursive: true });
  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  const failures = [];
  await context.route("http://127.0.0.1:1001/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/publish/accounts") {
      const timestamp = new Date().toISOString();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{
          account_id: "local-acceptance-account",
          platform: "douyin",
          name: "本地验收假账号（不发布）",
          status: "ready",
          message: "仅用于隔离页面验收，不连接真实平台",
          auto_publish_authorized: false,
          last_verified_at: timestamp,
          created_at: timestamp,
          updated_at: timestamp,
        }]),
      });
      return;
    }
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
    ) consoleErrors.push(text);
  });

  const balance = () => page.evaluate(async () => {
    const token = localStorage.getItem("vi_customer_token");
    const response = await fetch("/api/v1/credits", {
      headers: token ? { "X-Customer-Token": token } : {},
    });
    return (await response.json()).balance;
  });

  try {
    await page.goto("http://127.0.0.1:1001/login", { waitUntil: "networkidle" });
    await page.getByLabel("激活码").fill("LOCALQA8");
    await page.getByRole("button", { name: "进入工作台" }).click();
    await page.waitForURL(/\/(pipeline|production)/, { timeout: 15_000 });
    const balanceBefore = await balance();

    await page.goto("http://127.0.0.1:1001/transcription?entry=upload", {
      waitUntil: "networkidle",
    });
    await page.locator('input[type="file"]').first().setInputFiles(sourceFile);
    await page.getByText("本地演示不调用真实云服务，也不扣积分", { exact: false }).waitFor({
      timeout: 10_000,
    });
    await page.getByRole("checkbox", { name: /我确认拥有该文件的处理权/ }).check();
    const transcriptionResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/transcriptions/upload"
        && response.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: "确认权利并开始免费演示" }).click();
    const transcription = await transcriptionResponse;
    if (!transcription.ok()) {
      throw new Error(`转写上传失败 ${transcription.status()}：${await transcription.text()}`);
    }
    await page.getByText("识别完成", { exact: false }).first().waitFor({ timeout: 15_000 });
    await page.screenshot({
      path: path.join(evidenceRoot, "local-transcription-sandbox.png"),
      fullPage: true,
    });

    await page.goto("http://127.0.0.1:1001/publish", { waitUntil: "networkidle" });
    const continueButton = page.getByRole("button", { name: "去选择成片" });
    if (await continueButton.isVisible().catch(() => false)) await continueButton.click();
    await page.getByRole("button", { name: "上传成片" }).waitFor({ timeout: 10_000 });
    const publishResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/publish/assets/upload"
        && response.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.locator('input[type="file"]').first().setInputFiles(sourceFile);
    const uploaded = await publishResponse;
    if (!uploaded.ok()) {
      throw new Error(`发布素材上传失败 ${uploaded.status()}：${await uploaded.text()}`);
    }
    await page.getByText("local-acceptance-source", { exact: false }).first().waitFor({
      timeout: 15_000,
    });
    await page.screenshot({
      path: path.join(evidenceRoot, "local-publish-upload-only.png"),
      fullPage: true,
    });

    const balanceAfter = await balance();
    if (String(balanceBefore) !== String(balanceAfter)) {
      throw new Error(`免费验收路径不应扣积分：${balanceBefore} -> ${balanceAfter}`);
    }
    if (failures.length) throw new Error(`API 失败：${failures.join(", ")}`);
    if (consoleErrors.length) throw new Error(`控制台错误：${consoleErrors.join(" | ")}`);
    process.stdout.write(JSON.stringify({
      ok: true,
      transcription: "sandbox upload completed",
      publish: "asset uploaded; no publish action executed",
      balanceBefore,
      balanceAfter,
    }, null, 2));
  } catch (error) {
    const failureScreenshot = path.join(evidenceRoot, "local-transcription-publish-failed.png");
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
