import fs from 'node:fs/promises';
import path from 'node:path';

const base = path.dirname(new URL(import.meta.url).pathname).replace(/^\//, '').replace(/^([A-Za-z]):/, '$1:');
const raw = 'https://raw.githubusercontent.com/tabler/tabler-icons/master/icons/outline';
const sets = {
  education_exam: ['中考辅导', 'school:校园学校,book:教材书本,books:书籍资料,backpack:学生书包,pencil:答题笔记,notebook:笔记本,certificate:证书成绩,medal:学习荣誉,math:数学公式,calculator:计算器,ruler:尺规工具,atom:理科实验,language:英语语言,brain:学习思维,target:目标分数,question-mark:疑难问题,bulb:解题思路,clock:学习时间,calendar:考试日期,chart-bar:成绩分析'],
  real_estate_home: ['房产家装', 'home:住宅房屋,building:楼宇建筑,building-community:小区社区,building-skyscraper:高层大楼,key:房屋钥匙,map-pin:房源定位,map:区域地图,dimensions:面积尺寸,sofa:客厅沙发,bath:卫生间,bed:卧室床铺,parking:停车车位,door:入户门,tools:装修工具,paint:油漆装修,ruler-measure:测量尺,shield-check:交易保障'],
  food_retail: ['餐饮零售', 'shopping-cart:购物车,shopping-bag:购物袋,basket:购物篮,tools-kitchen-2:厨房餐具,chef-hat:厨师餐饮,pizza:披萨食品,burger:汉堡食品,cup:饮品杯子,coffee:咖啡饮品,cake:蛋糕甜品,discount:折扣优惠,barcode:商品条码,qrcode:扫码点单,receipt:消费小票,cash:现金收款,credit-card:刷卡支付,building-store:实体门店,tags:商品标签'],
  medical_health: ['医疗健康', 'heartbeat:心率健康,medical-cross:医疗十字,stethoscope:听诊检查,pill:药片用药,first-aid-kit:急救箱,thermometer:体温检测,syringe:注射治疗,vaccine:疫苗接种,bandage:伤口包扎,wheelchair:轮椅服务,accessible:无障碍服务,lungs:肺部呼吸,bone:骨骼健康,dna:基因检测,activity:运动监测,heart:心脏关怀,mood-heart:心理关怀'],
  finance_insurance: ['金融保险', 'coin:硬币金额,coins:多笔资金,cash:现金收入,wallet:钱包账户,credit-card:银行卡支付,chart-line:趋势增长,chart-bar:数据报表,percentage:百分比利率,calculator:金额计算,receipt:交易凭证,report-money:财务报告,shield-check:保险保障,lock:账户安全,alert-circle:风险警示,arrow-up-right:收益增长,currency-yen:人民币金额'],
  auto_service: ['汽车维修', 'car:汽车出行,steering-wheel:方向盘驾驶,engine:发动机维修,tire:轮胎更换,battery:电瓶电池,gas-station:加油充电,tools:维修工具,wrench:扳手保养,screwdriver:螺丝刀维修,road:道路行驶,parking:停车服务,map-pin:门店定位,speedometer:车速仪表,car-garage:汽车车库,motorbike:摩托出行'],
  beauty_parenting: ['美妆母婴', 'mood-smile:美好笑脸,perfume:香水美妆,scissors:剪发造型,mirror:镜子护理,baby-carriage:婴儿推车,baby-bottle:奶瓶喂养,bottle:日用品瓶,wash:清洁护理,heart:关爱呵护,stars:效果亮点,photo:前后对比,flower:花艺美学,sparkles:焕新效果,face-mask:面部护理'],
  legal_corporate: ['法律企业', 'briefcase:商务公文包,scale:公平衡量,gavel:法律裁决,contract:合同协议,file-text:法律文件,signature:签名确认,badge:资质徽章,shield:安全防护,building:企业大楼,users:团队客户,report:企业报告,presentation:方案演示,clipboard-check:审核清单,lock:信息保密,user-check:客户认证'],
  travel_local: ['旅游本地服务', 'plane:飞机出行,train:火车出行,bus:公交出行,map:旅行地图,map-pin:景点定位,hotel:酒店住宿,beach:海滩度假,luggage:旅行行李,camera:旅行拍摄,ticket:门票票券,compass:方向导航,route:路线规划,world:全球旅行,sun:天气阳光,mountain:山地景区,coffee:休闲饮品'],
  recruiting_career: ['招聘职场', 'user-plus:新增人才,users:人才团队,briefcase:职位工作,search:搜索筛选,file-cv:个人简历,interview:面试沟通,certificate:职业证书,presentation:岗位介绍,device-laptop:电脑办公,calendar:面试日期,clipboard-check:入职清单,message:沟通消息,phone:电话联系,mail:邮件通知,timeline:招聘流程,target:岗位目标'],
  ecommerce_delivery: ['电商物流', 'shopping-bag:商品购物袋,package:商品包裹,truck-delivery:配送运输,truck:物流卡车,box:快递箱,barcode:商品条码,qrcode:扫码核销,gift:礼品赠送,heart:收藏喜欢,star:商品评价,message:客服消息,user:买家客户,wallet:支付钱包,discount:优惠折扣,tag:商品标签,receipt:订单小票,chart-line:销售增长']
};

const jobs = [];
for (const [slug, [name, rawItems]] of Object.entries(sets)) {
  const items = rawItems.split(',').map(pair => pair.split(':'));
  for (const [id, label] of items) jobs.push({ slug, name, id, label });
}
let ok = 0, failed = [];
const worker = async job => {
  const enDir = path.join(base, job.slug);
  const cnDir = path.join(base, '中文命名', job.name);
  await fs.mkdir(enDir, { recursive: true });
  await fs.mkdir(cnDir, { recursive: true });
  const enPath = path.join(enDir, `${job.id}.svg`);
  const cnPath = path.join(cnDir, `${job.name}_${job.label}.svg`);
  try {
    const res = await fetch(`${raw}/${job.id}.svg`);
    if (!res.ok) throw new Error(String(res.status));
    const bytes = Buffer.from(await res.arrayBuffer());
    if (bytes.length < 100) throw new Error('too small');
    await fs.writeFile(enPath, bytes);
    await fs.writeFile(cnPath, bytes);
    ok++;
  } catch (e) {
    failed.push(`${job.slug}/${job.id}:${e.message}`);
  }
};
for (let i = 0; i < jobs.length; i += 8) await Promise.all(jobs.slice(i, i + 8).map(worker));
console.log(`downloaded=${ok} failed=${failed.length}`);
if (failed.length) console.log(failed.join('\n'));
