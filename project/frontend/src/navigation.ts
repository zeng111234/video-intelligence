export type NavigationSection = "核心功能" | "高级工具" | "支持";

export interface NavigationItem {
  id: string;
  label: string;
  path: string;
  section: NavigationSection;
  description: string;
  keywords?: string[];
  disabled?: boolean;
}

export const CORE_NAVIGATION_ITEMS: NavigationItem[] = [
  {
    id: "pipeline",
    label: "智能创作",
    path: "/pipeline",
    section: "核心功能",
    description: "从素材到成片，自动推进到下一次人工确认",
    keywords: ["一键创作", "单条生产", "工作台"],
  },
  {
    id: "production",
    label: "任务队列",
    path: "/production",
    section: "核心功能",
    description: "查看批量任务、状态、审核和安全重试",
    keywords: ["批量生产", "生产批次"],
  },
  {
    id: "publish",
    label: "发布中心",
    path: "/publish",
    section: "核心功能",
    description: "确认成片并选择真实发布或手动发布包",
    keywords: ["多平台发布", "发布任务"],
  },
];

export const ADVANCED_NAVIGATION_ITEMS: NavigationItem[] = [
  {
    id: "crawler",
    label: "专业爬虫",
    path: "/crawler",
    section: "高级工具",
    description: "检索关键词候选并送入智能创作",
    keywords: ["关键词爬虫", "热点"],
  },
  {
    id: "candidates",
    label: "候选素材",
    path: "/candidates",
    section: "高级工具",
    description: "查看和筛选已有候选",
    keywords: ["候选检索", "候选库"],
  },
  {
    id: "transcription",
    label: "转写复核",
    path: "/transcription",
    section: "高级工具",
    description: "提取、校对并确认原始转写",
    keywords: ["语音转写", "字幕识别"],
  },
  {
    id: "ai-copy",
    label: "AI文案",
    path: "/ai-copy",
    section: "高级工具",
    description: "独立生成和修改口播文案",
    keywords: ["AI文案生成", "改写"],
  },
  {
    id: "avatar",
    label: "数字人",
    path: "/avatar",
    section: "高级工具",
    description: "独立创建数字人口播视频",
    keywords: ["数字人生成", "口播"],
  },
  {
    id: "video-editor",
    label: "剪辑成片",
    path: "/video-editor",
    section: "高级工具",
    description: "独立完成视频剪辑与导出",
    keywords: ["AI智能剪辑", "视频剪辑"],
  },
  {
    id: "subtitle",
    label: "字幕工具",
    path: "/subtitle",
    section: "高级工具",
    description: "独立生成和调整字幕",
    keywords: ["字幕生成"],
  },
];

export const SUPPORT_NAVIGATION_ITEMS: NavigationItem[] = [
  {
    id: "help",
    label: "帮助中心",
    path: "/help",
    section: "支持",
    description: "查看客户工作流与常见问题",
  },
  {
    id: "admin",
    label: "系统设置",
    path: "/admin",
    section: "支持",
    description: "查看服务、账号和供应商配置",
  },
];

export const NAVIGATION_ITEMS = [
  ...CORE_NAVIGATION_ITEMS,
  ...ADVANCED_NAVIGATION_ITEMS,
  ...SUPPORT_NAVIGATION_ITEMS,
];

const LEGACY_PAGE_TITLES: Record<string, string> = {
  "/": "智能创作",
  "/studio": "智能创作",
  "/dashboard": "数据概览",
  "/feedback": "反馈与复盘",
  "/tasks": "任务记录",
  "/analytics": "深度分析",
};

export function getNavigationItem(path: string) {
  return NAVIGATION_ITEMS.find((item) => item.path === path);
}

export function getPageTitle(path: string) {
  return getNavigationItem(path)?.label || LEGACY_PAGE_TITLES[path] || "页面";
}
