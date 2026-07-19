import { Layout, Menu, Typography } from "antd";

const { Header, Content } = Layout;

const menuItems = [
  { key: "pipeline", label: "批量生产流水线" },
  { key: "keywords", label: "关键词管理" },
  { key: "tasks", label: "任务中心" },
];

export default function App() {
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <Typography.Title level={4} style={{ color: "white", margin: 0 }}>
          短视频批量生产系统
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          defaultSelectedKeys={["pipeline"]}
          items={menuItems}
          style={{ flex: 1, minWidth: 0 }}
        />
      </Header>
      <Content style={{ padding: 24 }}>
        <Typography.Paragraph>
          已搭建前端骨架，默认端口 1001，后续可对接 FastAPI 后端 2001。
        </Typography.Paragraph>
      </Content>
    </Layout>
  );
}
