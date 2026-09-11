const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  DESKTOP_BLOCKED_SECRET_KEYS,
  resolveBackendRuntimeRoot,
  sanitizeBackendEnvironment,
} = require("./environment.cjs");

test("desktop backend never inherits permanent supplier credentials", () => {
  const environment = {
    PATH: "C:\\Windows\\System32",
    ADMIN_PASSWORD: "must-not-reach-child",
    VIDEOINSIGHT_WORKER_TOKEN: "must-not-reach-child",
    COPYWRITING_API_KEY: "must-not-reach-child",
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: "must-not-reach-child",
    openai_api_key: "case-insensitive-secret",
  };
  const sanitized = sanitizeBackendEnvironment(environment, {
    VIDEOINSIGHT_RUNTIME_ROOT: "C:\\VideoInsight",
  });

  assert.equal(sanitized.PATH, environment.PATH);
  assert.equal(sanitized.VIDEOINSIGHT_RUNTIME_ROOT, "C:\\VideoInsight");
  assert.equal(sanitized.ADMIN_PASSWORD, undefined);
  assert.equal(sanitized.VIDEOINSIGHT_WORKER_TOKEN, undefined);
  assert.equal(sanitized.COPYWRITING_API_KEY, undefined);
  assert.equal(sanitized.ALIBABA_CLOUD_ACCESS_KEY_SECRET, undefined);
  assert.equal(sanitized.openai_api_key, undefined);
  assert.equal(environment.COPYWRITING_API_KEY, "must-not-reach-child");
});

test("blocked names stay aligned with the packaged Python launcher", () => {
  const required = [
    "APP_SECRET_KEY",
    "API_KEY",
    "ADMIN_PASSWORD",
    "POSTGRES_PASSWORD",
    "VIDEOINSIGHT_WORKER_TOKEN",
    "DASHSCOPE_API_KEY",
    "COPYWRITING_API_KEY",
    "OPENAI_API_KEY",
    "AVATAR_API_KEY",
    "AVATAR_SERVICE_TOKEN",
    "BAIDU_XILING_APP_KEY",
    "PUBLISH_DOUYIN_CLIENT_SECRET",
  ];
  for (const key of required) {
    assert.ok(DESKTOP_BLOCKED_SECRET_KEYS.includes(key), key);
  }
});

test("installer-selected local path controls backend data storage", () => {
  const root = resolveBackendRuntimeRoot({
    executablePath: "D:\\Apps\\VideoInsight\\VideoInsight.exe",
    localAppData: "C:\\Users\\demo\\AppData\\Local",
    fallbackUserData: "C:\\fallback",
    existsSync: () => true,
    readFileSync: () => JSON.stringify({ runtimeRoot: "E:\\VideoWork\\VideoInsight-Data" }),
  });
  assert.equal(root, "E:\\VideoWork\\VideoInsight-Data");
});

test("legacy install keeps its existing local data path", () => {
  const root = resolveBackendRuntimeRoot({
    executablePath: "C:\\Apps\\VideoInsight\\VideoInsight.exe",
    localAppData: "C:\\Users\\demo\\AppData\\Local",
    fallbackUserData: "C:\\fallback",
    existsSync: () => false,
    readFileSync: () => "",
  });
  assert.equal(root, "C:\\Users\\demo\\AppData\\Local\\VideoInsight");
});

test("runtime storage rejects drive roots and network shares", () => {
  for (const runtimeRoot of ["D:\\", "\\\\server\\share\\VideoInsight"]) {
    assert.throws(() => resolveBackendRuntimeRoot({
      executablePath: "C:\\Apps\\VideoInsight\\VideoInsight.exe",
      localAppData: "C:\\Users\\demo\\AppData\\Local",
      fallbackUserData: "C:\\fallback",
      existsSync: () => true,
      readFileSync: () => JSON.stringify({ runtimeRoot }),
    }), /安装位置记录无效/);
  }
});

test("desktop main prevents duplicate instances and records backend exits", () => {
  const mainSource = readFileSync(path.join(__dirname, "main.cjs"), "utf8");
  assert.match(mainSource, /requestSingleInstanceLock\(\)/);
  assert.match(mainSource, /backend_spawned/);
  assert.match(mainSource, /backend_exited/);
});

// 分析 main.cjs 的结构时必须先去掉注释：说明里会引用
// "app.whenReady().then(boot)" 这类代码片段，直接 indexOf 会命中注释而不是
// 真正的调用点。这里只处理行注释与块注释，并避开 "://" 以免误伤 URL。
function readMainSourceCode() {
  return readFileSync(path.join(__dirname, "main.cjs"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

test("a second instance registers no startup work at all", () => {
  const mainSource = readMainSourceCode();

  // 0.2.51 的缺陷：拿不到锁时只调用了 app.quit()，但下面的
  // app.whenReady().then(boot) 仍然注册。app.quit() 是异步的，whenReady 照样
  // 触发，于是第二个实例仍然启动并再拉起一个 VideoInsightBackend.exe。
  const guardIndex = mainSource.indexOf("if (hasSingleInstanceLock) {");
  assert.ok(guardIndex > 0, "启动与生命周期注册必须由单实例锁守卫");

  const whenReadyIndex = mainSource.indexOf("app.whenReady()");
  assert.ok(whenReadyIndex > 0, "应当注册 app.whenReady()");
  assert.ok(
    whenReadyIndex > guardIndex,
    "app.whenReady() 必须位于单实例锁守卫之内，否则第二个实例仍会启动后端",
  );

  assert.equal(
    mainSource.split("app.whenReady()").length - 1,
    1,
    "只允许注册一次 app.whenReady()",
  );
  assert.equal(
    mainSource.split("boot().catch(").length - 1,
    1,
    "只允许注册一次 boot()",
  );

  assert.match(
    mainSource,
    /if \(!hasSingleInstanceLock\) \{\s*\n\s*app\.quit\(\);/,
    "拿不到单实例锁时必须调用 app.quit()",
  );

  for (const event of [
    'app.on("second-instance"',
    'app.on("window-all-closed"',
    'app.on("before-quit"',
  ]) {
    const index = mainSource.indexOf(event);
    assert.ok(index > guardIndex, `${event} 必须位于单实例锁守卫之内`);
  }
});

test("desktop main captures backend output instead of leaving pipes unread", () => {
  const mainSource = readMainSourceCode();

  // 未显式传 stdio 时 Node 会建管道却没人读，后端写满缓冲区就会阻塞，
  // 异常信息也全部丢失——0.2.51 两次升级失败查不出底因正是如此。
  assert.match(
    mainSource,
    /stdio:\s*\[\s*"ignore"\s*,\s*"pipe"\s*,\s*"pipe"\s*\]/,
    "后端必须以 ignore/pipe/pipe 启动并接管输出",
  );
  assert.match(mainSource, /pipeBackendOutput\(backendProcess\.stdout/);
  assert.match(mainSource, /pipeBackendOutput\(backendProcess\.stderr/);

  // 落盘前必须脱敏。
  assert.match(mainSource, /function redactForLog\(/, "必须有脱敏函数");
  assert.match(mainSource, /redactForLog\(message\)/, "写日志前必须脱敏");
  assert.match(
    mainSource,
    /authorization\|cookie\|set-cookie/i,
    "必须覆盖鉴权与 Cookie 头",
  );
  assert.match(
    mainSource,
    /password\|passwd\|secret\|token/i,
    "必须覆盖口令与 Token 字段",
  );

  // 异常退出只自动重试一次；正常退出不重试也不弹窗。
  assert.match(mainSource, /let backendRestartAttempted = false;/);
  assert.match(mainSource, /if \(!backendRestartAttempted\)/);
  assert.match(mainSource, /if \(quitting\) return;/, "正常退出时不得重试或弹窗");
  assert.equal(
    mainSource.split("自动重启失败").length - 1,
    1,
    "重试失败只允许弹出一个错误框",
  );
});
