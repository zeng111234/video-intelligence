const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");
const { appendFileSync, existsSync, mkdirSync, readFileSync } = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const os = require("node:os");
const { resolveBackendRuntimeRoot, sanitizeBackendEnvironment } = require("./environment.cjs");
const {
  buildUpdateProgressHtml,
  compareVersions,
  downloadInstaller,
  fetchManifest,
  validateReleaseConfig,
} = require("./update.cjs");

let backendProcess = null;
let mainWindow = null;
let updateCheckStarted = false;
let backendOrigin = "";

function findAvailableLoopbackPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => {
        if (error) reject(error);
        else if (!port) reject(new Error("未能分配本机服务端口"));
        else resolve(port);
      });
    });
  });
}

function formatMegabytes(bytes) {
  return `${(Number(bytes || 0) / (1024 * 1024)).toFixed(1)} MB`;
}

async function createUpdateProgressWindow({ version, destination }) {
  const progressWindow = new BrowserWindow({
    width: 560,
    height: 360,
    parent: mainWindow || undefined,
    modal: Boolean(mainWindow),
    show: false,
    closable: false,
    resizable: false,
    maximizable: false,
    minimizable: true,
    backgroundColor: "#f6f8fc",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  progressWindow.removeMenu();
  await progressWindow.loadURL(
    `data:text/html;charset=utf-8,${encodeURIComponent(
      buildUpdateProgressHtml({ version, destination }),
    )}`,
  );
  progressWindow.show();
  return progressWindow;
}

function renderUpdateProgress(progressWindow, state) {
  if (!progressWindow || progressWindow.isDestroyed()) return;
  progressWindow.setProgressBar(Math.max(0, Math.min(1, Number(state.percent || 0) / 100)));
  mainWindow?.setProgressBar(Math.max(0, Math.min(1, Number(state.percent || 0) / 100)));
  void progressWindow.webContents
    .executeJavaScript(`window.renderUpdateProgress(${JSON.stringify(state)})`, true)
    .catch(() => undefined);
}

function launchInstaller(destination) {
  return new Promise((resolve, reject) => {
    const installer = spawn(destination, [], {
      cwd: path.dirname(destination),
      detached: true,
      windowsHide: false,
      stdio: "ignore",
    });
    installer.once("error", reject);
    installer.once("spawn", () => {
      installer.unref();
      resolve();
    });
  });
}

app.setName("VideoInsight");

// 单实例锁：拿不到锁说明本机已经有一个 VideoInsight 在运行。
//
// 历史缺陷（0.2.51）：这里只调用了 app.quit()，但下面的
// app.whenReady().then(boot) 依然照常注册。app.quit() 是异步的，whenReady
// 仍然会触发，于是第二个实例照样跑完 boot()，再拉起一个
// VideoInsightBackend.exe —— 表现为重复窗口、任务管理器里多个后端，
// 以及关掉其中一个时弹出的"本地服务已停止"。
//
// 因此把启动与生命周期注册整体放进 else：没有锁就什么都不注册。
const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
  app.quit();
}

function backendRuntimeRoot() {
  return resolveBackendRuntimeRoot({
    executablePath: process.execPath,
    localAppData: process.env.LOCALAPPDATA,
    fallbackUserData: app.getPath("userData"),
    existsSync,
    readFileSync,
  });
}

function backendExecutable() {
  return path.join(process.resourcesPath, "backend", "VideoInsightBackend.exe");
}

function appendBackendLifecycle(event, details = {}) {
  appendDesktopLog("INFO", "electron.backend", `${event} ${JSON.stringify(details)}`);
}

// 后端原始输出必须落盘，否则崩溃时只剩一个 code 1，最底层异常永远查不出来。
// 0.2.51 的两次升级失败就是这样失去证据的。
const LOG_REDACTIONS = [
  // Authorization / Cookie / Set-Cookie 头
  [/\b(authorization|cookie|set-cookie|x-api-key|x-customer-token|x-admin-token)\b\s*[:=]\s*[^\r\n]+/gi, "$1=[已隐藏]"],
  // 常见密钥形态
  [/\b(sk-[A-Za-z0-9._-]{4})[A-Za-z0-9._-]+/g, "$1…[已隐藏]"],
  [/\b(vpro_[A-Za-z0-9._-]{4})[A-Za-z0-9._-]+/g, "$1…[已隐藏]"],
  // password / passwd / secret / token 字段
  [/("?(?:password|passwd|secret|token|api_key|apiCode|api_code)"?\s*[:=]\s*"?)[^"',\s}]+/gi, "$1[已隐藏]"],
  // 请求正文：后端不打印正文，但一旦出现也拦掉
  [/("?(?:request_body|payload_json|body)"?\s*[:=]\s*"?)[^"',\s}]{24,}/gi, "$1[已隐藏]"],
];

function redactForLog(text) {
  let value = String(text ?? "");
  for (const [pattern, replacement] of LOG_REDACTIONS) {
    value = value.replace(pattern, replacement);
  }
  return value;
}

function appendDesktopLog(level, scope, message) {
  try {
    const runtimeRoot = backendRuntimeRoot();
    const logDirectory = path.join(runtimeRoot, "data", "logs");
    mkdirSync(logDirectory, { recursive: true });
    const body = redactForLog(message).replace(/\s+$/, "");
    appendFileSync(
      path.join(logDirectory, "desktop.log"),
      `${new Date().toISOString()} ${level} ${scope} ${body}\n`,
      "utf8",
    );
  } catch {
    // 日志写不进去也绝不能阻断桌面端启动。
  }
}

// 后端管道按行落盘，并带上 PID，便于把多实例输出区分开。
function pipeBackendOutput(stream, { pid, channel }) {
  if (!stream) return;
  let buffered = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    buffered += chunk;
    let index = buffered.indexOf("\n");
    while (index >= 0) {
      const line = buffered.slice(0, index);
      buffered = buffered.slice(index + 1);
      if (line.trim()) {
        appendDesktopLog("ERROR", `backend.${channel}`, `pid=${pid} ${line}`);
      }
      index = buffered.indexOf("\n");
    }
    // 单行过长时先冲掉，避免无限占用内存。
    if (buffered.length > 8192) {
      appendDesktopLog("ERROR", `backend.${channel}`, `pid=${pid} ${buffered}`);
      buffered = "";
    }
  });
  stream.on("end", () => {
    if (buffered.trim()) {
      appendDesktopLog("ERROR", `backend.${channel}`, `pid=${pid} ${buffered}`);
    }
    buffered = "";
  });
}

function releaseConfiguration() {
  const configurationPath = path.join(process.resourcesPath, "config", "release.json");
  if (!existsSync(configurationPath)) return null;
  try {
    return validateReleaseConfig(JSON.parse(readFileSync(configurationPath, "utf8")));
  } catch {
    return null;
  }
}

async function checkForUpdate() {
  if (updateCheckStarted) return;
  updateCheckStarted = true;
  const configuration = releaseConfiguration();
  if (!configuration) return;
  let manifest;
  let progressWindow = null;
  try {
    manifest = await fetchManifest(configuration.controlPlaneUrl);
  } catch {
    return;
  }
  if (!manifest || compareVersions(manifest.version, configuration.currentVersion) <= 0) return;
  try {
    const answer = await dialog.showMessageBox(mainWindow, {
      type: "info",
      title: "VideoInsight 有新版本",
      message: `发现新版本 ${manifest.version}`,
      detail: manifest.notes || "更新会保留本机数据，下载完成后自动覆盖安装。",
      buttons: ["下载并更新", "稍后再说"],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (answer.response !== 0) return;
    const updateDirectory = path.join(
      os.tmpdir(),
      "VideoInsight-updates",
      manifest.version,
      `${Date.now()}-${process.pid}`,
    );
    const destination = path.join(updateDirectory, manifest.installer);
    progressWindow = await createUpdateProgressWindow({
      version: manifest.version,
      destination,
    });
    let lastProgressAt = 0;
    let lastPercent = -1;
    await downloadInstaller({
      controlPlaneUrl: configuration.controlPlaneUrl,
      manifest,
      destination,
      onProgress: ({ downloadedBytes, totalBytes, percent }) => {
        const now = Date.now();
        if (percent < 100 && now - lastProgressAt < 150 && percent - lastPercent < 0.5) return;
        lastProgressAt = now;
        lastPercent = percent;
        renderUpdateProgress(progressWindow, {
          percent,
          status: percent >= 100 ? "下载完成，正在校验并打开安装程序…" : "正在下载更新，请不要关闭软件…",
          detail: `${percent.toFixed(1)}% · ${formatMegabytes(downloadedBytes)} / ${formatMegabytes(totalBytes)}`,
        });
      },
    });
    renderUpdateProgress(progressWindow, {
      percent: 100,
      status: "校验通过，正在打开安装程序…",
      detail: `100% · 安装包已保存到 ${destination}`,
    });
    await launchInstaller(destination);
    app.quit();
  } catch (error) {
    mainWindow?.setProgressBar(-1);
    if (progressWindow && !progressWindow.isDestroyed()) progressWindow.destroy();
    await dialog.showMessageBox(mainWindow, {
      type: "warning",
      title: "暂时无法更新",
      message: "更新没有完成，当前版本仍可继续使用。",
      detail:
        "请关闭其他 VideoInsight 安装窗口后重新打开软件再试一次。仍失败时，请把 installer-bootstrap.log 和 desktop.log 发给技术人员。",
      buttons: ["知道了"],
    });
  }
}

let backendRestartAttempted = false;

function startBackend(port) {
  const executable = backendExecutable();
  if (!existsSync(executable)) {
    throw new Error(`缺少本地服务文件：${executable}`);
  }
  const runtimeRoot = backendRuntimeRoot();
  mkdirSync(runtimeRoot, { recursive: true });
  // stdio 必须显式接管。0.2.51 没有传 stdio，Node 默认给子进程建管道却没人读，
  // 后端一多写几行 stderr 就会写满管道阻塞（挂住）——而且异常信息全部丢失。
  // "ignore" stdin 避免子进程等输入；stdout/stderr 由 pipeBackendOutput 落盘。
  backendProcess = spawn(executable, [], {
    cwd: runtimeRoot,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
    env: sanitizeBackendEnvironment(process.env, {
      VIDEOINSIGHT_NO_BROWSER: "true",
      VIDEOINSIGHT_RUNTIME_ROOT: runtimeRoot,
      VIDEOINSIGHT_DESKTOP_PORT: String(port),
      VIDEOINSIGHT_NODE_EXECUTABLE: process.execPath,
      VIDEOINSIGHT_NODE_AS_ELECTRON: "true",
    }),
  });
  const backendPid = backendProcess.pid || null;
  appendBackendLifecycle("backend_spawned", { pid: backendPid, port });
  pipeBackendOutput(backendProcess.stdout, { pid: backendPid, channel: "stdout" });
  pipeBackendOutput(backendProcess.stderr, { pid: backendPid, channel: "stderr" });
  backendProcess.once("error", (error) => {
    appendBackendLifecycle("backend_spawn_error", {
      pid: backendPid,
      code: error.code || "",
    });
  });
  backendProcess.once("exit", (code, signal) => {
    const quitting = Boolean(app.isQuitting);
    appendBackendLifecycle("backend_exited", {
      pid: backendPid,
      code: code === null ? "" : code,
      signal: signal || "",
      app_quitting: quitting,
    });
    backendProcess = null;
    // 正常退出（用户在关软件）时既不重试也不弹窗。
    if (quitting) return;
    if (code === 0) return;
    // 异常退出只自动重试一次，避免无限拉起后端。
    if (!backendRestartAttempted) {
      backendRestartAttempted = true;
      appendBackendLifecycle("backend_restart_attempt", { pid: backendPid, port });
      setTimeout(() => {
        if (app.isQuitting) return;
        try {
          startBackend(port);
        } catch (error) {
          appendBackendLifecycle("backend_restart_failed", {
            message: String(error?.message || error),
          });
          dialog.showErrorBox(
            "VideoInsight 本地服务已停止",
            "自动重启失败。请重新启动应用；如果仍然失败，请把本机 VideoInsight 数据目录中的 desktop.log 发给技术人员。",
          );
        }
      }, 1500);
      return;
    }
    // 重试后仍然失败：只显示一个错误框。
    appendBackendLifecycle("backend_unrecoverable", { pid: backendPid, port });
    dialog.showErrorBox(
      "VideoInsight 本地服务已停止",
      "请重新启动应用；如果仍然失败，请把本机 VideoInsight 数据目录中的 desktop.log 发给技术人员。",
    );
  });
}

async function waitForBackend(healthUrl, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(healthUrl, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
    } catch {
      // The local service may need several seconds on the first launch.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("本地服务启动超时");
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 360,
    minHeight: 640,
    show: false,
    backgroundColor: "#f6f8fc",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.removeMenu();
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (backendOrigin && url.startsWith(`${backendOrigin}/`)) {
      return { action: "allow" };
    }
    shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

async function boot() {
  createWindow();
  mainWindow.loadURL(
    `data:text/html;charset=utf-8,${encodeURIComponent(
      '<style>body{font-family:Segoe UI,sans-serif;display:grid;place-items:center;height:100vh;margin:0;background:#f6f8fc;color:#14213d}div{text-align:center}b{display:block;font-size:24px;margin-bottom:12px}</style><div><b>VideoInsight 正在启动</b>首次启动可能需要几十秒，请稍候…</div>',
    )}`,
  );
  const port = await findAvailableLoopbackPort();
  backendOrigin = `http://127.0.0.1:${port}`;
  startBackend(port);
  await waitForBackend(`${backendOrigin}/health`);
  await mainWindow.loadURL(`${backendOrigin}/login`);
  setTimeout(() => void checkForUpdate(), 3000);
}

function stopBackend() {
  app.isQuitting = true;
  const running = backendProcess;
  backendProcess = null;
  if (!running || running.exitCode !== null) return;
  appendBackendLifecycle("backend_stop_requested", {
    pid: running.pid || null,
  });
  try {
    running.kill();
  } catch (error) {
    appendBackendLifecycle("backend_stop_failed", {
      message: String(error?.message || error),
    });
  }
}

function handleBootFailure(error) {
  appendDesktopLog(
    "ERROR",
    "electron.boot",
    String(error?.stack || error?.message || error),
  );
  dialog.showErrorBox(
    "VideoInsight 启动失败",
    `${error?.message || error}\n\n请查看本机 VideoInsight 数据目录中的 desktop.log。`,
  );
  app.quit();
}

// 只有拿到单实例锁的进程才注册启动与生命周期回调。
// 否则（第二个实例）什么都不做，只等 app.quit() 生效。
if (hasSingleInstanceLock) {
  app.whenReady().then(() => {
    boot().catch(handleBootFailure);
  });

  app.on("second-instance", () => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });

  app.on("window-all-closed", () => app.quit());

  app.on("before-quit", stopBackend);
}
