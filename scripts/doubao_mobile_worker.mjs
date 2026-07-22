import { execFile } from "node:child_process";
import { Buffer } from "node:buffer";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const apiBase = args.get("--api") || "http://127.0.0.1:2001/api/v1/crawler";
const pollMs = Number(args.get("--poll-ms") || "2500");
const appiumBase = process.env.DOUBAO_MOBILE_APPIUM_URL || "http://127.0.0.1:4723";
const adbPath = process.env.ADB_PATH || "adb";
const doubaoPackage = process.env.DOUBAO_ANDROID_PACKAGE || "";
const workerId = `doubao-mobile-${process.pid}`;
const douyinShortRe = /https:\/\/v\.douyin\.com\/[A-Za-z0-9_-]+\/?/;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function runAdb(adbArgs, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const child = execFile(
      adbPath,
      adbArgs,
      { timeout: timeoutMs, windowsHide: true },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error((stderr || stdout || error.message).trim()));
          return;
        }
        resolve(stdout.trim());
      },
    );
    child.stdin?.end();
  });
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

async function appium(path, options = {}) {
  const response = await fetch(`${appiumBase}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.value?.message || body.message || `Appium ${response.status}`);
  }
  return body.value ?? body;
}

async function updateStage(job, stage, progress, outputs = {}) {
  await api(`/doubao-mobile/jobs/${job.task_id}/stage`, {
    method: "POST",
    body: JSON.stringify({ stage, progress, outputs }),
  }).catch((error) => console.log("stage update failed", error.message));
}

async function failJob(job, error, stage = "安卓手机自动化失败") {
  await api(`/doubao-mobile/jobs/${job.task_id}/fail`, {
    method: "POST",
    body: JSON.stringify({
      error_message: String(error.message || error).slice(0, 500),
      stage,
      retryable: true,
    }),
  }).catch((failError) => console.log("fail update failed", failError.message));
}

async function ensureDevice() {
  const devices = await runAdb(["devices"]);
  const ready = devices
    .split(/\r?\n/)
    .slice(1)
    .map((line) => line.trim())
    .filter((line) => line.endsWith("\tdevice"));
  if (ready.length === 0) {
    throw new Error("没有检测到可用安卓设备；请开启 USB 调试并确认 adb devices 显示 device。");
  }
}

async function createSession() {
  const caps = {
    platformName: "Android",
    "appium:automationName": "UiAutomator2",
    "appium:noReset": true,
    "appium:newCommandTimeout": 300,
  };
  const value = await appium("/session", {
    method: "POST",
    body: JSON.stringify({
      capabilities: {
        alwaysMatch: caps,
        firstMatch: [{}],
      },
    }),
  });
  return value.sessionId;
}

async function deleteSession(sessionId) {
  if (!sessionId) return;
  await fetch(`${appiumBase}/session/${sessionId}`, { method: "DELETE" }).catch(() => {});
}

function uiSelector(kind, text) {
  const escaped = text.replaceAll("\\", "\\\\").replaceAll('"', '\\"');
  return `new UiSelector().${kind}("${escaped}")`;
}

async function findElement(sessionId, selector, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return await appium(`/session/${sessionId}/element`, {
        method: "POST",
        body: JSON.stringify({
          using: "-android uiautomator",
          value: selector,
        }),
      });
    } catch (error) {
      lastError = error;
      await sleep(600);
    }
  }
  throw lastError || new Error(`未找到元素：${selector}`);
}

async function clickElement(sessionId, element) {
  const id = element.ELEMENT || element["element-6066-11e4-a52e-4f735466cecf"];
  await appium(`/session/${sessionId}/element/${id}/click`, { method: "POST", body: "{}" });
}

async function firstClickable(sessionId, selectors, timeoutMs = 12000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    for (const selector of selectors) {
      try {
        return await findElement(sessionId, selector, 800);
      } catch (error) {
        lastError = error;
      }
    }
    await sleep(500);
  }
  throw lastError || new Error("未找到可点击元素。");
}

async function mobileShell(sessionId, command, adbArgs = []) {
  return appium(`/session/${sessionId}/execute/sync`, {
    method: "POST",
    body: JSON.stringify({
      script: "mobile: shell",
      args: [{ command, args: adbArgs, timeout: 20000 }],
    }),
  });
}

async function setClipboard(sessionId, text) {
  await appium(`/session/${sessionId}/appium/device/set_clipboard`, {
    method: "POST",
    body: JSON.stringify({
      content: Buffer.from(text, "utf8").toString("base64"),
      contentType: "plaintext",
      label: "doubao-prompt",
    }),
  });
}

async function getClipboard(sessionId) {
  const value = await appium(`/session/${sessionId}/appium/device/get_clipboard`, {
    method: "POST",
    body: JSON.stringify({ contentType: "plaintext" }),
  });
  return Buffer.from(String(value || ""), "base64").toString("utf8");
}

async function getPageSource(sessionId) {
  return appium(`/session/${sessionId}/source`);
}

function xmlUnescape(value) {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&amp;", "&");
}

function extractReadableTexts(xml) {
  const texts = [];
  const attrRe = /\s(?:text|content-desc)="([^"]+)"/g;
  let match;
  while ((match = attrRe.exec(xml))) {
    const text = xmlUnescape(match[1]).trim();
    if (text.length > 0) texts.push(text);
  }
  return texts;
}

async function copyDouyinShareLink(sessionId, sourceUrl) {
  if (douyinShortRe.test(sourceUrl)) {
    return sourceUrl.match(douyinShortRe)[0];
  }
  await mobileShell(sessionId, "am", [
    "start",
    "-a",
    "android.intent.action.VIEW",
    "-d",
    sourceUrl,
  ]);
  await sleep(5000);

  const share = await firstClickable(sessionId, [
    uiSelector("descriptionContains", "分享"),
    uiSelector("textContains", "分享"),
  ], 15000);
  await clickElement(sessionId, share);
  await sleep(1800);

  const copy = await firstClickable(sessionId, [
    uiSelector("textContains", "复制链接"),
    uiSelector("descriptionContains", "复制链接"),
    uiSelector("textContains", "复制口令"),
    uiSelector("descriptionContains", "复制口令"),
  ], 15000);
  await clickElement(sessionId, copy);
  await sleep(1200);

  const clipboard = await getClipboard(sessionId).catch(() => "");
  const shortUrl = clipboard.match(douyinShortRe)?.[0] || null;
  if (!shortUrl) {
    throw new Error("已点击抖音分享复制，但没有从安卓剪贴板读取到 v.douyin.com 短链。");
  }
  return shortUrl;
}

function buildPrompt(job, shortUrl) {
  return [
    job.media_name || job.title,
    shortUrl,
    "请把这个抖音视频转成原版口播文案。",
    "要求：只输出口播正文；尽量保留原话、口语停顿和段落；不要总结、不要改写、不要扩写；如果无法读取视频，请直接说明无法访问链接。",
  ].join("\n");
}

async function openDoubao(sessionId) {
  if (!doubaoPackage) {
    throw new Error("未配置 DOUBAO_ANDROID_PACKAGE；请填豆包 App 包名后重启后端/执行器。");
  }
  await mobileShell(sessionId, "monkey", [
    "-p",
    doubaoPackage,
    "-c",
    "android.intent.category.LAUNCHER",
    "1",
  ]);
  await sleep(3500);
}

async function sendPromptToDoubao(sessionId, prompt) {
  await setClipboard(sessionId, prompt);
  const input = await firstClickable(sessionId, [
    "new UiSelector().className(\"android.widget.EditText\")",
    uiSelector("textContains", "发消息"),
    uiSelector("descriptionContains", "发消息"),
    uiSelector("textContains", "问一下"),
    uiSelector("descriptionContains", "问一下"),
  ], 15000);
  await clickElement(sessionId, input);
  await sleep(500);
  await mobileShell(sessionId, "input", ["keyevent", "279"]);
  await sleep(700);
  const send = await firstClickable(sessionId, [
    uiSelector("textContains", "发送"),
    uiSelector("descriptionContains", "发送"),
  ], 8000);
  await clickElement(sessionId, send);
}

function isAccessFailure(text) {
  return /无法访问|无法读取|不能访问|获取不到|暂时没办法|没办法提取|外部接口限制/.test(text);
}

function selectDoubaoAnswer(texts, prompt) {
  const promptHead = prompt.slice(0, 40);
  const candidates = texts
    .filter((text) => text.length > 20)
    .filter((text) => !text.includes(promptHead))
    .filter((text) => !/^发送$|^复制$|^分享$|^重新生成$/.test(text));
  return candidates.at(-1) || "";
}

async function readDoubaoAnswer(sessionId, prompt) {
  const deadline = Date.now() + 180000;
  let lastText = "";
  let stable = 0;
  while (Date.now() < deadline) {
    const xml = await getPageSource(sessionId);
    const text = selectDoubaoAnswer(extractReadableTexts(xml), prompt);
    if (text) {
      if (text === lastText) {
        stable += 1;
      } else {
        stable = 0;
        lastText = text;
      }
      if (stable >= 3) return lastText;
    }
    await sleep(2000);
  }
  throw new Error("等待豆包手机端回复超时。");
}

async function processJob(job) {
  let sessionId = "";
  try {
    await updateStage(job, "正在检查安卓设备与 Appium", 15);
    await ensureDevice();
    sessionId = await createSession();

    await updateStage(job, "正在打开抖音并复制分享短链", 35);
    const shortUrl = await copyDouyinShareLink(sessionId, job.source_url);
    await updateStage(job, "已拿到抖音分享短链，正在打开豆包 App", 55, {
      douyin_short_url: shortUrl,
    });

    const prompt = buildPrompt(job, shortUrl);
    await openDoubao(sessionId);
    await updateStage(job, "正在向豆包手机端发送链接", 70);
    await sendPromptToDoubao(sessionId, prompt);
    await updateStage(job, "等待豆包手机端生成文案", 85);
    const answer = await readDoubaoAnswer(sessionId, prompt);
    if (isAccessFailure(answer)) {
      throw new Error(`豆包手机端未能读取视频：${answer.slice(0, 160)}`);
    }

    await api(`/doubao-mobile/jobs/${job.task_id}/complete`, {
      method: "POST",
      body: JSON.stringify({
        transcript_text: answer,
        short_url: shortUrl,
        doubao_conversation_url: `android://${doubaoPackage}`,
        doubao_message_id: "",
      }),
    });
    console.log(`[${new Date().toISOString()}] completed ${job.task_id}`);
  } catch (error) {
    await failJob(job, error, "需要检查手机端登录、弹窗或选择器");
    console.log(`[${new Date().toISOString()}] failed ${job.task_id}: ${error.message}`);
  } finally {
    await deleteSession(sessionId);
  }
}

async function main() {
  console.log(`[${new Date().toISOString()}] worker started ${workerId}`);
  while (true) {
    try {
      const claimed = await api(
        `/doubao-mobile/jobs/claim?worker_id=${encodeURIComponent(workerId)}`,
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
