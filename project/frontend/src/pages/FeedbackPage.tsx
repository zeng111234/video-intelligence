import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Input, InputNumber, Space, Table, Typography } from "antd";
import { LineChartOutlined } from "@ant-design/icons";
import { createPublishFeedback, getFeedbackRecommendations, listPublishFeedback } from "../api/client";
import type { FeedbackRecommendations, PublishFeedback } from "../api/types";
import { useToast } from "../components/Toast";

const { Title, Text } = Typography;

export default function FeedbackPage() {
  const toast = useToast();
  const [items, setItems] = useState<PublishFeedback[]>([]);
  const [recommendations, setRecommendations] = useState<FeedbackRecommendations | null>(null);
  const [taskId, setTaskId] = useState("");
  const [recordedBy, setRecordedBy] = useState("当前操作人");
  const [views, setViews] = useState<number>(0);
  const [likes, setLikes] = useState<number>(0);
  const [comments, setComments] = useState<number>(0);
  const [leads, setLeads] = useState<number>(0);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const refresh = useCallback(async () => {
    const [feedback, review] = await Promise.all([listPublishFeedback(), getFeedbackRecommendations()]);
    setItems(feedback.items); setRecommendations(review);
  }, []);
  useEffect(() => { refresh().catch((error) => toast.error((error as Error).message || "反馈加载失败")); }, [refresh, toast]);
  const submit = async () => {
    if (!taskId.trim() || !recordedBy.trim()) { toast.warning("请填写发布任务 ID 与回填人"); return; }
    setLoading(true);
    try {
      const item = await createPublishFeedback({ publish_task_id: taskId.trim(), views, likes, comments, leads, recorded_by: recordedBy.trim(), note });
      setItems((current) => [item, ...current]); setTaskId(""); setNote(""); await refresh(); toast.success("已保存真实反馈数据");
    } catch (error) { toast.error((error as Error).message || "反馈保存失败"); } finally { setLoading(false); }
  };
  return <Space direction="vertical" size="large" style={{ width: "100%" }}>
    <div><Title level={4} style={{ margin: 0 }}><LineChartOutlined /> 发布反馈与复盘</Title><Text type="secondary">仅接受人工确认已发布、非演示任务的真实数据；建议不替代人工判断。</Text></div>
    <Alert type="info" showIcon message={recommendations?.message || "正在加载建议"} description={(recommendations?.recommendations || []).join(" ")} />
    <Card title="回填已确认作品的数据"><Space wrap style={{ width: "100%" }}>
      <Input value={taskId} onChange={(e) => setTaskId(e.target.value)} placeholder="发布任务 ID（pub-...）" style={{ width: 240 }} />
      <Input value={recordedBy} onChange={(e) => setRecordedBy(e.target.value)} placeholder="回填人" style={{ width: 140 }} />
      <InputNumber min={0} value={views} onChange={(v) => setViews(Number(v || 0))} placeholder="播放" />
      <InputNumber min={0} value={likes} onChange={(v) => setLikes(Number(v || 0))} placeholder="点赞" />
      <InputNumber min={0} value={comments} onChange={(v) => setComments(Number(v || 0))} placeholder="评论" />
      <InputNumber min={0} value={leads} onChange={(v) => setLeads(Number(v || 0))} placeholder="线索" />
      <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选）" style={{ width: 240 }} />
      <Button type="primary" loading={loading} onClick={submit}>保存反馈</Button>
    </Space></Card>
    <Card title={`已确认反馈（${items.length}）`}><Table rowKey="feedback_id" pagination={{ pageSize: 10 }} dataSource={items} columns={[
      { title: "发布任务", dataIndex: "publish_task_id" }, { title: "平台", dataIndex: "platform" }, { title: "播放", dataIndex: "views" }, { title: "互动", render: (_, item) => item.likes + item.comments }, { title: "线索", dataIndex: "leads" }, { title: "回填时间", dataIndex: "recorded_at", render: (v) => new Date(v).toLocaleString("zh-CN") },
    ]} /></Card>
  </Space>;
}
