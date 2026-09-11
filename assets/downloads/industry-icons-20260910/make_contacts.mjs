import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
const sharp = createRequire(import.meta.url)('C:/Users/zeng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/sharp');
const base = path.dirname(new URL(import.meta.url).pathname).replace(/^\//, '').replace(/^([A-Za-z]):/, '$1:');
const groups = [
  ['education_exam','中考辅导','real_estate_home','房产家装','food_retail','餐饮零售','medical_health','医疗健康'],
  ['finance_insurance','金融保险','auto_service','汽车维修','beauty_parenting','美妆母婴','legal_corporate','法律企业'],
  ['travel_local','旅游本地服务','recruiting_career','招聘职场','ecommerce_delivery','电商物流']
];
const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
for (let gi=0; gi<groups.length; gi++) {
  const entries=[];
  for (let i=0;i<groups[gi].length;i+=2) {
    const slug=groups[gi][i], cname=groups[gi][i+1];
    const files=(await fs.readdir(path.join(base,slug))).filter(x=>x.endsWith('.svg')).sort();
    for (const file of files) entries.push({slug,cname,file});
  }
  const cols=7, cw=180, ch=175, rows=Math.ceil(entries.length/cols), comps=[];
  for(let i=0;i<entries.length;i++){
    const e=entries[i], x=(i%cols)*cw, y=Math.floor(i/cols)*ch;
    const icon=await sharp(path.join(base,e.slug,'png',e.file.replace('.svg','.png'))).resize(98,98).png().toBuffer();
    const label=Buffer.from(`<svg width="180" height="65" xmlns="http://www.w3.org/2000/svg"><rect width="180" height="65" fill="white"/><text x="90" y="17" text-anchor="middle" font-family="Arial" font-size="11" fill="#222">${esc(e.file.replace('.svg',''))}</text><text x="90" y="38" text-anchor="middle" font-family="Arial" font-size="11" fill="#555">${esc(e.cname)}</text><text x="90" y="56" text-anchor="middle" font-family="Arial" font-size="10" fill="#999">${esc(e.slug)}</text></svg>`);
    comps.push({input:icon,left:x+41,top:y+6},{input:label,left:x,top:y+108});
  }
  await sharp({create:{width:cols*cw,height:rows*ch,channels:3,background:'#eef0f3'}}).composite(comps).jpeg({quality:90}).toFile(path.join(base,`industry-contact-${gi+1}.jpg`));
  console.log(`contact-${gi+1}=${entries.length}`);
}
