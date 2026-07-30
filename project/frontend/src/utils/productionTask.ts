export function productionTaskTitle(name: string) {
  const normalized = name.replace(/\s+/g, " ").trim();
  const prefixMatch = normalized.match(/^(单条创作\s*·\s*)/);
  const prefix = prefixMatch?.[1] || "";
  const withoutPrefix = prefix ? normalized.slice(prefix.length) : normalized;
  const withoutTags = withoutPrefix.split(/[#＃]/, 1)[0].trim();
  const repeated = withoutTags.match(/^(.{4,}?[。！？!?])\s+\1$/);
  return `${prefix}${repeated?.[1] || withoutTags || "未命名任务"}`;
}
