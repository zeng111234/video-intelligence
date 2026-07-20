/**
 * 快捷键帮助弹窗
 * Ctrl+/ 触发，展示所有可用快捷键
 */
import { CloseOutlined, KeyOutlined } from "@ant-design/icons";
import { getModKey } from "../hooks/useKeyboardShortcuts";

interface Props {
  open: boolean;
  onClose: () => void;
}

const SHORTCUTS = [
  { keys: ["Ctrl", "K"], desc: "打开全局搜索" },
  { keys: ["Ctrl", "/"], desc: "显示快捷键帮助" },
  { keys: ["Esc"], desc: "关闭当前弹窗/抽屉" },
];

export default function ShortcutHelpModal({ open, onClose }: Props) {
  if (!open) return null;
  const mod = getModKey();

  return (
    <>
      <div className="sh-overlay" onClick={onClose} />
      <div className="sh-modal">
        <div className="sh-header">
          <div className="sh-title">
            <KeyOutlined style={{ marginRight: 8 }} />
            键盘快捷键
          </div>
          <button className="sh-close" onClick={onClose}>
            <CloseOutlined />
          </button>
        </div>
        <div className="sh-body">
          {SHORTCUTS.map((s, i) => (
            <div key={i} className="sh-row">
              <span className="sh-desc">{s.desc}</span>
              <span className="sh-keys">
                {s.keys.map((k) => (
                  <kbd key={k} className="sh-kbd">
                    {k === "Ctrl" ? mod : k}
                  </kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
        <div className="sh-footer">
          更多快捷键将在后续版本中开放
        </div>
      </div>
      <style>{`
        .sh-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0,0,0,0.45);
          z-index: 9998;
          animation: fadeIn 0.15s ease-out;
        }
        .sh-modal {
          position: fixed;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          width: 400px;
          max-width: 90vw;
          background: var(--bg-card);
          border-radius: var(--radius-lg);
          box-shadow: var(--shadow-xl);
          z-index: 9999;
          animation: shPopIn 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .sh-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 20px 24px 16px;
          border-bottom: 1px solid var(--border-default);
        }
        .sh-title {
          font-size: 16px;
          font-weight: 600;
          color: var(--text-primary);
          display: flex;
          align-items: center;
        }
        .sh-close {
          background: transparent;
          border: none;
          cursor: pointer;
          color: var(--gray-400);
          font-size: 16px;
          padding: 4px;
          border-radius: var(--radius-sm);
          transition: var(--transition-fast);
        }
        .sh-close:hover {
          background: var(--gray-100);
          color: var(--text-primary);
        }
        .sh-body {
          padding: 16px 24px;
        }
        .sh-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 10px 0;
          border-bottom: 1px solid var(--border-default);
        }
        .sh-row:last-child { border-bottom: none; }
        .sh-desc {
          font-size: 14px;
          color: var(--text-secondary);
        }
        .sh-keys {
          display: flex;
          gap: 4px;
        }
        .sh-kbd {
          background: var(--gray-100);
          border: 1px solid var(--border-default);
          border-radius: 4px;
          padding: 2px 8px;
          font-size: 12px;
          font-family: monospace;
          color: var(--text-primary);
          min-width: 28px;
          text-align: center;
        }
        .sh-footer {
          padding: 12px 24px;
          text-align: center;
          font-size: 12px;
          color: var(--gray-400);
          border-top: 1px solid var(--border-default);
        }
        @keyframes shPopIn {
          from { opacity: 0; transform: translate(-50%, -50%) scale(0.95); }
          to { opacity: 1; transform: translate(-50%, -50%) scale(1); }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </>
  );
}
