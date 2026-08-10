import { useCallback, useEffect, useState } from "react";
import { Button, Card, InputNumber, Space, Table, Tag, Typography } from "antd";
import { listPricing, updatePricing, type PricingItem } from "../api/client";
import { useToast } from "./Toast";

function pricePrecision(item: PricingItem) {
  const values = [item.default, item.value];
  return Math.max(...values.map((value) => {
    const fraction = value.split(".")[1]?.replace(/0+$/, "") || "";
    return fraction.length;
  }));
}

function priceStep(item: PricingItem) {
  const precision = pricePrecision(item);
  return precision > 0 ? Number(`0.${"0".repeat(precision - 1)}1`) : 1;
}

/** 定价设置：管理员可调全部收费价格，改完即时生效（客户按新价扣费）。 */
export default function PricingSection() {
  const toast = useToast();
  const [items, setItems] = useState<PricingItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState(false);

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await listPricing());
    } catch (err) {
      toast.error((err as Error).message || "加载定价失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void fetchItems();
  }, [fetchItems]);

  const save = async (item: PricingItem) => {
    const value = drafts[item.key] ?? item.value;
    setSavingKey(item.key);
    try {
      const next = await updatePricing(item.key, value);
      toast.success(`${item.label} 已更新为 ${next.value}`);
      setDrafts((current) => ({ ...current, [item.key]: next.value }));
      await fetchItems();
    } catch (err) {
      toast.error((err as Error).message || "保存定价失败");
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <Card
      className="admin-pricing-card"
      title="收费项目"
      extra={(
        <Button size="small" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
          {expanded ? "收起" : "查看收费项目"}
        </Button>
      )}
    >
      {expanded ? (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            只有下列服务会扣积分（1 元 = 1 积分）；改完即时生效。
          </Typography.Text>
          <Table<PricingItem>
            size="small"
            rowKey="key"
            loading={loading}
            dataSource={items}
            pagination={false}
            columns={[
              {
                title: "收费项目",
                dataIndex: "label",
              },
              {
                title: "默认值",
                dataIndex: "default",
                width: 120,
              },
              {
                title: "当前价格",
                width: 180,
                render: (_, item) => (
                  <InputNumber
                    style={{ width: 140 }}
                    min={0}
                    step={priceStep(item)}
                    precision={pricePrecision(item)}
                    value={Number(drafts[item.key] ?? item.value)}
                    onChange={(value) =>
                      setDrafts((current) => ({
                        ...current,
                        [item.key]: value == null ? "" : String(value),
                      }))
                    }
                  />
                ),
              },
              {
                title: "状态",
                dataIndex: "overridden",
                width: 100,
                render: (overridden: boolean) =>
                  overridden ? <Tag color="orange">已调整</Tag> : <Tag>默认</Tag>,
              },
              {
                title: "操作",
                width: 100,
                render: (_, item) => (
                  <Button
                    type="primary"
                    size="small"
                    loading={savingKey === item.key}
                    disabled={savingKey !== null}
                    onClick={() => save(item)}
                  >
                    保存
                  </Button>
                ),
              },
            ]}
          />
        </Space>
      ) : (
        <Typography.Text type="secondary">
          不常改的收费规则已收起；找素材不消耗积分。
        </Typography.Text>
      )}
    </Card>
  );
}
