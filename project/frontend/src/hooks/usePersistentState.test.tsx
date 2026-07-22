// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAllPersistentState, usePersistentState } from "./usePersistentState";

const prefix = "video_app_persistent_";

function storedValue<T>(key: string): T | null {
  const raw = localStorage.getItem(`${prefix}${key}`);
  return raw ? JSON.parse(raw).value : null;
}

describe("usePersistentState", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
  });

  it("restores non-expired values", () => {
    localStorage.setItem(
      `${prefix}draft`,
      JSON.stringify({ value: "saved", timestamp: Date.now(), expiry: 1000 }),
    );

    const { result } = renderHook(() => usePersistentState("draft", "initial"));

    expect(result.current[0]).toBe("saved");
  });

  it("drops expired and malformed values", () => {
    localStorage.setItem(
      `${prefix}expired`,
      JSON.stringify({ value: "old", timestamp: Date.now() - 2000, expiry: 1000 }),
    );
    localStorage.setItem(`${prefix}broken`, "{");

    const expired = renderHook(() => usePersistentState("expired", "initial"));
    const broken = renderHook(() => usePersistentState("broken", "fallback"));

    expect(expired.result.current[0]).toBe("initial");
    expect(broken.result.current[0]).toBe("fallback");
    expect(localStorage.getItem(`${prefix}expired`)).toBeNull();
    expect(localStorage.getItem(`${prefix}broken`)).toBeNull();
  });

  it("debounces and writes the latest value", () => {
    const { result } = renderHook(() => usePersistentState("latest", 0, undefined, 300));

    act(() => {
      result.current[1](1);
      result.current[1](2);
      result.current[1]((prev) => prev + 1);
    });
    act(() => vi.advanceTimersByTime(299));
    expect(localStorage.getItem(`${prefix}latest`)).toBeNull();

    act(() => vi.advanceTimersByTime(1));
    expect(storedValue<number>("latest")).toBe(3);
  });

  it("flushes pending writes on unmount", () => {
    const { result, unmount } = renderHook(() => usePersistentState("unmount", "a", undefined, 500));

    act(() => result.current[1]("b"));
    unmount();

    expect(storedValue<string>("unmount")).toBe("b");
  });

  it("does not re-write after clear cancels a pending timer", () => {
    const { result } = renderHook(() => usePersistentState("clear", "a", undefined, 500));

    act(() => result.current[1]("b"));
    act(() => result.current[2]());
    act(() => vi.advanceTimersByTime(500));

    expect(result.current[0]).toBe("a");
    expect(localStorage.getItem(`${prefix}clear`)).toBeNull();
  });

  it("keeps keys isolated and can clear all persistent keys", () => {
    const first = renderHook(() => usePersistentState("task-1", "a", undefined, 0));
    const second = renderHook(() => usePersistentState("task-2", "b", undefined, 0));

    act(() => {
      first.result.current[1]("draft-a");
      second.result.current[1]("draft-b");
      vi.runOnlyPendingTimers();
    });

    expect(storedValue<string>("task-1")).toBe("draft-a");
    expect(storedValue<string>("task-2")).toBe("draft-b");

    clearAllPersistentState();
    expect(localStorage.getItem(`${prefix}task-1`)).toBeNull();
    expect(localStorage.getItem(`${prefix}task-2`)).toBeNull();
  });
});
