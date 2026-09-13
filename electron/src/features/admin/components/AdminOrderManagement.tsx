import React, { useCallback, useEffect, useState } from 'react';
import { Button, Select, Space, Table, Tag, Typography, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { adminService } from '../services/adminService';
import type { AdminOrderHistoryItem, AdminPlannedOrderItem } from '../types';

const { Title, Text } = Typography;

const SOURCE_LABEL: Record<string, string> = {
    hosted: '策略托管',
    risk: '风控触发',
};

const KIND_LABEL: Record<string, string> = {
    rebalance_job: '调仓任务',
    hosted_task: '托管执行',
    schedule: '下次窗口',
};

const STATUS_COLOR: Record<string, string> = {
    filled: 'green',
    submitted: 'blue',
    pending: 'processing',
    ready: 'blue',
    running: 'processing',
    queued: 'processing',
    scheduled: 'cyan',
    rejected: 'red',
    cancelled: 'default',
    expired: 'orange',
    failed: 'red',
};

const formatTime = (value?: string | null) => (value ? value.replace('T', ' ').slice(0, 19) : '-');

export const AdminOrderManagement: React.FC = () => {
    const [history, setHistory] = useState<AdminOrderHistoryItem[]>([]);
    const [planned, setPlanned] = useState<AdminPlannedOrderItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [mode, setMode] = useState<string | undefined>();
    const [source, setSource] = useState<string | undefined>();

    const loadAll = useCallback(async () => {
        setLoading(true);
        try {
            const [historyRows, plannedRows] = await Promise.all([
                adminService.listAutoOrderHistory({ mode, source, limit: 80 }),
                adminService.listPlannedOrders(),
            ]);
            setHistory(historyRows || []);
            setPlanned(plannedRows || []);
        } catch (error: any) {
            message.error(error?.response?.data?.detail || error?.message || '加载订单失败');
        } finally {
            setLoading(false);
        }
    }, [mode, source]);

    useEffect(() => {
        void loadAll();
    }, [loadAll]);

    const plannedColumns: ColumnsType<AdminPlannedOrderItem> = [
        { title: '计划时间', dataIndex: 'planned_at', width: 180, render: formatTime },
        {
            title: '类型',
            dataIndex: 'kind',
            width: 110,
            render: (value: string) => <Tag>{KIND_LABEL[value] || value}</Tag>,
        },
        { title: '模式', dataIndex: 'mode', width: 110 },
        { title: '用户', dataIndex: 'user_id', width: 100 },
        { title: '策略', dataIndex: 'strategy_id', width: 140, ellipsis: true },
        { title: '阶段', dataIndex: 'phase', width: 80, render: (value?: string | null) => value || '-' },
        {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (value: string) => <Tag color={STATUS_COLOR[value] || 'default'}>{value}</Tag>,
        },
        { title: '交易日', dataIndex: 'trade_date', width: 120 },
        { title: '说明', dataIndex: 'title', ellipsis: true },
    ];

    const historyColumns: ColumnsType<AdminOrderHistoryItem> = [
        { title: '时间', dataIndex: 'created_at', width: 180, render: formatTime },
        {
            title: '来源',
            dataIndex: 'source',
            width: 110,
            render: (value: string) => (
                <Tag color={value === 'risk' ? 'red' : 'blue'}>{SOURCE_LABEL[value] || value}</Tag>
            ),
        },
        { title: '模式', dataIndex: 'mode', width: 110 },
        { title: '用户', dataIndex: 'user_id', width: 90 },
        { title: '标的', dataIndex: 'symbol', width: 110 },
        {
            title: '方向',
            dataIndex: 'side',
            width: 70,
            render: (value: string) => (
                <span className={value === 'SELL' ? 'text-emerald-600' : 'text-rose-600'}>
                    {value === 'SELL' ? '卖' : '买'}
                </span>
            ),
        },
        { title: '数量', dataIndex: 'quantity', width: 80 },
        {
            title: '成交价',
            dataIndex: 'average_price',
            width: 90,
            render: (value?: number | null, row?: AdminOrderHistoryItem) =>
                value ?? row?.price ?? '-',
        },
        {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (value: string) => <Tag color={STATUS_COLOR[value] || 'default'}>{value}</Tag>,
        },
        { title: '策略', dataIndex: 'strategy_id', width: 120, ellipsis: true },
        { title: '备注', dataIndex: 'remarks', ellipsis: true },
    ];

    return (
        <div className="p-6 space-y-6 overflow-auto h-full">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <Title level={4} className="!mb-1">
                        订单管理
                    </Title>
                    <Text type="secondary">
                        展示策略托管 / 风控自动成交历史，以及尚未执行的调仓任务与下次触发窗口。不含手动点选下单。
                    </Text>
                </div>
                <Space>
                    <Select
                        allowClear
                        placeholder="模式"
                        className="w-32"
                        value={mode}
                        onChange={setMode}
                        options={[
                            { label: '模拟盘', value: 'SIMULATION' },
                            { label: '实盘', value: 'REAL' },
                        ]}
                    />
                    <Select
                        allowClear
                        placeholder="来源"
                        className="w-32"
                        value={source}
                        onChange={setSource}
                        options={[
                            { label: '策略托管', value: 'hosted' },
                            { label: '风控触发', value: 'risk' },
                        ]}
                    />
                    <Button icon={<ReloadOutlined />} onClick={() => void loadAll()}>
                        刷新
                    </Button>
                </Space>
            </div>

            <div>
                <Title level={5}>未来计划交易</Title>
                <Table
                    rowKey="id"
                    loading={loading}
                    columns={plannedColumns}
                    dataSource={planned}
                    pagination={false}
                    size="middle"
                />
            </div>

            <div>
                <Title level={5}>历史自动交易</Title>
                <Table
                    rowKey="id"
                    loading={loading}
                    columns={historyColumns}
                    dataSource={history}
                    pagination={{ pageSize: 20 }}
                    size="middle"
                />
            </div>
        </div>
    );
};
