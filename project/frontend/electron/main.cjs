const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");
const { existsSync, mkdirSync, readFileSync } = require("node:fs");
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

const APP_URL = "http://127.0.0.1:1001/login";
const HEALTH_URL = "http://127.0.0.1:1001/health";
let backendProcess = null;
let mainWindow = null;
let updateCheckStarted = false;

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

function startBackend() {
  const executable = backendExecutable();
  if (!existsSync(executable)) {
    throw new Error(`缺少本地服务文件：${executable}`);
  }
  const runtimeRoot = backendRuntimeRoot();
  mkdirSync(runtimeRoot, { recursive: true });
  backendProcess = spawn(executable, [], {
    cwd: runtimeRoot,
    windowsHide: true,
    env: sanitizeBackendEnvironment(process.env, {
      VIDEOINSIGHT_NO_BROWSER: "true",
      VIDEOINSIGHT_RUNTIME_ROOT: runtimeRoot,
      VIDEOINSIGHT_NODE_EXECUTABLE: process.execPath,
      VIDEOINSIGHT_NODE_AS_ELECTRON: "true",
    }),
  });
  backendProcess.once("exit", (code) => {
    backendProcess = null;
    if (!app.isQuitting && code !== 0) {
      dialog.showErrorBox(
        "VideoInsight 本地服务已停止",
        "请重新启动应用；如果仍然失败，请把本机 VideoInsight 数据目录中的 desktop.log 发给技术人员。",
      );
    }
  });
}

async function waitForBackend(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(1000) });
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
    if (url.startsWith("http://127.0.0.1:1001/")) {
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
  startBackend();
  await waitForBackend();
  await mainWindow.loadURL(APP_URL);
  setTimeout(() => void checkForUpdate(), 3000);
}

app.whenReady().then(() => {
  boot().catch((error) => {
    dialog.showErrorBox(
      "VideoInsight 启动失败",
      `${error.message}\n\n请查看本机 VideoInsight 数据目录中的 desktop.log。`,
    );
    app.quit();
  });
});

app.on("window-all-closed", () => app.quit());

app.on("before-quit", () => {
  app.isQuitting = true;
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
});
