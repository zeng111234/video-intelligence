/* Real-Chrome acceptance for disposable admin controls on port 2101. */
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("../project/frontend/node_modules/playwright-core");

const backend = "http://127.0.0.1:2101";
const frontend = process.env.VIDEOINSIGHT_ACCEPTANCE_FRONTEND_URL
  || "http://127.0.0.1:1001";
const repositoryRoot = path.resolve(__dirname, "..");
const evidenceRoot = path.resolve(
  process.env.VIDEOINSIGHT_ACCEPTANCE_EVIDENCE_DIR
    || path.join(repositoryRoot, "build", "acceptance-evidence", "admin"),
);
const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
];

async function jsonRequest(url, options = {}) {
  const response = await fetch(`${backend}${url}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": "local-acceptance-api-key",
      ...(options.headers || {}),
    },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(`${options.method || "GET"} ${url} -> ${response.status}: ${JSON.stringify(body)}`);
  return body;
}

async function seedRechargeRequest(reason) {
  const login = await jsonRequest("/api/v1/auth/customer-login", {
    method: "POST",
    body: JSON.stringify({ code: "LOCALQA8" }),
  });
  return jsonRequest("/api/v1/credits/recharge-request", {
    method: "POST",
    headers: {
      "X-Customer-Token": login.token,
      "Idempotency-Key": `admin-acceptance-recharge-${Date.now()}`,
    },
    body: JSON.stringify({ amount: 3, reason }),
  });
}

async function clickVisibleModalPrimary(page) {
  const buttons = page.locator(".ant-modal-wrap:visible .ant-modal-footer button:visible");
  const count = await buttons.count();
  if (count !== 2) {
    throw new Error(`可见弹窗按钮数量异常：${count}; ${JSON.stringify(await buttons.allTextContents())}`);
  }
  await buttons.nth(1).click();
}

async function confirmVisiblePopconfirm(page) {
  const popover = page.locator(".ant-popover:visible").last();
  await popover.waitFor({ state: "visible" });
  const buttons = popover.locator("button:visible");
  const count = await buttons.count();
  if (count !== 2) {
    throw new Error(`确认框按钮数量异常：${count}; ${JSON.stringify(await buttons.allTextContents())}`);
  }
  await buttons.nth(1).click();
}

async function main() {
  const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));
  if (!executablePath) throw new Error("未找到 Chrome 或 Edge。");
  fs.mkdirSync(evidenceRoot, { recursive: true });

  const suffix = String(Date.now());
  const customerName = `本地周卡验收-${suffix}`;
  const adminName = `qa_${suffix.slice(-8)}`;
  const rechargeReason = `本地审批验收-${suffix}`;
  await seedRechargeRequest(rechargeReason);

  const browser = await chromium.launch({ executablePath, headless: true });
  const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
  const failures = [];
  const consoleErrors = [];
  await context.route(`${frontend}/api/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const headers = { ...request.headers() };
    delete headers.host;
    await route.continue({
      url: `${backend}${url.pathname}${url.search}`,
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
    await page.goto(`${frontend}/login`, { waitUntil: "networkidle" });
    await page.getByLabel("激活码").fill("LOCALQA8");
    await page.getByRole("button", { name: "进入工作台", exact: true }).click();
    await page.waitForURL(/\/(pipeline|production)/, { timeout: 15_000 });
    await page.goto(`${frontend}/admin`, { waitUntil: "networkidle" });
    await page.getByPlaceholder("管理员账号").fill("admin");
    const passwordInput = page.getByPlaceholder("请输入管理密码");
    await passwordInput.fill("LocalQAAdmin2026Pass");
    await passwordInput.press("Enter");
    await page.getByText("先处理需要你确认的事，再管理客户和账号。", { exact: true }).waitFor();

    await page.getByRole("button", { name: "生成激活码", exact: true }).click();
    const generate = page.getByRole("dialog", { name: "生成激活码", exact: true });
    await generate.getByPlaceholder("如：王老板 / 某某公司").fill(customerName);
    if (await generate.getByLabel("可使用天数").inputValue() !== "7") {
      throw new Error("周卡默认使用天数不是 7 天。");
    }
    if (Number(await generate.getByLabel("套餐内含可用积分").inputValue()) !== 9.9) {
      throw new Error("周卡默认可用积分不是 9.9。");
    }
    await clickVisibleModalPrimary(page);
    await page.getByRole("button", { name: "知道了", exact: true }).click();

    const customerRow = page.getByRole("row").filter({ hasText: customerName });
    await customerRow.waitFor({ state: "visible" });
    const generatedText = await customerRow.innerText();
    if (!generatedText.includes("7天") || !generatedText.includes("9.9") || !generatedText.includes("待首次激活")) {
      throw new Error(`周卡展示不正确：${generatedText}`);
    }

    await customerRow.getByRole("button", { name: "调整使用期", exact: true }).click();
    const extend = page.getByRole("dialog", { name: `延长使用期：${customerName}`, exact: true });
    if (await extend.getByRole("spinbutton").count() !== 1) {
      throw new Error("续期弹窗不应再出现积分输入框。");
    }
    await clickVisibleModalPrimary(page);
    await page.getByText(`已为 ${customerName} 延长 7 天`, { exact: false }).waitFor();

    await customerRow.getByRole("button", { name: "充值", exact: true }).click();
    const recharge = page.getByRole("dialog", { name: new RegExp(`^为客户充值：${customerName}`) });
    await recharge.getByRole("spinbutton").fill("5");
    await clickVisibleModalPrimary(page);
    await page.getByText(`已为 ${customerName} 充值 5 积分`, { exact: false }).waitFor();

    await customerRow.getByRole("button", { name: "禁用", exact: true }).click();
    await confirmVisiblePopconfirm(page);
    await page.getByText("已禁用该激活码", { exact: true }).waitFor();
    await customerRow.getByRole("button", { name: "启用", exact: true }).click();
    await confirmVisiblePopconfirm(page);
    await page.getByText("已启用该激活码", { exact: true }).waitFor();

    const pendingRow = page.locator("tr.ant-table-row").filter({ hasText: rechargeReason });
    await pendingRow.waitFor({ state: "visible" });
    if (!(await pendingRow.innerText()).includes("本地整体验收")) {
      throw new Error(`充值申请没有显示客户名称：${await pendingRow.innerText()}`);
    }
    const pendingButtons = pendingRow.locator("button");
    const pendingButtonTexts = await pendingButtons.allTextContents();
    const approveIndex = pendingButtonTexts.findIndex(
      (text) => text.replace(/\s+/g, "") === "批准",
    );
    if (approveIndex < 0) {
      throw new Error(`待审批行没有批准按钮：${JSON.stringify(pendingButtonTexts)}`);
    }
    await pendingButtons.nth(approveIndex).click();
    await confirmVisiblePopconfirm(page);
    await page.getByText("已批准并充值", { exact: true }).waitFor();

    await page.getByPlaceholder("新管理员账号").fill(adminName);
    await page.getByPlaceholder("密码（至少 12 位）").fill("LocalAdminPass2026!");
    await page.getByRole("button", { name: "新增管理员", exact: true }).click();
    const adminRow = page.getByRole("row").filter({ hasText: adminName });
    await adminRow.waitFor({ state: "visible" });
    await adminRow.getByRole("button", { name: "重置密码", exact: true }).click();
    const reset = page.getByRole("dialog", { name: `重置密码：${adminName}`, exact: true });
    await reset.getByPlaceholder("新密码（至少 12 位）").fill("LocalAdminPass2027!");
    await clickVisibleModalPrimary(page);
    await page.getByText(`已重置 ${adminName} 的密码`, { exact: true }).waitFor();
    await adminRow.getByRole("button", { name: "删除", exact: true }).click();
    await confirmVisiblePopconfirm(page);
    await page.getByText(`已删除管理员 ${adminName}`, { exact: true }).waitFor();

    await page.getByRole("button", { name: "查看收费项目", exact: true }).click();
    const pricingToggle = page.locator('.admin-pricing-card button[aria-expanded="true"]');
    await pricingToggle.waitFor({ state: "visible" });
    if ((await pricingToggle.textContent())?.replace(/\s+/g, "") !== "收起") {
      throw new Error(`收费项目展开状态异常：${await pricingToggle.textContent()}`);
    }
    await page.screenshot({
      path: path.join(evidenceRoot, "local-admin-controls.png"),
      fullPage: true,
    });

    if (failures.length) throw new Error(`API 失败：${failures.join(", ")}`);
    if (consoleErrors.length) throw new Error(`控制台错误：${consoleErrors.join(" | ")}`);
    process.stdout.write(JSON.stringify({
      ok: true,
      weeklyPackage: "7 days / 9.9 credits",
      customerLifecycle: ["generated", "adjusted", "credited", "disabled", "enabled"],
      rechargeRequest: "approved",
      administrator: ["created", "password reset", "deleted"],
      pricing: "expanded without modification",
    }, null, 2));
  } catch (error) {
    const screenshot = path.join(evidenceRoot, "local-admin-controls-failed.png");
    await page.screenshot({ path: screenshot, fullPage: true }).catch(() => undefined);
    error.message = [
      error.message,
      failures.length ? `API 失败：${failures.join(", ")}` : "",
      consoleErrors.length ? `控制台错误：${consoleErrors.join(" | ")}` : "",
      `失败截图：${screenshot}`,
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
