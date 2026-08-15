import { useCallback, useEffect, useState } from "react";
import { Button, Card, Col, Row, Statistic, Table, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { getAdminCreditUsage } from "../api/client";
import type {
  AdminCreditUsageResponse,
  AdminCreditUsageTransaction,
  CreditUsageGroup,
} from "../api/types";
import { getAdminToken } from "../hooks/useAdminAuth";
import { useToast } from "./Toast";

const { Text } = Typography;

export default function CreditUsageSection() {
  const toast = useToast();
  const [usage, setUsage] = useState<AdminCreditUsageResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    const token = getAdminToken();
    if (!token) return;
    setLoading(true);
    try {
      setUsage(await getAdminCreditUsage(token));
    } catch (error) {
      toast.error((error as Error).message || "加载积分消费记录失败");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const summaryColumns = [
    { title: "名称", dataIndex: "name" },
    {
      title: "已用积分",
      dataIndex: "consumed",
      render: (value: string) => `${value} 积分`,
    },
    { title: "消费次数", dataIndex: "transaction_count", width: 100 },
  ];

  return (
    <Card
      className="admin-credit-usage-card"
      title="积分消费"
      extra={(
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
          刷新
        </Button>
      )}
    >
      <Text type="secondary">
        这里只统计真实扣减流水；充值和赠送不会算作消费。
      </Text>
      <Statistic
        className="admin-credit-total"
        title="累计扣减"
        value={usage?.total_consumed || "0"}
        suffix="积分"
      />
      <Row gutter={[20, 20]}>
        <Col xs={24} lg={12}>
          <Text strong>按功能项目</Text>
          <Table<CreditUsageGroup>
            size="small"
            rowKey="key"
            loading={loading}
            dataSource={usage?.by_project || []}
            columns={summaryColumns}
            pagination={false}
            locale={{ emptyText: "暂无消费" }}
          />
        </Col>
        <Col xs={24} lg={12}>
          <Text strong>按客户</Text>
          <Table<CreditUsageGroup>
            size="small"
            rowKey="key"
            loading={loading}
            dataSource={usage?.by_customer || []}
            columns={summaryColumns}
            pagination={false}
            locale={{ emptyText: "暂无消费" }}
          />
        </Col>
      </Row>
      <details className="admin-credit-details">
        <summary>查看最近消费明细</summary>
        <Table<AdminCreditUsageTransaction>
          size="small"
          rowKey="id"
          dataSource={usage?.recent_transactions || []}
          pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
          columns={[
            { title: "客户", dataIndex: "customer_name" },
            { title: "用途", dataIndex: "reason" },
            {
              title: "扣减",
              dataIndex: "amount",
              render: (value: string) => `${Math.abs(Number(value)).toFixed(2)} 积分`,
            },
            {
              title: "时间",
              dataIndex: "created_at",
              render: (value: string) => value.replace("T", " ").slice(0, 16),
            },
          ]}
          locale={{ emptyText: "暂无消费" }}
        />
      </details>
    </Card>
  );
}
