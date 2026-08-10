const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DESKTOP_BLOCKED_SECRET_KEYS,
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
