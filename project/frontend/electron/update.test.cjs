const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const { promises: fs } = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  compareVersions,
  downloadInstaller,
  fetchManifest,
  validateManifest,
  validateReleaseConfig,
} = require("./update.cjs");

test("version comparison only offers newer releases", () => {
  assert.equal(compareVersions("0.2.1", "0.2.0"), 1);
  assert.equal(compareVersions("0.2.0", "0.2.0"), 0);
  assert.equal(compareVersions("0.1.9", "0.2.0"), -1);
});

test("manifest installer name must match its version", () => {
  assert.throws(
    () =>
      validateManifest({
        version: "0.2.1",
        installer: "VideoInsight-0.2.2-Setup.exe",
        sha256: "0".repeat(64),
        size_bytes: 1,
      }),
    /文件名与版本不一致/,
  );
});

test("release configuration requires the company HTTPS origin", () => {
  assert.deepEqual(
    validateReleaseConfig({
      control_plane_url: "https://video-api.company.example",
      current_version: "0.2.0",
    }),
    {
      controlPlaneUrl: "https://video-api.company.example",
      currentVersion: "0.2.0",
    },
  );
  assert.throws(
    () =>
      validateReleaseConfig({
        control_plane_url: "http://video-api.company.example",
        current_version: "0.2.0",
      }),
    /更新服务地址无效/,
  );
  assert.throws(
    () =>
      validateReleaseConfig({
        control_plane_url: "https://video-api.company.example/another-service",
        current_version: "0.2.0",
      }),
    /更新服务地址无效/,
  );
});

test("manifest and installer stay on the trusted update path", async () => {
  const content = Buffer.from("verified installer bytes");
  const manifest = validateManifest({
    version: "0.2.1",
    installer: "VideoInsight-0.2.1-Setup.exe",
    sha256: createHash("sha256").update(content).digest("hex"),
    size_bytes: content.length,
    notes: "修复稳定性问题",
  });
  const captured = [];
  const fakeFetch = async (url) => {
    captured.push(String(url));
    if (String(url).endsWith("latest.json")) {
      return new Response(JSON.stringify({
        version: manifest.version,
        installer: manifest.installer,
        sha256: manifest.sha256,
        size_bytes: manifest.sizeBytes,
        notes: manifest.notes,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    return new Response(content, {
      status: 200,
      headers: { "content-length": String(content.length) },
    });
  };
  const fetched = await fetchManifest("https://video-api.company.example", fakeFetch);
  assert.deepEqual(fetched, manifest);

  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "vi-update-test-"));
  try {
    const destination = path.join(temporaryRoot, manifest.installer);
    await downloadInstaller({
      controlPlaneUrl: "https://video-api.company.example",
      manifest,
      destination,
      fetchImpl: fakeFetch,
    });
    assert.deepEqual(await fs.readFile(destination), content);
    assert.ok(captured.every((url) => url.startsWith("https://video-api.company.example/desktop-updates/")));
  } finally {
    await fs.rm(temporaryRoot, { recursive: true, force: true });
  }
});

test("download rejects a file that does not match the published digest", async () => {
  const content = Buffer.from("tampered");
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "vi-update-test-"));
  const destination = path.join(temporaryRoot, "VideoInsight-0.2.1-Setup.exe");
  try {
    await assert.rejects(
      downloadInstaller({
        controlPlaneUrl: "https://video-api.company.example",
        manifest: {
          version: "0.2.1",
          installer: path.basename(destination),
          sha256: "0".repeat(64),
          sizeBytes: content.length,
          notes: "",
        },
        destination,
        fetchImpl: async () =>
          new Response(content, {
            status: 200,
            headers: { "content-length": String(content.length) },
          }),
      }),
      /校验失败/,
    );
    await assert.rejects(fs.access(destination));
  } finally {
    await fs.rm(temporaryRoot, { recursive: true, force: true });
  }
});
