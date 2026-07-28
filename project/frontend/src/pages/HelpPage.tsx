/**
 * 帮助中心页面
 * 提供使用指南、常见问题、联系方式
 */
import { useState } from "react";
import { Typography, Card, Collapse, Space, Tag, Input, Row, Col, Button } from "antd";
import { useNavigate } from "react-router-dom";
import {
  QuestionCircleOutlined,
  BookOutlined,
  SearchOutlined,
  VideoCameraOutlined,
  RocketOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { getNavigationItem } from "../navigation";

const { Title, Text, Paragraph } = Typography;
const { Panel } = Collapse;
const intelligentCreation = getNavigationItem("/pipeline")!;
const taskQueue = getNavigationItem("/production")!;
const publishCenter = getNavigationItem("/publish")!;
const crawler = getNavigationItem("/crawler")!;

/** 常见问题数据 */
const FAQ_DATA = [
  {
    category: "基础使用",
    icon: <BookOutlined />,
    items: [
      {
        q: "如何开始使用系统？",
        a: `进入「${intelligentCreation.label}」，从关键词、视频链接、选题或已有文案开始。系统会自动推进到下一次人工确认，不会越过转写、文案和发布闸门。`,
      },
      {
        q: `「${intelligentCreation.label}」和「${taskQueue.label}」有什么区别？`,
        a: `「${intelligentCreation.label}」默认处理一条内容并显示当前步骤；「${taskQueue.label}」用于查看批量任务、暂停、审核和安全重试。两者都只展示真实任务与产物。`,
      },
      {
        q: `什么时候使用「${crawler.label}」？`,
        a: `需要查看完整榜单、筛选更多候选或诊断搜索结果时使用「${crawler.label}」。选中候选后直接点击「送入智能创作」，无需复制内部 ID。`,
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
    category: "能力状态",
    icon: <SettingOutlined />,
    items: [
      {
        q: "为什么有些按钮不可用？",
        a: "不可用通常是因为供应商凭证、授权确认、可识别媒体、成稿审批或额度条件不满足。系统会显示阻断原因，不伪造生产成功。",
      },
      {
        q: "Sandbox 和 Production 有什么区别？",
        a: "Sandbox 只用于演示流程，不代表真实平台生产数据。Production 会调用已配置供应商接口，并受本地预算、缓存和幂等保护限制。",
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
    title: intelligentCreation.label,
    desc: intelligentCreation.description,
    path: intelligentCreation.path,
  },
  {
    step: 2,
    title: taskQueue.label,
    desc: taskQueue.description,
    path: taskQueue.path,
  },
  {
    step: 3,
    title: publishCenter.label,
    desc: publishCenter.description,
    path: publishCenter.path,
  },
];

export default function HelpPage() {
  const navigate = useNavigate();
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
            <Col xs={24} sm={12} lg={8} key={item.step}>
              <Card
                size="small"
                hoverable
                onClick={() => navigate(item.path)}
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

      {/* 本地资料 */}
      <Card title={<Space><BookOutlined /> 本地资料</Space>}>
        <Row gutter={[24, 24]}>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: "center" }}>
              <SettingOutlined style={{ fontSize: 32, color: "#6366f1", marginBottom: 12 }} />
              <Title level={5}>系统状态</Title>
              <Text type="secondary">查看后端、数据库和供应商配置</Text>
              <br />
              <Button size="small" style={{ marginTop: 8 }} onClick={() => navigate("/admin")}>
                打开系统设置
              </Button>
            </div>
          </Col>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: "center" }}>
              <RocketOutlined style={{ fontSize: 32, color: "#10b981", marginBottom: 12 }} />
              <Title level={5}>启动说明</Title>
              <Text type="secondary">1001 React 和 2001 FastAPI 是正式入口</Text>
              <br />
              <Text code>README.md</Text>
            </div>
          </Col>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: "center" }}>
              <BookOutlined style={{ fontSize: 32, color: "#f59e0b", marginBottom: 12 }} />
              <Title level={5}>交付文档</Title>
              <Text type="secondary">交接和交付文件保留在本地仓库</Text>
              <br />
              <Text code>交接文档.md</Text>
            </div>
          </Col>
        </Row>
      </Card>
    </div>
  );
}
