/**
 * 全局 Toast 提示组件
 * 支持 success / warning / error / info 四种类型
 * 自动消失、可堆叠、支持手动关闭
 */
import { useState, useCallback, useRef, createContext, useContext, type ReactNode } from "react";
import {
  CheckCircleOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  CloseOutlined,
} from "@ant-design/icons";

/** Toast 类型 */
export type ToastType = "success" | "warning" | "error" | "info";

/** 单条 Toast 数据 */
interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
  duration: number;
  visible: boolean;
}

/** Context 方法 */
interface ToastContextValue {
  toast: {
    success: (msg: string, duration?: number) => void;
    warning: (msg: string, duration?: number) => void;
    error: (msg: string, duration?: number) => void;
    info: (msg: string, duration?: number) => void;
  };
}

const ToastContext = createContext<ToastContextValue | null>(null);

/** 类型配置 */
const TOAST_CONFIG: Record<ToastType, { icon: ReactNode; bg: string; border: string; color: string }> = {
  success: {
    icon: <CheckCircleOutlined />,
    bg: "var(--bg-green-light)",
    border: "var(--success)",
    color: "var(--text-green)",
  },
  warning: {
    icon: <WarningOutlined />,
    bg: "var(--bg-orange-light)",
    border: "var(--warning)",
    color: "var(--text-orange)",
  },
  error: {
    icon: <CloseCircleOutlined />,
    bg: "var(--bg-red-light)",
    border: "var(--error)",
    color: "var(--text-red)",
  },
  info: {
    icon: <InfoCircleOutlined />,
    bg: "var(--bg-blue-light)",
    border: "var(--info)",
    color: "var(--text-blue)",
  },
};

/** Provider */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const counterRef = useRef(0);

  const addToast = useCallback((type: ToastType, message: string, duration = 3000) => {
    const id = `toast-${++counterRef.current}`;
    const toast: ToastItem = { id, type, message, duration, visible: true };
    setToasts((prev) => [...prev.slice(-4), toast]); // 最多堆叠 5 条

    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) =>
          prev.map((t) => (t.id === id ? { ...t, visible: false } : t))
        );
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, 300);
      }, duration);
    }
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) =>
      prev.map((t) => (t.id === id ? { ...t, visible: false } : t))
    );
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 300);
  }, []);

  const toast = {
    success: (msg: string, dur?: number) => addToast("success", msg, dur),
    warning: (msg: string, dur?: number) => addToast("warning", msg, dur),
    error: (msg: string, dur?: number) => addToast("error", msg, dur),
    info: (msg: string, dur?: number) => addToast("info", msg, dur),
  };

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      {/* Toast 容器 */}
      <div className="vi-toast-container">
        {toasts.map((t) => {
          const cfg = TOAST_CONFIG[t.type];
          return (
            <div
              key={t.id}
              className={`vi-toast-item${t.visible ? " vi-toast-enter" : " vi-toast-exit"}`}
              style={{
                background: cfg.bg,
                borderLeft: `4px solid ${cfg.border}`,
                color: cfg.color,
              }}
            >
              <span className="vi-toast-icon">{cfg.icon}</span>
              <span className="vi-toast-msg">{t.message}</span>
              <button className="vi-toast-close" onClick={() => removeToast(t.id)}>
                <CloseOutlined />
              </button>
            </div>
          );
        })}
      </div>
      <style>{`
        .vi-toast-container {
          position: fixed;
          top: 80px;
          right: 24px;
          z-index: 9999;
          display: flex;
          flex-direction: column;
          gap: 8px;
          pointer-events: none;
        }
        .vi-toast-item {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 12px 16px;
          border-radius: var(--radius-sm);
          box-shadow: var(--shadow-lg);
          min-width: 280px;
          max-width: 420px;
          font-size: 14px;
          font-weight: 500;
          pointer-events: auto;
          transform: translateX(0);
          opacity: 1;
          transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .vi-toast-enter {
          animation: toastSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .vi-toast-exit {
          opacity: 0;
          transform: translateX(100%);
        }
        .vi-toast-icon { font-size: 18px; flex-shrink: 0; }
        .vi-toast-msg { flex: 1; line-height: 1.5; }
        .vi-toast-close {
          background: transparent;
          border: none;
          cursor: pointer;
          color: inherit;
          opacity: 0.6;
          padding: 2px;
          display: flex;
          align-items: center;
          font-size: 12px;
          transition: opacity 0.15s;
        }
        .vi-toast-close:hover { opacity: 1; }
        @keyframes toastSlideIn {
          from { opacity: 0; transform: translateX(100%); }
          to { opacity: 1; transform: translateX(0); }
        }
        @media (max-width: 768px) {
          .vi-toast-container { right: 16px; left: 16px; }
          .vi-toast-item { min-width: auto; }
        }
      `}</style>
    </ToastContext.Provider>
  );
}

/** useToast Hook */
export function useToast(): ToastContextValue["toast"] {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast 必须在 ToastProvider 内部使用");
  return ctx.toast;
}

export default ToastProvider;
