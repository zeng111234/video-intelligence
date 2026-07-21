/**
 * 帮助中心页面
 * 提供使用指南、常见问题、联系方式
 */
import { useState } from "react";
import { Typography, Card, Collapse, Space, Tag, Input, Row, Col, Button } from "antd";
import {
  QuestionCircleOutlined,
  BookOutlined,
  MessageOutlined,
  MailOutlined,
  SearchOutlined,
  ThunderboltOutlined,
  VideoCameraOutlined,
  AudioOutlined,
  RocketOutlined,
  SettingOutlined,
} from "@ant-design/icons";

const { Title, Text, Paragraph, Link } = Typography;
const { Panel } = Collapse;

/** 常见问题数据 */
const FAQ_DATA = [
  {
    category: "基础使用",
    icon: <BookOutlined />,
    items: [
      {
        q: "如何开始使用系统？",
        a: "首先在「候选检索」页面搜索感兴趣的视频，然后可以使用「批量生产」功能生成类似内容，或使用「语音转写」功能提取视频文案。",
      },
      {
        q: "如何批量生产短视频？",
        a: "进入「批量生产」页面，输入关键词（如：二手车、美食），设置生产数量和风格，点击「开始批量生产」即可。",
      },
      {
        q: "如何使用语音转写功能？",
        a: "支持两种方式：1) 粘贴视频直链（MP4格式）；2) 上传本地视频文件。系统会自动提取音频并转写为文字。",
      },
    ],
  },
  {
    category: "数字人生成",
    icon: <VideoCameraOutlined />,
    items: [
      {
        q: "数字人视频如何生成？",
        a: "进入「数字人生成」页面，输入文案内容，选择音色，点击「开始生成」。系统会自动将文字转为语音，并生成口播视频。",
      },
      {
        q: "支持哪些音色？",
        a: "目前支持5种音色：甜美女声、磁性男声、活力青年、专业播音、亲切客服。后续会增加更多音色。",
      },
      {
        q: "生成的视频可以下载吗？",
        a: "可以。视频生成完成后，点击「下载视频」按钮即可保存到本地。",
      },
    ],
  },
  {
    category: "Pro 功能",
    icon: <ThunderboltOutlined />,
    items: [
      {
        q: "Pro 版本有哪些额外功能？",
        a: "Pro 版本包含：深度分析（趋势分析、竞品分析）、AI文案生成（智能改写、多风格）、以及更高的使用额度。",
      },
      {
        q: "如何升级到 Pro？",
        a: "点击侧边栏底部的「升级到 Pro」卡片，或联系客服获取优惠价格。",
      },
    ],
  },
  {
    category: "技术问题",
    icon: <SettingOutlined />,
    items: [
      {
        q: "语音转写显示「演示模式」怎么办？",
        a: "需要安装 faster-whisper 和 FFmpeg，并设置环境变量 ASR_MODE=local。详见后端 ASR_SETUP.md 文档。",
      },
      {
        q: "视频上传失败怎么办？",
        a: "请检查：1) 文件格式是否为 MP4/MOV；2) 文件大小是否超过 50MB；3) 后端服务是否正常运行。",
      },
      {
        q: "API 请求失败怎么办？",
        a: "请确认后端服务已启动（端口 2001）。可以在终端运行 `curl http://localhost:2001/health` 检查服务状态。",
      },
    ],
  },
];

/** 快速入门指南 */
const QUICK_START = [
  {
    step: 1,
    title: "搜索候选视频",
    desc: "在「候选检索」页面输入关键词，找到热门视频素材",
    path: "/candidates",
  },
  {
    step: 2,
    title: "提取视频文案",
    desc: "使用「语音转写」功能，将视频内容转为文字",
    path: "/transcription",
  },
  {
    step: 3,
    title: "批量生产内容",
    desc: "在「批量生产」页面，一键生成多条短视频",
    path: "/pipeline",
  },
  {
    step: 4,
    title: "生成数字人视频",
    desc: "使用「数字人生成」功能，创建口播视频",
    path: "/avatar",
  },
];

export default function HelpPage() {
  const [searchText, setSearchText] = useState("");

  /** 过滤 FAQ */
  const filteredFAQ = FAQ_DATA.map((category) => ({
    ...category,
    items: category.items.filter(
      (item) =>
        !searchText ||
        item.q.toLowerCase().includes(searchText.toLowerCase()) ||
        item.a.toLowerCase().includes(searchText.toLowerCase())
    ),
  })).filter((category) => category.items.length > 0);

  return (
    <div>
      {/* 页面头部 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>
          <QuestionCircleOutlined /> 帮助中心
        </Title>
        <Text type="secondary">快速找到你需要的帮助信息</Text>
      </div>

      {/* 搜索框 */}
      <Card style={{ marginBottom: 24 }}>
        <Input
          size="large"
          placeholder="搜索常见问题..."
          prefix={<SearchOutlined />}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          allowClear
        />
      </Card>

      {/* 快速入门 */}
      <Card title={<Space><RocketOutlined /> 快速入门</Space>} style={{ marginBottom: 24 }}>
        <Row gutter={[16, 16]}>
          {QUICK_START.map((item) => (
            <Col xs={24} sm={12} lg={6} key={item.step}>
              <Card
                size="small"
                hoverable
                onClick={() => (window.location.href = item.path)}
                style={{ height: "100%" }}
              >
                <div style={{ textAlign: "center" }}>
                  <div
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: "50%",
                      background: "var(--primary-500, #6366f1)",
                      color: "white",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 18,
                      fontWeight: 600,
                      margin: "0 auto 12px",
                    }}
                  >
                    {item.step}
                  </div>
                  <Text strong style={{ display: "block", marginBottom: 4 }}>
                    {item.title}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {item.desc}
                  </Text>
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      {/* 常见问题 */}
      <Card title={<Space><QuestionCircleOutlined /> 常见问题</Space>} style={{ marginBottom: 24 }}>
        {filteredFAQ.length > 0 ? (
          <Collapse accordion>
            {filteredFAQ.map((category) => (
              <Panel
                header={
                  <Space>
                    {category.icon}
                    <Text strong>{category.category}</Text>
                    <Tag>{category.items.length}</Tag>
                  </Space>
                }
                key={category.category}
              >
                <Collapse ghost>
                  {category.items.map((item, index) => (
                    <Panel header={item.q} key={`${category.category}-${index}`}>
                      <Paragraph>{item.a}</Paragraph>
                    </Panel>
                  ))}
                </Collapse>
              </Panel>
            ))}
          </Collapse>
        ) : (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Text type="secondary">未找到匹配的问题</Text>
          </div>
        )}
      </Card>

      {/* 联系支持 */}
      <Card title={<Space><MessageOutlined /> 联系支持</Space>}>
        <Row gutter={[24, 24]}>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: "center" }}>
              <MailOutlined style={{ fontSize: 32, color: "#6366f1", marginBottom: 12 }} />
              <Title level={5}>邮件支持</Title>
              <Text type="secondary">发送问题到我们的邮箱</Text>
              <br />
              <Link href="mailto:support@videoinsight.com">support@videoinsight.com</Link>
            </div>
          </Col>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: "center" }}>
              <MessageOutlined style={{ fontSize: 32, color: "#10b981", marginBottom: 12 }} />
              <Title level={5}>在线客服</Title>
              <Text type="secondary">工作日 9:00-18:00</Text>
              <br />
              <Button type="primary" size="small" style={{ marginTop: 8 }}>
                开始对话
              </Button>
            </div>
          </Col>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: "center" }}>
              <BookOutlined style={{ fontSize: 32, color: "#f59e0b", marginBottom: 12 }} />
              <Title level={5}>文档中心</Title>
              <Text type="secondary">查看详细使用文档</Text>
              <br />
              <Button size="small" style={{ marginTop: 8 }}>
                查看文档
              </Button>
            </div>
          </Col>
        </Row>
      </Card>
    </div>
  );
}
