const path = require("node:path");

const windowsPath = path.win32;

const DESKTOP_BLOCKED_SECRET_KEYS = Object.freeze([
  "APP_SECRET_KEY",
  "API_KEY",
  "ADMIN_PASSWORD",
  "POSTGRES_PASSWORD",
  "VIDEOINSIGHT_WORKER_TOKEN",
  "DASHSCOPE_API_KEY",
  "ALIYUN_MODEL_STUDIO_WORKSPACE_ID",
  "ALIBABA_CLOUD_ACCESS_KEY_ID",
  "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
  "ALIYUN_ACCESS_KEY_ID",
  "ALIYUN_ACCESS_KEY_SECRET",
  "ALIYUN_ASR_ACCESS_KEY_ID",
  "ALIYUN_ASR_ACCESS_KEY_SECRET",
  "ALIYUN_ASR_APP_KEY",
  "ONEAPI_API_KEY",
  "DOUYIN_CLIENT_KEY",
  "DOUYIN_CLIENT_SECRET",
  "COPYWRITING_API_KEY",
  "OPENAI_API_KEY",
  "AVATAR_API_KEY",
  "SHUYING_AVATAR_API_CODE",
  "AVATAR_SERVICE_TOKEN",
  "BAIDU_XILING_APP_ID",
  "BAIDU_XILING_APP_KEY",
  "PUBLISH_DOUYIN_CLIENT_KEY",
  "PUBLISH_DOUYIN_CLIENT_SECRET",
  "PUBLISH_DOUYIN_ACCESS_TOKEN",
  "PUBLISH_DOUYIN_REFRESH_TOKEN",
  "PUBLISH_KUAISHOU_CLIENT_KEY",
  "PUBLISH_KUAISHOU_CLIENT_SECRET",
  "PUBLISH_KUAISHOU_ACCESS_TOKEN",
  "PUBLISH_WECHAT_CHANNELS_CLIENT_KEY",
  "PUBLISH_WECHAT_CHANNELS_CLIENT_SECRET",
  "PUBLISH_WECHAT_CHANNELS_ACCESS_TOKEN",
  "PUBLISH_XIAOHONGSHU_CLIENT_KEY",
  "PUBLISH_XIAOHONGSHU_CLIENT_SECRET",
  "PUBLISH_XIAOHONGSHU_ACCESS_TOKEN",
]);

function sanitizeBackendEnvironment(environment, overrides = {}) {
  const sanitized = { ...environment, ...overrides };
  const blocked = new Set(DESKTOP_BLOCKED_SECRET_KEYS);
  for (const key of Object.keys(sanitized)) {
    if (blocked.has(key.toUpperCase())) {
      delete sanitized[key];
    }
  }
  return sanitized;
}

function resolveBackendRuntimeRoot({ executablePath, localAppData, fallbackUserData, existsSync, readFileSync }) {
  const configuredPath = windowsPath.join(
    windowsPath.dirname(executablePath),
    "runtime-location.json",
  );
  if (existsSync(configuredPath)) {
    const configured = JSON.parse(readFileSync(configuredPath, "utf8"));
    const runtimeRoot = windowsPath.resolve(String(configured.runtimeRoot || ""));
    const driveRoot = windowsPath.parse(runtimeRoot).root;
    if (
      !windowsPath.isAbsolute(runtimeRoot)
      || !driveRoot
      || runtimeRoot === driveRoot
      || runtimeRoot.startsWith("\\\\")
    ) {
      throw new Error("安装位置记录无效，请重新运行安装包修复。");
    }
    return runtimeRoot;
  }
  return localAppData
    ? windowsPath.join(localAppData, "VideoInsight")
    : fallbackUserData;
}

module.exports = {
  DESKTOP_BLOCKED_SECRET_KEYS,
  resolveBackendRuntimeRoot,
  sanitizeBackendEnvironment,
};
