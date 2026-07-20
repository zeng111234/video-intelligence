/**
 * 深色模式 Context
 * 管理 data-theme 属性 + localStorage 持久化
 */
import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
  type ReactNode,
} from "react";

/** 主题类型 */
export type ThemeMode = "light" | "dark";

/** Context 值类型 */
interface ThemeContextValue {
  /** 当前主题模式 */
  theme: ThemeMode;
  /** 是否深色模式 */
  isDark: boolean;
  /** 切换主题 */
  toggleTheme: () => void;
  /** 设置指定主题 */
  setTheme: (theme: ThemeMode) => void;
}

/** localStorage 键名 */
const THEME_STORAGE_KEY = "videoinsight-theme";

/** 默认值 */
const DEFAULT_THEME: ThemeMode = "light";

/**
 * 读取 localStorage 中保存的主题
 * SSR 安全：在服务端渲染时返回默认值
 */
function getSavedTheme(): ThemeMode {
  if (typeof window === "undefined") return DEFAULT_THEME;
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // localStorage 不可用时静默失败
  }
  return DEFAULT_THEME;
}

/**
 * 将主题应用到 DOM
 * 在 <html> 元素上设置 data-theme 属性
 */
function applyThemeToDOM(theme: ThemeMode): void {
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", theme);
  }
}

/** 创建 Context */
const ThemeContext = createContext<ThemeContextValue | null>(null);

/** Provider Props */
interface ThemeProviderProps {
  children: ReactNode;
  /** 可选：初始主题覆盖 */
  defaultTheme?: ThemeMode;
}

/**
 * ThemeProvider 组件
 * 包裹应用根节点，提供主题状态和切换方法
 */
export function ThemeProvider({
  children,
  defaultTheme,
}: ThemeProviderProps) {
  const [theme, setThemeState] = useState<ThemeMode>(() => {
    return defaultTheme ?? getSavedTheme();
  });

  const isDark = theme === "dark";

  // 主题变化时同步到 DOM 和 localStorage
  useEffect(() => {
    applyThemeToDOM(theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // localStorage 不可用时静默失败
    }
  }, [theme]);

  // 初始化时确保 DOM 一致
  useEffect(() => {
    applyThemeToDOM(getSavedTheme());
  }, []);

  /** 切换主题 */
  const toggleTheme = useCallback(() => {
    setThemeState((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  /** 设置指定主题 */
  const setTheme = useCallback((newTheme: ThemeMode) => {
    setThemeState(newTheme);
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, isDark, toggleTheme, setTheme }),
    [theme, isDark, toggleTheme, setTheme]
  );

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

/**
 * useTheme Hook
 * 获取当前主题状态和切换方法
 * 必须在 ThemeProvider 内部使用
 */
export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme 必须在 ThemeProvider 内部使用");
  }
  return context;
}

export default ThemeContext;
