/**
 * 骨架屏加载组件
 * 提供 Card、Table、Statistic 等常用骨架屏样式
 * 复用 variables.css 中的 .skeleton 动画
 */

interface SkeletonProps {
  /** 宽度，默认 100% */
  width?: number | string;
  /** 高度，默认 16px */
  height?: number | string;
  /** 圆角，默认 radius-sm */
  radius?: string;
  /** 是否为圆形 */
  circle?: boolean;
}

/** 基础骨架条 */
export function SkeletonLine({ width = "100%", height = 16, radius = "var(--radius-sm)", circle }: SkeletonProps) {
  return (
    <div
      className="skeleton"
      style={{
        width: circle ? height : width,
        height: typeof height === "number" ? `${height}px` : height,
        borderRadius: circle ? "50%" : radius,
        flexShrink: 0,
      }}
    />
  );
}

/** 文本骨架（多行） */
export function SkeletonText({ lines = 3, width = "100%" }: { lines?: number; width?: number | string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <SkeletonLine
          key={i}
          width={i === lines - 1 ? "60%" : width}
          height={14}
        />
      ))}
    </div>
  );
}

/** 卡片骨架 */
export function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div
      style={{
        background: "var(--bg-card)",
        borderRadius: "var(--radius-md)",
        padding: 24,
        border: "1px solid var(--border-default)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <SkeletonLine circle height={40} />
        <div style={{ flex: 1 }}>
          <SkeletonLine width="40%" height={16} />
          <div style={{ marginTop: 8 }}>
            <SkeletonLine width="25%" height={12} />
          </div>
        </div>
      </div>
      <SkeletonText lines={rows} />
    </div>
  );
}

/** 统计卡片骨架 */
export function SkeletonStat() {
  return (
    <div
      style={{
        background: "var(--bg-card)",
        borderRadius: "var(--radius-md)",
        padding: 20,
        border: "1px solid var(--border-default)",
      }}
    >
      <SkeletonLine width="50%" height={12} />
      <div style={{ marginTop: 12 }}>
        <SkeletonLine width="70%" height={28} />
      </div>
    </div>
  );
}

/** 表格骨架 */
export function SkeletonTable({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div style={{ overflow: "hidden" }}>
      {/* 表头 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: 16,
          padding: "12px 16px",
          background: "var(--gray-50)",
          borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
        }}
      >
        {Array.from({ length: cols }).map((_, i) => (
          <SkeletonLine key={i} width={`${60 + Math.random() * 30}%`} height={14} />
        ))}
      </div>
      {/* 表体 */}
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div
          key={rowIdx}
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, 1fr)`,
            gap: 16,
            padding: "14px 16px",
            borderBottom: "1px solid var(--border-default)",
          }}
        >
          {Array.from({ length: cols }).map((_, colIdx) => (
            <SkeletonLine
              key={colIdx}
              width={`${40 + Math.random() * 50}%`}
              height={14}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/** 页面级骨架（统计卡片 + 表格） */
export function SkeletonPage() {
  return (
    <div>
      {/* 标题骨架 */}
      <div style={{ marginBottom: 24 }}>
        <SkeletonLine width={180} height={24} />
        <div style={{ marginTop: 8 }}>
          <SkeletonLine width={260} height={14} />
        </div>
      </div>
      {/* 统计卡片行 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 16,
          marginBottom: 24,
        }}
      >
        <SkeletonStat />
        <SkeletonStat />
        <SkeletonStat />
        <SkeletonStat />
      </div>
      {/* 表格骨架 */}
      <div
        style={{
          background: "var(--bg-card)",
          borderRadius: "var(--radius-md)",
          padding: 24,
          border: "1px solid var(--border-default)",
        }}
      >
        <SkeletonTable />
      </div>
    </div>
  );
}

export default SkeletonPage;
