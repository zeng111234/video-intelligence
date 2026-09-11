# 本地口播素材候选库

本批下载：151 个 Kenney 音效（原 OGG + 48kHz 单声道 PCM WAV），48 个 OpenMoji 彩色 SVG 静态贴纸。

打开 preview.html 试听、查看；catalog.json 包含文件位置、来源、许可、时长、校验值。

## 状态

所有音效已成功解码转换；SVG 已解析校验。未完成逐个试听和视觉审美筛选，未接入产品运行时，不代表剪映同款或发布级验收。

## 许可与署名

- Kenney：CC0；两包原始 License.txt 随包保留。
- 贴纸署名：OpenMoji contributors — https://openmoji.org/ — CC BY-SA 4.0。原文件未修改。修改版素材须按同一许可分发；产品接入时保留署名与许可入口。完整许可见 openmoji-LICENSE.txt。

## DS 接入说明

先试听 confirmation / maximize / minimize / drop / question / error 等类别，选择适合口播的短效果。不要将所有 UI switch/click 随机混入视频。
本目录是候选素材，不覆盖 assets/sounds 现有文件。选定后再按产品素材契约接入，保留正确的第三方作者和许可，不标记为项目原创。
贴纸是静态 SVG，入退场动画由现有渲染器实现；需转透明 PNG 时保留矢量原件。
不要统一截断所有音效尾音，不要仅因完成下载而判定成片通过。

## 中文命名与 AI 检索

中文命名/ 内提供全部素材的中文名副本，旧路径和 ID 保留以兼容正在接入的脚本。AI素材索引.json 提供中文用途、描述和使用限制。catalog.json 新增 display_name_zh、tags_zh、named_file 等字段，原 file 字段保持不变。音效用途来自原作者命名，尚未逐个试听；不能据此宣称声画匹配已经验收。
