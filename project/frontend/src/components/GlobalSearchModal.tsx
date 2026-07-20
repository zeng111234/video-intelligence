/**
 * 全局搜索弹窗
 * Ctrl+K / Cmd+K 触发
 * 支持页面导航、最近访问、快捷操作
 */
import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  SearchOutlined,
  DashboardOutlined,
  ThunderboltOutlined,
  AudioOutlined,
  UnorderedListOutlined,
  LineChartOutlined,
  EditOutlined,
  RocketOutlined,
  SettingOutlined,
  BugOutlined,
} from "@ant-design/icons";
import { getModKey } from "../hooks/useKeyboardShortcuts";

interface SearchItem {
  key: string;
  label: string;
  path: string;
  icon: React.ReactNode;
  group: string;
}

const ALL_ITEMS: SearchItem[] = [
  { key: "dashboard", label: "数据仪表盘", path: "/dashboard", icon: <DashboardOutlined />, group: "核心功能" },
  { key: "candidates", label: "候选检索", path: "/candidates", icon: <SearchOutlined />, group: "核心功能" },
  { key: "pipeline", label: "批量生产", path: "/pipeline", icon: <ThunderboltOutlined />, group: "核心功能" },
  { key: "transcription", label: "语音转写", path: "/transcription", icon: <AudioOutlined />, group: "核心功能" },
  { key: "tasks", label: "任务中心", path: "/tasks", icon: <UnorderedListOutlined />, group: "核心功能" },
  { key: "analytics", label: "深度分析", path: "/analytics", icon: <LineChartOutlined />, group: "高级功能" },
  { key: "ai-copy", label: "AI文案生成", path: "/ai-copy", icon: <EditOutlined />, group: "高级功能" },
  { key: "publish", label: "多平台发布", path: "/publish", icon: <RocketOutlined />, group: "高级功能" },
  { key: "crawler", label: "关键词爬虫", path: "/crawler", icon: <BugOutlined />, group: "高级功能" },
  { key: "admin", label: "系统设置", path: "/admin", icon: <SettingOutlined />, group: "系统" },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function GlobalSearchModal({ open, onClose }: Props) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  // 过滤结果
  const results = useMemo(() => {
    if (!query.trim()) return ALL_ITEMS;
    const q = query.toLowerCase();
    return ALL_ITEMS.filter(
      (item) => item.label.toLowerCase().includes(q) || item.path.includes(q)
    );
  }, [query]);

  // 打开时聚焦
  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  // 键盘导航
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.min(prev + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter" && results[selectedIndex]) {
        navigate(results[selectedIndex].path);
        onClose();
      } else if (e.key === "Escape") {
        onClose();
      }
    },
    [results, selectedIndex, navigate, onClose]
  );

  if (!open) return null;

  // 分组结果
  const groups = results.reduce<Record<string, SearchItem[]>>((acc, item) => {
    (acc[item.group] ||= []).push(item);
    return acc;
  }, {});

  return (
    <>
      {/* 遮罩 */}
      <div className="gs-overlay" onClick={onClose} />
      {/* 弹窗 */}
      <div className="gs-modal" onKeyDown={handleKeyDown}>
        {/* 搜索框 */}
        <div className="gs-input-wrap">
          <SearchOutlined className="gs-input-icon" />
          <input
            ref={inputRef}
            className="gs-input"
            placeholder="搜索页面、功能..."
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
          />
          <span className="gs-shortcut-hint">
            {getModKey()}+K
          </span>
        </div>
        {/* 结果列表 */}
        <div className="gs-results">
          {Object.entries(groups).map(([group, items]) => (
            <div key={group} className="gs-group">
              <div className="gs-group-title">{group}</div>
              {items.map((item) => {
                const idx = results.indexOf(item);
                return (
                  <div
                    key={item.key}
                    className={`gs-item${idx === selectedIndex ? " active" : ""}`}
                    onClick={() => {
                      navigate(item.path);
                      onClose();
                    }}
                    onMouseEnter={() => setSelectedIndex(idx)}
                  >
                    <span className="gs-item-icon">{item.icon}</span>
                    <span className="gs-item-label">{item.label}</span>
                    <span className="gs-item-path">{item.path}</span>
                  </div>
                );
              })}
            </div>
          ))}
          {results.length === 0 && (
            <div className="gs-empty">
              <SearchOutlined style={{ fontSize: 24, color: "var(--gray-300)", marginBottom: 8 }} />
              <div>未找到匹配结果</div>
            </div>
          )}
        </div>
        {/* 底部提示 */}
        <div className="gs-footer">
          <span><kbd>↑↓</kbd> 导航</span>
          <span><kbd>Enter</kbd> 跳转</span>
          <span><kbd>Esc</kbd> 关闭</span>
        </div>
      </div>
      <style>{`
        .gs-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0,0,0,0.5);
          z-index: 9998;
          animation: fadeIn 0.15s ease-out;
        }
        .gs-modal {
          position: fixed;
          top: 20%;
          left: 50%;
          transform: translateX(-50%);
          width: 560px;
          max-width: 90vw;
          max-height: 480px;
          background: var(--bg-card);
          border-radius: var(--radius-lg);
          box-shadow: var(--shadow-xl);
          z-index: 9999;
          display: flex;
          flex-direction: column;
          overflow: hidden;
          animation: gsSlideDown 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .gs-input-wrap {
          display: flex;
          align-items: center;
          padding: 16px 20px;
          border-bottom: 1px solid var(--border-default);
          gap: 12px;
        }
        .gs-input-icon {
          font-size: 18px;
          color: var(--gray-400);
          flex-shrink: 0;
        }
        .gs-input {
          flex: 1;
          border: none;
          outline: none;
          background: transparent;
          font-size: 16px;
          color: var(--text-primary);
          line-height: 1.5;
        }
        .gs-input::placeholder { color: var(--gray-400); }
        .gs-shortcut-hint {
          font-size: 11px;
          color: var(--gray-400);
          background: var(--gray-100);
          padding: 2px 8px;
          border-radius: 4px;
          font-family: monospace;
          flex-shrink: 0;
        }
        .gs-results {
          flex: 1;
          overflow-y: auto;
          padding: 8px;
        }
        .gs-group-title {
          font-size: 11px;
          font-weight: 600;
          color: var(--gray-400);
          text-transform: uppercase;
          letter-spacing: 0.5px;
          padding: 8px 12px 4px;
        }
        .gs-item {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 10px 12px;
          border-radius: var(--radius-sm);
          cursor: pointer;
          transition: background 0.1s;
        }
        .gs-item:hover, .gs-item.active {
          background: var(--primary-50);
        }
        .gs-item-icon {
          width: 32px;
          height: 32px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--gray-100);
          border-radius: var(--radius-sm);
          font-size: 14px;
          color: var(--primary-500);
          flex-shrink: 0;
        }
        .gs-item.active .gs-item-icon {
          background: var(--primary-100);
        }
        .gs-item-label {
          flex: 1;
          font-size: 14px;
          font-weight: 500;
          color: var(--text-primary);
        }
        .gs-item-path {
          font-size: 12px;
          color: var(--gray-400);
          font-family: monospace;
        }
        .gs-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 40px;
          color: var(--gray-400);
          font-size: 14px;
        }
        .gs-footer {
          display: flex;
          gap: 16px;
          padding: 10px 20px;
          border-top: 1px solid var(--border-default);
          font-size: 12px;
          color: var(--gray-400);
        }
        .gs-footer kbd {
          background: var(--gray-100);
          padding: 1px 6px;
          border-radius: 3px;
          font-family: monospace;
          font-size: 11px;
          margin-right: 4px;
        }
        @keyframes gsSlideDown {
          from { opacity: 0; transform: translateX(-50%) translateY(-12px); }
          to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </>
  );
}
