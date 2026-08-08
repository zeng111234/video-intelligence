/**
 * 积分换算工具：1 元 = 1 积分，向上取整到 2 位小数。
 *
 * 与后端 src/services/credits.py 的 cny_to_credits 规则保持一致：
 * 例如 0.082 元 → 0.09 积分；2.25 元 → 2.25 积分；0.00187 元 → 0.01 积分。
 */

import { Modal } from "antd";

/** 人民币金额 → 积分（字符串，避免浮点误差） */
export function cnyToCredits(cny: number | string): string {
  const amount = Number(cny);
  if (!Number.isFinite(amount) || amount <= 0) {
    return "0";
  }
  // 向上取整到 2 位小数：先放大 100 倍向上取整，再除以 100
  const credits = Math.ceil(amount * 100) / 100;
  return credits.toFixed(2);
}

/** 人民币金额 → 积分展示文案，如 "0.09 积分" */
export function cnyToCreditsLabel(cny: number | string | null | undefined): string {
  if (cny === null || cny === undefined) {
    return "费用未知";
  }
  return `${cnyToCredits(cny)} 积分`;
}

/**
 * 统一处理"积分不足"错误：弹出提示并提供"去充值"直达管理页。
 * 返回 true 表示已处理（调用方无需再展示普通错误提示）。
 */
export function handleCreditsError(
  error: unknown,
  onGoRecharge: () => void,
): boolean {
  const messageText = error instanceof Error ? error.message : String(error ?? "");
  if (!messageText.includes("积分不足")) {
    return false;
  }
  Modal.confirm({
    title: "积分不足，请先充值",
    content: messageText || "当前积分不足以完成本次操作，请先在系统管理中充值。",
    okText: "去充值",
    cancelText: "取消",
    onOk: () => {
      onGoRecharge();
    },
  });
  return true;
}
