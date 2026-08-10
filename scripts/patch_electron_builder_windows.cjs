const fs = require("node:fs");
const path = require("node:path");

const frontendRoot = path.resolve(__dirname, "..", "project", "frontend");
const target = path.join(
  frontendRoot,
  "node_modules",
  "app-builder-lib",
  "out",
  "util",
  "electronGet.js",
);

if (!fs.existsSync(target)) {
  process.exit(0);
}

const original = fs.readFileSync(target, "utf8");
if (original.includes("renameElectronDirectoryWithRetry")) {
  process.exit(0);
}

const marker = "        await fs.rename(tmpDir, dir);";
if (!original.includes(marker)) {
  throw new Error("electron-builder 的目录改名位置已变化，请更新 Windows 补丁。");
}

const replacement = `        const renameElectronDirectoryWithRetry = async () => {
            for (let attempt = 1; attempt <= 4; attempt += 1) {
                try {
                    await fs.rename(tmpDir, dir);
                    return;
                }
                catch (error) {
                    if (error.code !== "EPERM" || attempt === 4) {
                        throw error;
                    }
                    await new Promise(resolve => setTimeout(resolve, attempt * 750));
                }
            }
        };
        await renameElectronDirectoryWithRetry();`;

fs.writeFileSync(target, original.replace(marker, replacement), "utf8");
console.log("Applied electron-builder Windows rename retry patch.");
