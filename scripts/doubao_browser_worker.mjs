import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, "..");
const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const apiBase = args.get("--api") || "http://127.0.0.1:2001/api/v1/crawler";
const debugPort = Number(args.get("--debug-port") || "9225");
const pollMs = Number(args.get("--poll-ms") || "2500");
const workerId = `doubao-browser-${process.pid}`;
const profileDir = resolve(projectRoot, "data", "browser_profiles", "doubao-worker");
const douyinShortRe = /https:\/\/v\.douyin\.com\/[A-Za-z0-9_-]+\/?/;

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

async function api(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || body.message || `API ${response.status}`);
  }
  return response.json();
}

function findChromeExecutable() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean);
  const found = candidates.find((candidate) => existsSync(candidate));
  if (!found) {
    throw new Error("未找到 Chrome/Edge，可设置 CHROME_PATH 指向浏览器可执行文件。");
  }
  return found;
}

async function httpJson(path, method = "GET") {
  const response = await fetch(`http://127.0.0.1:${debugPort}${path}`, { method });
  if (!response.ok) {
    throw new Error(`Chrome DevTools ${response.status}`);
  }
  return response.json();
}

async function ensureChrome() {
  try {
    await httpJson("/json/version");
    return;
  } catch {
    // Start a dedicated visible browser profile. This is normal automation, not
    // stealth browsing; login and verification prompts remain visible.
  }
  mkdirSync(profileDir, { recursive: true });
  const chrome = findChromeExecutable();
  const child = spawn(
    chrome,
    [
      `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${profileDir}`,
      "--new-window",
      "about:blank",
    ],
    {
      cwd: projectRoot,
      detached: true,
      stdio: "ignore",
    },
  );
  child.unref();
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      await httpJson("/json/version");
      return;
    } catch {
      await sleep(500);
    }
  }
  throw new Error("专用浏览器启动超时。");
}

class CdpPage {
  constructor(target) {
    this.target = target;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    this.ws = new WebSocket(this.target.webSocketDebuggerUrl);
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => rejectOpen(new Error("CDP 连接超时")), 10000);
      this.ws.addEventListener("open", () => {
        clearTimeout(timer);
        resolveOpen();
      });
      this.ws.addEventListener("error", () => {
        clearTimeout(timer);
        rejectOpen(new Error("CDP 连接失败"));
      });
    });
    this.ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (!payload.id) return;
      const pending = this.pending.get(payload.id);
      if (!pending) return;
      this.pending.delete(payload.id);
      if (payload.error) pending.reject(new Error(payload.error.message));
      else pending.resolve(payload.result);
    });
    await this.send("Page.enable");
    await this.send("Runtime.enable");
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const message = JSON.stringify({ id, method, params });
    return new Promise((resolveSend, rejectSend) => {
      this.pending.set(id, { resolve: resolveSend, reject: rejectSend });
      this.ws.send(message);
    });
  }

  async evaluate(fn, ...values) {
    const expression = `(${fn})(...${JSON.stringify(values)})`;
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "页面脚本执行失败");
    }
    return result.result?.value;
  }

  close() {
    try {
      this.ws?.close();
    } catch {
      // ignore close errors
    }
  }
}

async function openPage(url) {
  const target = await httpJson(`/json/new?${encodeURIComponent(url)}`, "PUT");
  const page = new CdpPage(target);
  await page.connect();
  await sleep(1500);
  return page;
}

async function updateStage(job, stage, progress, outputs = {}) {
  await api(`/doubao-browser/jobs/${job.task_id}/stage`, {
    method: "POST",
    body: JSON.stringify({ stage, progress, outputs }),
  }).catch((error) => console.log("stage update failed", error.message));
}

async function extractDouyinShortUrl(page, sourceUrl) {
  if (douyinShortRe.test(sourceUrl)) {
    return sourceUrl.match(douyinShortRe)[0];
  }
  const existing = await page.evaluate(() => {
    const text = [
      document.body?.innerText || "",
      ...Array.from(document.images).map((image) => image.alt || ""),
    ].join("\n");
    return text.match(/https:\/\/v\.douyin\.com\/[A-Za-z0-9_-]+\/?/)?.[0] || null;
  });
  if (existing) return existing;

  await page.evaluate(() => {
    const direct = document.querySelector('[data-e2e="video-player-share"]');
    const fallback = Array.from(document.querySelectorAll("button, div, span")).find(
      (node) => (node.textContent || "").trim() === "分享",
    );
    const target = direct || fallback;
    if (!target) return false;
    target.click();
    return true;
  });
  await sleep(1800);

  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const shortUrl = await page.evaluate(() => {
      const chunks = [
        document.body?.innerText || "",
        ...Array.from(document.images).map((image) => image.alt || ""),
        ...Array.from(document.querySelectorAll("[alt], [title], [aria-label]")).map(
          (node) =>
            [
              node.getAttribute("alt"),
              node.getAttribute("title"),
              node.getAttribute("aria-label"),
            ].join(" "),
        ),
      ].join("\n");
      return chunks.match(/https:\/\/v\.douyin\.com\/[A-Za-z0-9_-]+\/?/)?.[0] || null;
    });
    if (shortUrl) return shortUrl;
    await sleep(1000);
  }
  throw new Error("没有从抖音分享弹窗拿到 v.douyin.com 短链。");
}

function buildPrompt(job, shortUrl) {
  return [
    job.media_name || job.title,
    shortUrl,
    "请把这个抖音视频转成原版口播文案。",
    "要求：只输出口播正文；尽量保留原话、口语停顿和段落；不要总结、不要改写、不要扩写；如果无法读取视频，请直接说明无法访问链接。",
  ].join("\n");
}

async function sendDoubaoPrompt(page, prompt) {
  const sent = await page.evaluate((value) => {
    const textarea =
      document.querySelector('textarea[placeholder*="发消息"]') ||
      document.querySelector("textarea");
    if (!textarea) return false;
    textarea.focus();
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value",
    )?.set;
    setter.call(textarea, value);
    textarea.dispatchEvent(new InputEvent("input", { bubbles: true, data: value }));
    return true;
  }, prompt);
  if (!sent) {
    throw new Error("豆包输入框未出现，可能需要登录或页面结构变更。");
  }
  await sleep(400);
  await page.send("Input.dispatchKeyEvent", {
    type: "keyDown",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
  });
  await page.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: "Enter",
    code: "Enter",
    windowsVirtualKeyCode: 13,
  });
}

async function readDoubaoAnswer(page, prompt) {
  const promptHead = prompt.slice(0, 80);
  const deadline = Date.now() + 180000;
  let lastText = "";
  let stableSince = 0;
  let lastMessageId = "";
  while (Date.now() < deadline) {
    const candidate = await page.evaluate((head) => {
      const nodes = Array.from(document.querySelectorAll("[data-message-id]"));
      const messages = nodes
        .map((node) => ({
          id: node.getAttribute("data-message-id") || "",
          text: (node.innerText || "").trim(),
          className: String(node.className || ""),
        }))
        .filter((item) => item.text && !item.text.includes(head))
        .filter((item) => item.text.length > 20);
      return messages.at(-1) || null;
    }, promptHead);
    if (candidate?.text) {
      if (candidate.text === lastText) {
        stableSince += 1;
      } else {
        stableSince = 0;
        lastText = candidate.text;
        lastMessageId = candidate.id || "";
      }
      if (stableSince >= 3) {
        return { text: lastText, messageId: lastMessageId };
      }
    }
    await sleep(2000);
  }
  throw new Error("等待豆包回复超时。");
}

function isAccessFailure(text) {
  return /无法访问|无法读取|不能访问|获取不到|暂时没办法|没办法提取|外部接口限制/.test(
    text,
  );
}

async function processJob(job) {
  let douyinPage;
  let doubaoPage;
  try {
    await updateStage(job, "正在打开抖音视频", 20);
    douyinPage = await openPage(job.source_url);
    await sleep(5000);
    await updateStage(job, "正在点击分享并读取抖音短链", 35);
    const shortUrl = await extractDouyinShortUrl(douyinPage, job.source_url);
    await updateStage(job, "已拿到抖音短链，正在打开豆包", 50, {
      douyin_short_url: shortUrl,
    });

    doubaoPage = await openPage("https://www.doubao.com/chat/");
    await sleep(5000);
    const prompt = buildPrompt(job, shortUrl);
    await updateStage(job, "正在发送豆包提示词", 65);
    await sendDoubaoPrompt(doubaoPage, prompt);
    await updateStage(job, "等待豆包生成文案", 80);
    const answer = await readDoubaoAnswer(doubaoPage, prompt);
    if (isAccessFailure(answer.text)) {
      throw new Error(`豆包未能读取视频：${answer.text.slice(0, 160)}`);
    }
    await api(`/doubao-browser/jobs/${job.task_id}/complete`, {
      method: "POST",
      body: JSON.stringify({
        transcript_text: answer.text,
        short_url: shortUrl,
        doubao_conversation_url: doubaoPage.target.url || "https://www.doubao.com/chat/",
        doubao_message_id: answer.messageId,
      }),
    });
    console.log(`[${new Date().toISOString()}] completed ${job.task_id}`);
  } catch (error) {
    await api(`/doubao-browser/jobs/${job.task_id}/fail`, {
      method: "POST",
      body: JSON.stringify({
        error_message: error.message,
        stage: "需要人工处理或重试",
        retryable: true,
      }),
    }).catch((failError) => console.log("fail update failed", failError.message));
    console.log(`[${new Date().toISOString()}] failed ${job.task_id}: ${error.message}`);
  } finally {
    douyinPage?.close();
    doubaoPage?.close();
  }
}

async function main() {
  console.log(`[${new Date().toISOString()}] worker started ${workerId}`);
  await ensureChrome();
  while (true) {
    try {
      const claimed = await api(
        `/doubao-browser/jobs/claim?worker_id=${encodeURIComponent(workerId)}`,
        { method: "POST" },
      );
      if (claimed.job) {
        await processJob(claimed.job);
      } else {
        await sleep(pollMs);
      }
    } catch (error) {
      console.log(`[${new Date().toISOString()}] worker loop error: ${error.message}`);
      await sleep(Math.max(pollMs, 5000));
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
