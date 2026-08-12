const { createHash } = require("node:crypto");
const { createWriteStream, promises: fs } = require("node:fs");
const { pipeline } = require("node:stream/promises");
const { Readable, Transform } = require("node:stream");
const path = require("node:path");

const MAX_MANIFEST_BYTES = 16 * 1024;
const MAX_INSTALLER_BYTES = 1024 * 1024 * 1024;
const INSTALLER_PATTERN = /^VideoInsight-[0-9A-Za-z.-]+-Setup\.exe$/;
const VERSION_PATTERN = /^[0-9]+(?:\.[0-9]+){1,3}(?:-[0-9A-Za-z.-]+)?$/;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function buildUpdateProgressHtml({ version, destination }) {
  const safeVersion = escapeHtml(version);
  const safeDestination = escapeHtml(destination);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>VideoInsight 正在更新</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; padding: 28px; background: #f6f8fc; color: #14213d; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
    h1 { margin: 0 0 8px; font-size: 22px; }
    #status { margin: 0 0 20px; color: #58657a; }
    progress { display: block; width: 100%; height: 18px; accent-color: #7638ef; }
    #amount { margin: 10px 0 20px; color: #35445d; font-variant-numeric: tabular-nums; }
    .location { padding: 12px 14px; border: 1px solid #d9e0ec; border-radius: 10px; background: white; }
    .location strong { display: block; margin-bottom: 5px; }
    #destination { color: #52617a; font-size: 12px; overflow-wrap: anywhere; user-select: text; }
    .note { margin-top: 14px; color: #7b879b; font-size: 13px; line-height: 1.6; }
  </style>
</head>
<body>
  <h1>正在下载 VideoInsight ${safeVersion}</h1>
  <p id="status">正在连接更新服务…</p>
  <progress id="progress" max="100" value="0"></progress>
  <p id="amount">0% · 准备下载</p>
  <div class="location">
    <strong>安装包保存位置</strong>
    <div id="destination">${safeDestination}</div>
  </div>
  <div class="note">下载完成并校验通过后会自动打开安装程序；桌面和开始菜单快捷方式会自动创建或更新。</div>
  <script>
    window.renderUpdateProgress = function (state) {
      var percent = Math.max(0, Math.min(100, Number(state.percent) || 0));
      document.getElementById('progress').value = percent;
      document.getElementById('status').textContent = state.status || '正在下载更新…';
      document.getElementById('amount').textContent = state.detail || (percent.toFixed(1) + '%');
    };
  </script>
</body>
</html>`;
}

function compareVersions(left, right) {
  const numeric = (value) =>
    String(value)
      .split("-", 1)[0]
      .split(".")
      .map((part) => Number.parseInt(part, 10) || 0);
  const a = numeric(left);
  const b = numeric(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if ((a[index] || 0) !== (b[index] || 0)) {
      return (a[index] || 0) > (b[index] || 0) ? 1 : -1;
    }
  }
  return 0;
}

function validateReleaseConfig(raw) {
  const controlPlaneUrl = String(raw?.control_plane_url || "").trim().replace(/\/$/, "");
  const currentVersion = String(raw?.current_version || "").trim();
  const parsed = new URL(controlPlaneUrl);
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    parsed.pathname !== "/"
  ) {
    throw new Error("更新服务地址无效");
  }
  if (!VERSION_PATTERN.test(currentVersion)) throw new Error("当前版本号无效");
  return { controlPlaneUrl, currentVersion };
}

function validateManifest(raw) {
  const version = String(raw?.version || "").trim();
  const installer = String(raw?.installer || "").trim();
  const sha256 = String(raw?.sha256 || "").trim().toLowerCase();
  const sizeBytes = Number(raw?.size_bytes);
  const notes = String(raw?.notes || "").trim().slice(0, 500);
  if (!VERSION_PATTERN.test(version)) throw new Error("更新版本号无效");
  if (
    !INSTALLER_PATTERN.test(installer) ||
    installer !== `VideoInsight-${version}-Setup.exe`
  ) {
    throw new Error("更新文件名与版本不一致");
  }
  if (!/^[a-f0-9]{64}$/.test(sha256)) throw new Error("更新校验值无效");
  if (!Number.isSafeInteger(sizeBytes) || sizeBytes <= 0 || sizeBytes > MAX_INSTALLER_BYTES) {
    throw new Error("更新文件大小无效");
  }
  return { version, installer, sha256, sizeBytes, notes };
}

async function fetchManifest(controlPlaneUrl, fetchImpl = fetch) {
  const url = new URL("desktop-updates/latest.json", `${controlPlaneUrl}/`);
  const response = await fetchImpl(url, {
    headers: { Accept: "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("暂时无法读取更新信息");
  const text = await response.text();
  if (Buffer.byteLength(text, "utf8") > MAX_MANIFEST_BYTES) {
    throw new Error("更新信息过大");
  }
  return validateManifest(JSON.parse(text));
}

async function downloadInstaller({
  controlPlaneUrl,
  manifest,
  destination,
  fetchImpl = fetch,
  onProgress = () => undefined,
}) {
  const url = new URL(
    `desktop-updates/${encodeURIComponent(manifest.installer)}`,
    `${controlPlaneUrl}/`,
  );
  const response = await fetchImpl(url, {
    redirect: "error",
    signal: AbortSignal.timeout(30 * 60 * 1000),
  });
  if (!response.ok || !response.body) throw new Error("新版安装包下载失败");
  const advertisedLength = Number(response.headers.get("content-length") || 0);
  if (advertisedLength && advertisedLength !== manifest.sizeBytes) {
    throw new Error("新版安装包大小与发布记录不一致");
  }

  await fs.mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.download`;
  const hash = createHash("sha256");
  let written = 0;
  const input = Readable.fromWeb(response.body);
  const verifier = new Transform({
    transform(chunk, _encoding, callback) {
      written += chunk.length;
      if (written > MAX_INSTALLER_BYTES || written > manifest.sizeBytes) {
        callback(new Error("新版安装包超过发布大小"));
        return;
      }
      hash.update(chunk);
      onProgress({
        downloadedBytes: written,
        totalBytes: manifest.sizeBytes,
        percent: (written / manifest.sizeBytes) * 100,
      });
      callback(null, chunk);
    },
  });
  try {
    onProgress({ downloadedBytes: 0, totalBytes: manifest.sizeBytes, percent: 0 });
    await fs.rm(temporary, { force: true });
    await pipeline(input, verifier, createWriteStream(temporary, { flags: "wx" }));
    if (written !== manifest.sizeBytes || hash.digest("hex") !== manifest.sha256) {
      throw new Error("新版安装包校验失败，已停止安装");
    }
    await fs.rm(destination, { force: true });
    await fs.rename(temporary, destination);
    onProgress({
      downloadedBytes: manifest.sizeBytes,
      totalBytes: manifest.sizeBytes,
      percent: 100,
    });
    return destination;
  } catch (error) {
    await fs.rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

module.exports = {
  buildUpdateProgressHtml,
  compareVersions,
  downloadInstaller,
  fetchManifest,
  validateManifest,
  validateReleaseConfig,
};
