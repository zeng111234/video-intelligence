/**
 * 全局键盘快捷键 Hook
 * 提供统一的快捷键注册和管理
 *
 * 内置快捷键：
 *   Ctrl+K / Cmd+K  → 打开全局搜索
 *   Ctrl+/ / Cmd+/  → 显示快捷键帮助
 *   Escape          → 关闭当前弹窗/抽屉
 */
import { useEffect, useState } from "react";

/** 快捷键定义 */
interface Shortcut {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  description: string;
  action: () => void;
}

/** 全局快捷键管理器 */
class ShortcutManager {
  private shortcuts: Map<string, Shortcut> = new Map();

  register(id: string, shortcut: Shortcut) {
    this.shortcuts.set(id, shortcut);
  }

  unregister(id: string) {
    this.shortcuts.delete(id);
  }

  getAll(): Shortcut[] {
    return Array.from(this.shortcuts.values());
  }

  handleKeyDown(e: KeyboardEvent) {
    const isMac = navigator.platform.includes("Mac");
    const modKey = isMac ? e.metaKey : e.ctrlKey;

    for (const shortcut of this.shortcuts.values()) {
      const keyMatch = e.key.toLowerCase() === shortcut.key.toLowerCase();
      const ctrlMatch = shortcut.ctrl ? modKey : !modKey;
      const shiftMatch = shortcut.shift ? e.shiftKey : !e.shiftKey;
      const altMatch = shortcut.alt ? e.altKey : !e.altKey;

      if (keyMatch && ctrlMatch && shiftMatch && altMatch) {
        // 不拦截 input/textarea 内的快捷键（除了全局搜索）
        const target = e.target as HTMLElement;
        const isInput = target.tagName === "INPUT" || target.tagName === "TEXTAREA";
        if (isInput && shortcut.key !== "k") continue;

        e.preventDefault();
        shortcut.action();
        return;
      }
    }
  }
}

const manager = new ShortcutManager();

/** useKeyboardShortcuts - 注册组件级快捷键 */
export function useKeyboardShortcuts(shortcuts: Record<string, Shortcut>) {
  useEffect(() => {
    const entries = Object.entries(shortcuts);
    entries.forEach(([id, shortcut]) => manager.register(id, shortcut));
    return () => {
      entries.forEach(([id]) => manager.unregister(id));
    };
  }, [shortcuts]);
}

/** useGlobalShortcuts - 全局快捷键初始化（应用根组件调用一次） */
export function useGlobalShortcuts() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  // 全局键盘事件监听
  useEffect(() => {
    const handler = (e: KeyboardEvent) => manager.handleKeyDown(e);
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // 注册全局快捷键
  useEffect(() => {
    manager.register("global-search", {
      key: "k",
      ctrl: true,
      description: "全局搜索",
      action: () => setSearchOpen((v) => !v),
    });
    manager.register("global-help", {
      key: "/",
      ctrl: true,
      description: "快捷键帮助",
      action: () => setHelpOpen((v) => !v),
    });
    manager.register("global-escape", {
      key: "Escape",
      description: "关闭弹窗",
      action: () => {
        setSearchOpen(false);
        setHelpOpen(false);
      },
    });

    return () => {
      manager.unregister("global-search");
      manager.unregister("global-help");
      manager.unregister("global-escape");
    };
  }, []);

  return { searchOpen, setSearchOpen, helpOpen, setHelpOpen, shortcuts: manager.getAll() };
}

/** 获取当前平台的修饰键名称 */
export function getModKey(): string {
  return navigator.platform.includes("Mac") ? "⌘" : "Ctrl";
}

export default useKeyboardShortcuts;
