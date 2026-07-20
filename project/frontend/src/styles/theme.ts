/**
 * Ant Design 主题配置
 * 将原型配色映射到 antd Design Token
 * 参考：https://ant.design/docs/react/customize-theme-cn
 */
import type { ThemeConfig } from "antd";

/**
 * 亮色主题配置
 * 主色调使用原型的蓝紫渐变色系
 */
export const lightTheme: ThemeConfig = {
  token: {
    // 主色 - 使用原型 primary-600
    colorPrimary: "#7c3aed",
    colorPrimaryBg: "#f5f3ff",
    colorPrimaryBgHover: "#ede9fe",
    colorPrimaryBorder: "#ddd6fe",
    colorPrimaryBorderHover: "#c4b5fd",
    colorPrimaryHover: "#6d28d9",
    colorPrimaryActive: "#5b21b6",
    colorPrimaryTextHover: "#6d28d9",
    colorPrimaryText: "#7c3aed",
    colorPrimaryTextActive: "#5b21b6",

    // 成功色
    colorSuccess: "#10b981",
    // 警告色
    colorWarning: "#f59e0b",
    // 错误色
    colorError: "#ef4444",
    // 信息色
    colorInfo: "#7c3aed",

    // 文字颜色
    colorText: "#1e293b",        // gray-800
    colorTextSecondary: "#64748b", // gray-500
    colorTextTertiary: "#94a3b8",  // gray-400
    colorTextQuaternary: "#cbd5e1", // gray-300

    // 背景色
    colorBgContainer: "#ffffff",
    colorBgElevated: "#ffffff",
    colorBgLayout: "#f8fafc",     // gray-50
    colorBgSpotlight: "#f8fafc",
    colorBgMask: "rgba(0, 0, 0, 0.45)",

    // 边框
    colorBorder: "#e2e8f0",       // gray-200
    colorBorderSecondary: "#f1f5f9", // gray-100

    // 圆角
    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 6,
    borderRadiusXS: 4,

    // 字体
    fontFamily:
      "'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    fontSize: 14,
    fontSizeHeading1: 32,
    fontSizeHeading2: 24,
    fontSizeHeading3: 20,
    fontSizeHeading4: 16,
    fontSizeHeading5: 14,

    // 间距
    marginXS: 8,
    marginSM: 12,
    margin: 16,
    marginMD: 20,
    marginLG: 24,
    marginXL: 32,

    paddingXS: 8,
    paddingSM: 12,
    padding: 16,
    paddingMD: 20,
    paddingLG: 24,
    paddingXL: 32,

    // 阴影
    boxShadow:
      "0 1px 2px 0 rgb(0 0 0 / 0.05)",
    boxShadowSecondary:
      "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",

    // 动效
    motionDurationSlow: "0.3s",
    motionDurationMid: "0.2s",
    motionDurationFast: "0.15s",
    motionEaseInOut: "cubic-bezier(0.4, 0, 0.2, 1)",

    // 链接色
    colorLink: "#7c3aed",
    colorLinkHover: "#6d28d9",
    colorLinkActive: "#5b21b6",
  },
  components: {
    Layout: {
      headerBg: "#ffffff",
      siderBg: "#0f172a",        // gray-900
      bodyBg: "#f8fafc",        // gray-50
      headerHeight: 64,
      headerPadding: "0 24px",
    },
    Menu: {
      darkItemBg: "#0f172a",
      darkSubMenuItemBg: "#0f172a",
      darkItemSelectedBg: "#7c3aed",
      darkItemColor: "#cbd5e1",
      darkItemHoverColor: "#ffffff",
      darkItemHoverBg: "rgba(255, 255, 255, 0.1)",
      itemBorderRadius: 8,
      itemMarginInline: 8,
      iconSize: 16,
    },
    Card: {
      borderRadiusLG: 16,
      paddingLG: 24,
    },
    Button: {
      borderRadius: 8,
      controlHeight: 40,
      paddingContentHorizontal: 20,
    },
    Input: {
      borderRadius: 8,
      controlHeight: 40,
      paddingInline: 16,
    },
    Select: {
      borderRadius: 8,
      controlHeight: 40,
    },
    Table: {
      borderRadius: 12,
      headerBg: "#f8fafc",
      headerColor: "#475569",
      rowHoverBg: "#f8fafc",
    },
    Modal: {
      borderRadiusLG: 24,
      paddingLG: 32,
    },
    Tag: {
      borderRadiusSM: 8,
    },
    Badge: {
      dotSize: 8,
    },
  },
};

/**
 * 深色主题配置
 * 基于原型的深色模式变量
 */
export const darkTheme: ThemeConfig = {
  token: {
    ...lightTheme.token,

    // 主色 - 深色模式下使用更亮的色调
    colorPrimary: "#818cf8",
    colorPrimaryBg: "#1e1b4b",
    colorPrimaryBgHover: "#312e81",
    colorPrimaryBorder: "#3730a3",
    colorPrimaryBorderHover: "#4f46e5",
    colorPrimaryHover: "#a5b4fc",
    colorPrimaryActive: "#c7d2fe",
    colorPrimaryTextHover: "#a5b4fc",
    colorPrimaryText: "#818cf8",
    colorPrimaryTextActive: "#c7d2fe",

    // 信息色
    colorInfo: "#818cf8",

    // 文字颜色
    colorText: "#f1f5f9",        // gray-800 (反转)
    colorTextSecondary: "#94a3b8", // gray-500 (反转)
    colorTextTertiary: "#64748b",  // gray-400 (反转)
    colorTextQuaternary: "#475569", // gray-300 (反转)

    // 背景色
    colorBgContainer: "#1e293b",  // gray-100 (反转)
    colorBgElevated: "#1e293b",
    colorBgLayout: "#0f172a",
    colorBgSpotlight: "#1e293b",
    colorBgMask: "rgba(0, 0, 0, 0.65)",

    // 边框
    colorBorder: "#334155",       // gray-200 (反转)
    colorBorderSecondary: "#1e293b", // gray-100 (反转)

    // 链接色
    colorLink: "#818cf8",
    colorLinkHover: "#a5b4fc",
    colorLinkActive: "#c7d2fe",
  },
  components: {
    ...lightTheme.components,
    Layout: {
      ...lightTheme.components?.Layout,
      headerBg: "#1e293b",
      siderBg: "#1e293b",
      bodyBg: "#0f172a",
    },
    Menu: {
      ...lightTheme.components?.Menu,
      darkItemBg: "#1e293b",
      darkSubMenuItemBg: "#1e293b",
      darkItemSelectedBg: "#818cf8",
      darkItemColor: "#cbd5e1",
      darkItemHoverColor: "#ffffff",
      darkItemHoverBg: "#1e1b4b",
    },
    Card: {
      ...lightTheme.components?.Card,
    },
    Table: {
      ...lightTheme.components?.Table,
      headerBg: "#334155",
      headerColor: "#94a3b8",
      rowHoverBg: "#334155",
    },
  },
  algorithm: undefined, // antd 5.x 使用 token 覆盖而非 algorithm
};

/**
 * 获取当前主题配置
 * @param isDark 是否深色模式
 */
export function getThemeConfig(isDark: boolean): ThemeConfig {
  return isDark ? darkTheme : lightTheme;
}
