/**
 * AI 文案生成页面 - Pro 功能
 * 基于 AI 自动生成短视频文案、标题、标签
 */
import { useState } from "react";
import {
  Card,
  Input,
  Button,
  Select,
  Space,
  Tag,
  Typography,
  Row,
  Col,
  Divider,
  message,
  Spin,
} from "antd";
import {
  EditOutlined,
  CopyOutlined,
  ThunderboltOutlined,
  TagsOutlined,
  FontSizeOutlined,
  ReloadOutlined,
} from "@ant-design/icons";

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;
const { Option } = Select;

/** 预设模板 */
const TEMPLATES = [
  { label: "二手车测评", value: "二手车测评，突出车况和性价比" },
  { label: "新车对比", value: "两款热门新车全方位对比评测" },
  { label: "用车技巧", value: "分享实用的汽车保养和驾驶技巧" },
  { label: "汽车Vlog", value: "日常用车生活记录，轻松有趣的风格" },
];

/** 生成的文案示例 */
const SAMPLE_RESULTS = [
  {
    title: "🔥 20万以内最值得买的3款二手车，第3款让人心动！",
    content: "二手车市场水太深？别慌！今天给大家盘点3款20万以内性价比超高的二手车。第一款丰田凯美瑞，省油耐用保值率高；第二款本田雅阁，空间大配置丰富；第三款宝马3系，操控一流驾驶乐趣满满。想知道哪款最适合你？看完这个视频你就明白了！",
    tags: ["二手车", "买车攻略", "性价比", "丰田凯美瑞", "本田雅阁", "宝马3系"],
  },
  {
    title: "💡 二手车验车必看的5个细节，学会不再被坑！",
    content: "买二手车最怕遇到事故车、泡水车。今天教大家5个验车绝招：看漆面是否均匀、查螺丝有无拧动痕迹、检查轮胎磨损程度、测试空调制冷效果、查看保养记录。学会这几招，小白也能买到好车！",
    tags: ["验车技巧", "二手车避坑", "买车必看", "汽车知识"],
  },
];

export default function AiCopyPage() {
  const [topic, setTopic] = useState("");
  const [style, setStyle] = useState("engaging");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(SAMPLE_RESULTS);

  /** 模拟 AI 生成 */
  const handleGenerate = () => {
    if (!topic.trim()) {
      message.warning("请输入视频主题");
      return;
    }
    setLoading(true);
    setTimeout(() => {
      setResults(SAMPLE_RESULTS);
      setLoading(false);
      message.success("文案生成完成！");
    }, 1500);
  };

  /** 复制文案 */
  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success("已复制到剪贴板");
  };

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <EditOutlined /> AI 文案生成
        </Title>
        <Text type="secondary">Pro 专属 - AI 驱动的创意文案引擎</Text>
      </div>

      <Row gutter={[24, 24]}>
        {/* 左侧：输入区 */}
        <Col xs={24} lg={10}>
          <Card title="生成设置">
            {/* 视频主题 */}
            <div style={{ marginBottom: 16 }}>
              <Text strong style={{ display: "block", marginBottom: 8 }}>
                视频主题
              </Text>
              <TextArea
                placeholder="描述你的视频内容，例如：二手车测评、新车对比、用车技巧..."
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                rows={3}
              />
            </div>

            {/* 快捷模板 */}
            <div style={{ marginBottom: 16 }}>
              <Text strong style={{ display: "block", marginBottom: 8 }}>
                快捷模板
              </Text>
              <Space wrap>
                {TEMPLATES.map((t) => (
                  <Tag
                    key={t.label}
                    style={{ cursor: "pointer" }}
                    onClick={() => setTopic(t.value)}
                  >
                    {t.label}
                  </Tag>
                ))}
              </Space>
            </div>

            {/* 文案风格 */}
            <div style={{ marginBottom: 16 }}>
              <Text strong style={{ display: "block", marginBottom: 8 }}>
                文案风格
              </Text>
              <Select value={style} onChange={setStyle} style={{ width: "100%" }}>
                <Option value="engaging">吸引眼球</Option>
                <Option value="professional">专业权威</Option>
                <Option value="humorous">幽默风趣</Option>
                <Option value="storytelling">故事叙述</Option>
                <Option value="educational">知识科普</Option>
              </Select>
            </div>

            {/* 生成按钮 */}
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              size="large"
              block
              loading={loading}
              onClick={handleGenerate}
            >
              AI 一键生成
            </Button>
          </Card>
        </Col>

        {/* 右侧：结果区 */}
        <Col xs={24} lg={14}>
          <Spin spinning={loading} tip="AI 正在创作中...">
            {results.map((item, index) => (
              <Card
                key={index}
                style={{ marginBottom: 16 }}
                title={
                  <Space>
                    <FontSizeOutlined />
                    <Text strong>方案 {index + 1}</Text>
                  </Space>
                }
                extra={
                  <Space>
                    <Button
                      type="text"
                      icon={<CopyOutlined />}
                      onClick={() => handleCopy(`${item.title}\n\n${item.content}`)}
                    >
                      复制
                    </Button>
                    <Button type="text" icon={<ReloadOutlined />} onClick={handleGenerate}>
                      换一个
                    </Button>
                  </Space>
                }
              >
                {/* 标题 */}
                <div style={{ marginBottom: 12 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    推荐标题
                  </Text>
                  <Paragraph
                    strong
                    style={{ fontSize: 16, margin: "4px 0 0", lineHeight: 1.6 }}
                  >
                    {item.title}
                  </Paragraph>
                </div>

                <Divider style={{ margin: "12px 0" }} />

                {/* 正文 */}
                <div style={{ marginBottom: 12 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    视频文案
                  </Text>
                  <Paragraph style={{ margin: "4px 0 0", lineHeight: 1.8 }}>
                    {item.content}
                  </Paragraph>
                </div>

                <Divider style={{ margin: "12px 0" }} />

                {/* 标签 */}
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    <TagsOutlined /> 推荐标签
                  </Text>
                  <div style={{ marginTop: 8 }}>
                    {item.tags.map((tag) => (
                      <Tag key={tag} color="blue" style={{ marginBottom: 4 }}>
                        #{tag}
                      </Tag>
                    ))}
                  </div>
                </div>
              </Card>
            ))}
          </Spin>
        </Col>
      </Row>
    </div>
  );
}
