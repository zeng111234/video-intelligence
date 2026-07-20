import { Layout, Menu, Typography } from "antd";
import {
  SearchOutlined,
  ThunderboltOutlined,
  PlayCircleOutlined,
  UnorderedListOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import {
  BrowserRouter,
  Routes,
  Route,
  useNavigate,
  useLocation,
  Navigate,
} from "react-router-dom";

import CandidatesPage from "./pages/CandidatesPage";
import PipelinePage from "./pages/PipelinePage";
import TranscriptionPage from "./pages/TranscriptionPage";
import TasksPage from "./pages/TasksPage";
import AdminPage from "./pages/AdminPage";
import NotFoundPage from "./pages/NotFoundPage";
import ErrorBoundary from "./components/ErrorBoundary";

const { Header, Content } = Layout;

const menuItems = [
  { key: "/candidates", label: "候选检索", icon: <SearchOutlined /> },
  { key: "/pipeline", label: "批量生产流水线", icon: <ThunderboltOutlined /> },
  { key: "/transcription", label: "转写任务", icon: <PlayCircleOutlined /> },
  { key: "/tasks", label: "任务中心", icon: <UnorderedListOutlined /> },
  { key: "/admin", label: "系统管理", icon: <SettingOutlined /> },
];

function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "0 24px",
        }}
      >
        <Typography.Title level={4} style={{ color: "white", margin: 0, whiteSpace: "nowrap" }}>
          短视频批量生产系统
        </Typography.Title>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ flex: 1, minWidth: 0 }}
        />
      </Header>
      <Content style={{ padding: 24, background: "#f5f5f5" }}>
        <Routes>
          <Route path="/" element={<Navigate to="/candidates" replace />} />
          <Route path="/candidates" element={<CandidatesPage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/transcription" element={<TranscriptionPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Content>
    </Layout>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <AppLayout />
      </ErrorBoundary>
    </BrowserRouter>
  );
}
