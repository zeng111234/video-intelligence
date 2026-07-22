import { useState, useCallback, useEffect, useRef } from "react";

const STORAGE_PREFIX = "video_app_persistent_";
const DEFAULT_EXPIRY = 7 * 24 * 60 * 60 * 1000;
const DEFAULT_DEBOUNCE = 300;

interface StorageData<T> {
  value: T;
  timestamp: number;
  expiry: number;
}

/**
 * 持久化状态 Hook
 * @param key 存储键名（自动添加前缀）
 * @param initialValue 初始值
 * @param expiry 可选：过期时间（毫秒），默认7天
 * @param debounce 可选：防抖时间（毫秒），默认300ms
 * @returns [value, setValue, clearValue] 类似 useState 的返回值
 */
export function usePersistentState<T>(
  key: string,
  initialValue: T,
  expiry: number = DEFAULT_EXPIRY,
  debounce: number = DEFAULT_DEBOUNCE
): [T, (value: T | ((prev: T) => T)) => void, () => void] {
  const storageKey = `${STORAGE_PREFIX}${key}`;
  const initialValueRef = useRef(initialValue);
  const valueRef = useRef<T>(initialValue);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingWriteRef = useRef(false);
  const clearedRef = useRef(false);

  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored) {
        const data: StorageData<T> = JSON.parse(stored);
        if (Date.now() - data.timestamp < data.expiry) {
          valueRef.current = data.value;
          return data.value;
        }
        localStorage.removeItem(storageKey);
      }
    } catch (error) {
      localStorage.removeItem(storageKey);
      console.warn(`Failed to parse persistent state for key "${key}":`, error);
    }
    valueRef.current = initialValueRef.current;
    return initialValueRef.current;
  });

  const writeNow = useCallback(() => {
    if (clearedRef.current || !pendingWriteRef.current) return;
    try {
      const data: StorageData<T> = {
        value: valueRef.current,
        timestamp: Date.now(),
        expiry,
      };
      localStorage.setItem(storageKey, JSON.stringify(data));
      pendingWriteRef.current = false;
    } catch (error) {
      console.warn(`Failed to save persistent state for key "${key}":`, error);
    }
  }, [expiry, key, storageKey]);

  const scheduleSave = useCallback(() => {
    clearedRef.current = false;
    pendingWriteRef.current = true;
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      writeNow();
      debounceTimerRef.current = null;
    }, debounce);
  }, [debounce, writeNow]);

  const setPersistentValue = useCallback(
    (newValue: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const nextValue = typeof newValue === "function" ? (newValue as (prev: T) => T)(prev) : newValue;
        valueRef.current = nextValue;
        scheduleSave();
        return nextValue;
      });
    },
    [scheduleSave]
  );

  const clearValue = useCallback(() => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = null;
    }
    clearedRef.current = true;
    pendingWriteRef.current = false;
    valueRef.current = initialValueRef.current;
    localStorage.removeItem(storageKey);
    setValue(initialValueRef.current);
  }, [storageKey]);

  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      writeNow();
    };
  }, [writeNow]);

  return [value, setPersistentValue, clearValue];
}

export function clearAllPersistentState(): void {
  const keys = Object.keys(localStorage);
  keys.forEach((key) => {
    if (key.startsWith(STORAGE_PREFIX)) {
      localStorage.removeItem(key);
    }
  });
}

export function getPersistentStateKeys(): string[] {
  const keys = Object.keys(localStorage);
  return keys
    .filter((key) => key.startsWith(STORAGE_PREFIX))
    .map((key) => key.replace(STORAGE_PREFIX, ""));
}
