import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const sharp = createRequire(import.meta.url)('C:/Users/zeng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');

const base = path.dirname(new URL(import.meta.url).pathname).replace(/^\//, '').replace(/^([A-Za-z]):/, '$1:');
const dirs = (await fs.readdir(base, { withFileTypes: true })).filter(x => x.isDirectory() && x.name !== '中文命名');
let count = 0;
for (const dir of dirs) {
  const src = path.join(base, dir.name);
  const out = path.join(src, 'png');
  await fs.mkdir(out, { recursive: true });
  const files = (await fs.readdir(src)).filter(x => x.endsWith('.svg'));
  await Promise.all(files.map(async file => {
    await sharp(path.join(src, file)).resize(256, 256).png().toFile(path.join(out, file.replace('.svg', '.png')));
    count++;
  }));
}
console.log(`rendered=${count}`);
