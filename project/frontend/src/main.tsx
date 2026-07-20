/**
 * 应用入口文件
 * 注入 ThemeProvider + Ant Design ConfigProvider + 全局样式
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { ThemeProvider, useTheme } from "./contexts/ThemeContext";
import { getThemeConfig } from "./styles/theme";
import App from "./App";

// 导入全局 CSS 变量
import "./styles/variables.css";

/**
 * 带主题感知的 ConfigProvider 包装组件
 * 根据当前主题模式切换 antd 主题配置
 */
function ThemedApp() {
  const { isDark } = useTheme();
  const themeConfig = getThemeConfig(isDark);

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <App />
    </ConfigProvider>
  );
}

/**
 * 根节点渲染
 * 渲染层级：ThemeProvider > ConfigProvider > App
 */
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <ThemedApp />
    </ThemeProvider>
  </React.StrictMode>
);
