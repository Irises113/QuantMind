import React from 'react';
import { Card, Divider, Alert, Tag, Space, Typography, Empty, Button, Table, Tooltip as AntTooltip } from 'antd';
import { BarChart, MonitorPlay, Activity, Download, Filter } from 'lucide-react';
import { 
  BarChart as ReBarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  Legend, 
  ResponsiveContainer, 
  ReferenceLine
} from 'recharts';
import dayjs from 'dayjs';
import { clsx } from 'clsx';
import { 
  TrainingResult, 
  TrainingRequestPayload,
  getObjectiveMetricDescription,
  getTargetModeDescription,
} from './trainingUtils';

const { Text } = Typography;

const MARKET_LABELS: Record<string, string> = {
  CN: 'A股',
  HK: '港股',
  US: '美股',
  CRYPTO: '区块链',
  FUTURES: '期货',
};

interface TrainingResultViewProps {
  result: TrainingResult | null;
  resultError: string;
  settingDefaultModel: boolean;
  onSetDefaultModel: () => void;
  onExportConfig: () => void;
  trainingStatus: string;
}

const MetricCard: React.FC<{
  label: string;
  value: string;
  hint?: string;
  centered?: boolean;
  valueClassName?: string;
  hintClassName?: string;
}> = ({ label, value, hint, centered = false, valueClassName, hintClassName }) => (
  <div className={clsx('rounded-2xl border border-slate-200 bg-white p-4 shadow-sm', centered && 'text-center')}>
    <div className={clsx('text-[10px] font-black uppercase tracking-[0.18em] text-slate-400', centered && 'text-center')}>{label}</div>
    <div className={clsx('mt-2 text-lg font-semibold text-slate-900', centered && 'text-center', valueClassName)}>{value}</div>
    {hint ? <div className={clsx('mt-1 text-xs text-slate-500', centered && 'text-center', hintClassName)}>{hint}</div> : null}
  </div>
);

const SectionHeader: React.FC<{ title: string; desc: string; icon?: React.ReactNode }> = ({ title, desc, icon }) => (
  <div className="flex items-start justify-between gap-4">
    <div>
      <div className="flex items-center gap-2">
        {icon}
        <Typography.Title level={4} className="!mb-0 !text-slate-900">
          {title}
        </Typography.Title>
      </div>
      <Typography.Paragraph className="!mb-0 !mt-2 !text-xs !text-slate-500 leading-relaxed">
        {desc}
      </Typography.Paragraph>
    </div>
  </div>
);

interface FactorReportRow {
  name: string;
  ic: number | null;
  icir: number | null;
  ic_positive_rate: number | null;
  n_days: number;
  coverage: number;
  status: string;
  reason: string;
}

/** 因子筛选报告：漏斗 + 每特征 IC/ICIR/覆盖/淘汰原因（train.py select_top_factors 产出）。 */
const FactorSelectionReport: React.FC<{ report: any }> = ({ report }) => {
  const sc: Record<string, number> = report?.stage_counts || {};
  const thr: Record<string, number> = report?.thresholds || {};
  const features: FactorReportRow[] = Array.isArray(report?.features) ? report.features : [];
  const maxVal = Math.max(1, Number(sc.input) || 1);

  const funnel = [
    { label: '输入特征', value: Number(sc.input) || 0, color: 'bg-slate-400' },
    { label: '|IC|/|ICIR| 通过', value: Number(sc.ic_pass) || 0, color: 'bg-indigo-400' },
    { label: '相关性剪枝后', value: Number(sc.corr_pass) || 0, color: 'bg-violet-400' },
    { label: '稳定性通过', value: Number(sc.stable) || 0, color: 'bg-emerald-400' },
    { label: '最终入选', value: Number(sc.selected) || 0, color: 'bg-emerald-500' },
  ];

  const columns = [
    {
      title: '特征',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      align: 'center' as const,
      ellipsis: true,
      render: (v: string, row: FactorReportRow) => (
        <div className="flex items-center justify-center gap-1.5">
          <span className="font-mono text-[11px]">{v}</span>
          {row.status === 'selected' && <Tag className="m-0" color="green">入选</Tag>}
        </div>
      ),
    },
    {
      title: 'IC',
      dataIndex: 'ic',
      key: 'ic',
      width: 70,
      align: 'center' as const,
      render: (v: number | null) => (v == null ? '—' : v.toFixed(4)),
    },
    {
      title: 'ICIR',
      dataIndex: 'icir',
      key: 'icir',
      width: 70,
      align: 'center' as const,
      render: (v: number | null) => (v == null ? '—' : v.toFixed(3)),
    },
    {
      title: 'IC>0占比',
      dataIndex: 'ic_positive_rate',
      key: 'ic_positive_rate',
      width: 80,
      align: 'center' as const,
      render: (v: number | null) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`),
    },
    {
      title: '有效天数',
      dataIndex: 'n_days',
      key: 'n_days',
      width: 70,
      align: 'center' as const,
    },
    {
      title: '训练段覆盖',
      dataIndex: 'coverage',
      key: 'coverage',
      width: 96,
      align: 'center' as const,
      render: (v: number) => {
        const pct = v * 100;
        const cls = v >= 0.99 ? 'text-emerald-600' : v >= 0.9 ? 'text-amber-600' : 'text-red-500 font-semibold';
        return <span className={cls}>{pct.toFixed(1)}%</span>;
      },
    },
    {
      title: '淘汰原因',
      dataIndex: 'reason',
      key: 'reason',
      width: 240,
      align: 'center' as const,
      ellipsis: true,
      render: (v: string, row: FactorReportRow) => (
        <span className={row.status === 'selected' ? 'text-emerald-600' : 'text-slate-500'}>{v}</span>
      ),
    },
  ];

  return (
    <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
      <SectionHeader
        title="因子筛选报告"
        desc="为什么最终是这些特征进入训练：IC/ICIR 初筛 → 相关性剪枝 → 稳定性检验的完整漏斗与逐特征依据。"
        icon={<Filter size={18} className="text-emerald-500" />}
      />
      <Divider className="my-4" />

      {/* 漏斗 */}
      <div className="flex items-end gap-2">
        {funnel.map((s) => (
          <div key={s.label} className="flex flex-1 flex-col items-center gap-1">
            <div className="text-sm font-bold text-slate-800">{s.value}</div>
            <div className={`w-full rounded-t-md ${s.color}`} style={{ height: `${Math.max(10, (s.value / maxVal) * 72)}px` }} />
            <div className="h-8 text-center text-[10px] leading-tight text-slate-500">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
        <span>阈值：top-N ≤ {thr.n_top ?? '—'} · |IC| ≥ {thr.ic_threshold ?? '—'} · |ICIR| ≥ {thr.icir_threshold ?? '—'} · 相关性 &lt; {thr.correlation_threshold ?? '—'}</span>
        {report?.train_rows != null && <span>筛选基于训练段 {Number(report.train_rows).toLocaleString()} 行（不含验证/测试，防泄漏）</span>}
        {report?.method ? <span className="font-mono">{report.method}</span> : null}
      </div>

      {features.length > 0 ? (
        <div className="mt-3 w-full">
          <Table<FactorReportRow>
            size="small"
            rowKey="name"
            columns={columns}
            dataSource={features}
            pagination={{ pageSize: 20, showSizeChanger: false, size: 'small' }}
            scroll={{ y: 380 }}
            title={() => (
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-slate-500">共 {features.length} 个特征 · 入选 {features.filter((f) => f.status === 'selected').length} 个</span>
                <AntTooltip title="训练段覆盖 = 训练窗口内特征非空比例。低于 90% 说明上游数据存在缺失（如 L2 vpin 系 2024Q4-2025Q1 空窗），红色需排查。">
                  <span className="cursor-help text-[11px] text-slate-400">覆盖度说明</span>
                </AntTooltip>
              </div>
            )}
          />
        </div>
      ) : (
        <Empty description="本次训练未产出因子筛选报告" className="mt-4" />
      )}
    </Card>
  );
};

/** WFA 诊断解读：基于 IC 均值/标准差/正窗占比/ICIR 组合判断，输出可读结论 */
const WfaInterpretation: React.FC<{ wfa: any }> = ({ wfa }) => {
  if (!wfa || !wfa.enabled) return null;
  const icMean = Number(wfa.ic_mean);
  const icStd = Number(wfa.ic_std);
  const positiveRate = Number(wfa.positive_rate);
  const icir = Number(wfa.overall_icir);
  const hasIcir = Number.isFinite(icir) && !Number.isNaN(icir);

  const checks: Array<{ label: string; ok: boolean; text: string }> = [];
  if (icMean >= 0.05) checks.push({ label: 'IC 强度', ok: true, text: `IC均值 ${icMean.toFixed(4)} ≥ 0.05，信号强度良好` });
  else if (icMean >= 0) checks.push({ label: 'IC 强度', ok: true, text: `IC均值 ${icMean.toFixed(4)} 为正，信号有效但偏弱（<0.05）` });
  else checks.push({ label: 'IC 强度', ok: false, text: `IC均值 ${icMean.toFixed(4)} 为负，信号方向可能反了` });

  if (icStd <= 0.02) checks.push({ label: 'IC 稳定性', ok: true, text: `标准差 ${icStd.toFixed(4)} ≤ 0.02，各窗口波动小` });
  else checks.push({ label: 'IC 稳定性', ok: false, text: `标准差 ${icStd.toFixed(4)} > 0.02，各窗口波动偏大` });

  if (positiveRate >= 0.75) checks.push({ label: '正窗占比', ok: true, text: `${Math.round(positiveRate * 100)}% 窗口 IC 为正，跨期一致性好` });
  else if (positiveRate >= 0.5) checks.push({ label: '正窗占比', ok: true, text: `${Math.round(positiveRate * 100)}% 窗口为正，存在少数走弱窗口` });
  else checks.push({ label: '正窗占比', ok: false, text: `仅 ${Math.round(positiveRate * 100)}% 窗口为正，多数窗口失效` });

  if (hasIcir) {
    if (Math.abs(icir) >= 0.3) checks.push({ label: 'ICIR', ok: true, text: `ICIR ${icir.toFixed(3)}，收益/波动比合理` });
    else checks.push({ label: 'ICIR', ok: false, text: `ICIR ${icir.toFixed(3)} < 0.3，信号相对波动偏弱` });
  }

  const okCount = checks.filter(c => c.ok).length;

  return (
    <div className="mt-3 rounded-xl bg-slate-50/70 border border-slate-100 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[9px] font-black uppercase tracking-wider text-slate-500">判断解读</div>
        <Text className={clsx('text-[9px] font-black', okCount === checks.length ? 'text-emerald-600' : okCount >= 2 ? 'text-amber-600' : 'text-rose-500')}>
          {okCount}/{checks.length} 项达标
        </Text>
      </div>
      <div className="space-y-1">
        {checks.map((c, i) => (
          <div key={i} className="flex items-start gap-1.5">
            <span className={clsx('mt-0.5 text-[8px] font-black', c.ok ? 'text-emerald-500' : 'text-rose-400')}>{c.ok ? '✓' : '✗'}</span>
            <Text className={clsx('text-[10px] leading-snug', c.ok ? 'text-slate-600' : 'text-rose-500/80')}>
              <span className="font-bold text-slate-500">{c.label}：</span>{c.text}
            </Text>
          </div>
        ))}
      </div>
      <Text className="block mt-2 text-[10px] text-slate-400 leading-relaxed">
        {okCount === checks.length
          ? '整体稳定可用，适合作为选股模型。'
          : okCount >= 2
            ? '多数维度达标，个别窗口波动可接受，建议关注 IC 表现最弱的区间。'
            : '多个维度未达标，建议调整特征/参数后重新训练，或考虑融合其他模型。'}
      </Text>
    </div>
  );
};

export const TrainingResultView: React.FC<TrainingResultViewProps> = ({
  result,
  resultError,
  settingDefaultModel,
  onSetDefaultModel,
  onExportConfig,
  trainingStatus,
}) => {
  if (!result && !resultError) {
    return (
      <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
         <SectionHeader
          title="第五步：结果入库"
          desc="展示训练完成后会进入模型管理页的元数据与产物预览。"
          icon={<BarChart size={18} className="text-indigo-500" />}
        />
        <Divider className="my-4" />
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="先执行训练，再查看结果摘要" />
      </Card>
    );
  }

  // 入模因子：因子筛选报告中 status=selected 的特征
  const selectedFactors: FactorReportRow[] = (
    (result?.metadata?.factor_selection?.features ?? []) as FactorReportRow[]
  ).filter((f) => f.status === 'selected');

  return (
    <div className="space-y-4">
      {/* 第一排：结果入库概览全宽横条（状态 + 注册 + 关键指标 + 产物） */}
      <Card className="rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
        <SectionHeader
          title="第五步：结果入库"
          desc="展示训练完成后会进入模型管理页的元数据与产物预览。"
          icon={<BarChart size={18} className="text-indigo-500" />}
        />
        <Divider className="my-4" />
        {resultError ? (
          <Alert type="error" showIcon className="mb-4 rounded-2xl" message="训练结果异常" description={resultError} />
        ) : null}
        {result ? (
          <div className="space-y-4">
            {result.metrics?.score_direction === 'reversed' && (
              <Alert
                type="warning"
                showIcon
                message="检测到反向模型"
                description="验证集 IC < 0，模型预测方向与实际收益相反（高分=跌，低分=涨）。推理时已自动翻转分数，但建议检查特征选择是否合理或重新训练。"
                className="rounded-2xl border-amber-100 bg-amber-50/70"
              />
            )}
            <Alert
              type="success"
              showIcon
              message={result.summary.status}
              description={result.summary.notes}
              className="rounded-2xl border-emerald-100 bg-emerald-50/70"
            />

            <Card className="rounded-2xl border-slate-200" size="small" title="模型注册与同步状态">
              <div className="flex flex-wrap items-center gap-3">
                <Tag
                  className={clsx(
                    'm-0 rounded-full border-0 px-3 py-1',
                    result.modelRegistration?.status === 'ready'
                      ? 'bg-emerald-50 text-emerald-600'
                      : result.modelRegistration?.status === 'failed'
                        ? 'bg-rose-50 text-rose-600'
                        : 'bg-amber-50 text-amber-600',
                  )}
                >
                  {result.modelRegistration?.status || 'unknown'}
                </Tag>
                {result.metadata.market ? (
                  <Tag className="m-0 rounded-full border-0 bg-blue-50 px-3 py-1 text-blue-600">
                    {MARKET_LABELS[result.metadata.market.toUpperCase()] || result.metadata.market}
                  </Tag>
                ) : null}
                <Text className="text-xs text-slate-600">
                  model_id: {result.modelRegistration?.modelId || result.modelId}
                </Text>
                <Button
                  size="small"
                  type="primary"
                  className="rounded-xl bg-blue-600"
                  loading={settingDefaultModel}
                  disabled={result.modelRegistration?.status !== 'ready'}
                  onClick={onSetDefaultModel}
                >
                  设为默认模型
                </Button>
                <Button
                  size="small"
                  icon={<Download size={14} />}
                  className="rounded-xl"
                  onClick={onExportConfig}
                >
                  导出训练配置
                </Button>
              </div>
            </Card>

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                label="模型标识"
                value={result.modelId}
                hint={result.modelName}
                centered
                valueClassName="text-sm leading-tight break-all"
                hintClassName="text-[10px] leading-tight break-all"
              />
              <MetricCard
                label="T+N"
                value={`T+${result.metadata.target_horizon_days}`}
                hint={result.metadata.target_mode === 'classification' ? '分类目标' : '回归目标'}
                centered
              />
              <MetricCard
                label="提交特征数"
                value={`${result.metadata.requested_feature_count}`}
                hint={`${result.request.selectedFeatures.length} 个提交维度`}
                centered
              />
              <MetricCard
                label="实际入模特征数"
                value={`${result.metadata.feature_count}`}
                hint={result.metadata.feature_categories.join(' / ') || '—'}
                centered
              />
            </div>
            
            <Card className="rounded-2xl border-slate-200" size="small" title="建议落盘文件">
              <div className="flex flex-wrap gap-2">
                {result.artifacts.map((artifact) => (
                  <Tag key={artifact} className="m-0 rounded-full border-0 bg-indigo-50 px-3 py-1 text-indigo-600">
                    {artifact}
                  </Tag>
                ))}
              </div>
            </Card>
          </div>
        ) : null}
      </Card>

      {/* 第二排：元数据预览 + 结果摘要平铺全宽 */}
      {result ? (
      <div className="grid gap-4 md:grid-cols-2">
      <Card className="h-full rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20, height: '100%', display: 'flex', flexDirection: 'column' } }}>
        <div className="mb-3 flex items-center gap-2">
          <BarChart size={15} className="text-indigo-500" />
          <span className="text-sm font-black text-slate-800">模型元数据预览</span>
        </div>
        <div className="space-y-2">
          {([
            { zh: '展示名称', en: 'display_name', value: <Text code className="text-[11px] break-all">{result.metadata.display_name}</Text> },
            { zh: '预测类型', en: 'target_mode', value: <span>{getTargetModeDescription(result.metadata.target_mode)}</span> },
            { zh: '标签公式', en: 'label_formula', value: <Text code className="text-[11px] break-all">{result.metadata.label_formula}</Text> },
            { zh: '时间窗口', en: 'training_window', value: <Text code className="text-[11px] break-all">{result.metadata.training_window}</Text> },
            { zh: '目标函数', en: 'objective_metric', value: <span>{getObjectiveMetricDescription(result.metadata.objective, result.metadata.metric)}</span> },
          ] as { zh: string; en: string; value: React.ReactNode }[]).map((it) => (
            <div key={it.en} className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2">
              <div className="text-[9px] font-bold tracking-wider text-slate-400">{it.zh} · <span className="font-mono">{it.en}</span></div>
              <div className="mt-1 text-[11px] font-semibold leading-relaxed text-slate-800 break-all">{it.value}</div>
            </div>
          ))}
        </div>

        {/* 入模因子：只显示名称，标签流一行多个，填充等高后的剩余区域 */}
        {selectedFactors.length > 0 && (
          <div className="mt-4 flex flex-col">
            <div className="mb-2 flex items-center justify-between text-[9px] font-bold uppercase tracking-wider text-slate-400">
              <span>入模因子</span>
              <span>{selectedFactors.length} 个</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {selectedFactors.map((f) => (
                <span key={f.name} className="rounded-md border border-indigo-100 bg-indigo-50/70 px-2 py-0.5 font-mono text-[10px] font-semibold text-indigo-600">
                  {f.name}
                </span>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card className="h-full rounded-3xl border-slate-200 shadow-sm" styles={{ body: { padding: 20 } }}>
        <div className="mb-3 flex items-center gap-2">
          <MonitorPlay size={15} className="text-indigo-500" />
          <span className="text-sm font-black text-slate-800">结果摘要</span>
          <span className="text-[10px] text-slate-400">给模型管理页与后续回放使用的最小信息集合</span>
        </div>
        {result ? (
          <div className="space-y-4">
            <MetricCard label="结果状态" value={trainingStatus === 'completed' ? '已生成' : '等待完成'} hint={result.completedAt ? dayjs(result.completedAt).format('YYYY-MM-DD HH:mm:ss') : ''} />
            
            {result.metrics && (
              <div className="rounded-2xl border border-slate-200 bg-white p-4">
                <div className="mb-3 flex items-center justify-between">
                  <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">IC 评估图表</div>
                </div>
                <ResponsiveContainer width="100%" height={200}>
                  <ReBarChart
                    data={[
                      { name: '训练集', IC: result.metrics.train.ic, RankIC: result.metrics.train.rank_ic },
                      { name: '验证集', IC: result.metrics.val.ic, RankIC: result.metrics.val.rank_ic },
                      { name: '测试集', IC: result.metrics.test.ic, RankIC: result.metrics.test.rank_ic },
                    ]}
                    barCategoryGap="30%"
                    margin={{ top: 10, right: 10, left: -10, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#64748b' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} tickFormatter={(v: number) => v.toFixed(2)} />
                    <Tooltip
                      contentStyle={{ borderRadius: 12, fontSize: 11 }}
                      formatter={(value: number) => Number(value).toFixed(4)}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <ReferenceLine y={0.05} stroke="#f59e0b" strokeDasharray="5 3" />
                    <ReferenceLine y={0.10} stroke="#10b981" strokeDasharray="5 3" />
                    <Bar dataKey="IC" fill="#6366f1" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="RankIC" fill="#06b6d4" radius={[4, 4, 0, 0]} />
                  </ReBarChart>
                </ResponsiveContainer>
                
                <div className="mt-3 grid grid-cols-3 gap-2">
                  {(['train', 'val', 'test'] as const).map((split, i) => {
                    const labels = ['训练集', '验证集', '测试集'];
                    const seg = result.metrics![split];
                    const icVal = seg.ic;
                    const color = icVal >= 0.10 ? 'text-emerald-600' : icVal >= 0.05 ? 'text-amber-600' : 'text-rose-500';
                    return (
                      <div key={split} className="rounded-xl bg-slate-50 px-3 py-2 text-center">
                        <div className="text-[9px] font-semibold uppercase tracking-wider text-slate-400">{labels[i]}</div>
                        <div className={`mt-0.5 text-sm font-bold ${color}`}>{icVal.toFixed(4)}</div>
                        <div className="text-[9px] text-slate-400">RankIC {seg.rank_ic.toFixed(4)}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {result.wfa?.enabled && result.wfa.windows?.length > 0 && (
              <div className="rounded-2xl border border-violet-200 bg-white p-4">
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Activity size={14} className="text-violet-500" />
                    <div className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-400">
                      WFA 稳定性诊断
                    </div>
                  </div>
                  <Tag
                    className={clsx('m-0 rounded-full border-0 px-2.5 py-0.5', result.wfa.stability === 'stable' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600')}
                  >
                    {result.wfa.stability === 'stable' ? '稳定' : '不稳定'}
                  </Tag>
                </div>

                <div className="mb-4 grid grid-cols-2 gap-2">
                  <div className="rounded-xl bg-slate-50 px-3 py-2 text-center">
                    <div className="text-[9px] font-semibold uppercase tracking-wider text-slate-400">IC 均值</div>
                    <div className={`mt-0.5 text-sm font-bold ${result.wfa.ic_mean >= 0.05 ? 'text-emerald-600' : result.wfa.ic_mean >= 0 ? 'text-amber-600' : 'text-rose-500'}`}>
                      {Number(result.wfa.ic_mean).toFixed(4)}
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 px-3 py-2 text-center">
                    <div className="text-[9px] font-semibold uppercase tracking-wider text-slate-400">IC 标准差</div>
                    <div className={`mt-0.5 text-sm font-bold ${result.wfa.ic_std <= 0.02 ? 'text-emerald-600' : 'text-amber-600'}`}>
                      {Number(result.wfa.ic_std).toFixed(4)}
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 px-3 py-2 text-center">
                    <div className="text-[9px] font-semibold uppercase tracking-wider text-slate-400">ICIR</div>
                    <div className={`mt-0.5 text-sm font-bold ${Number(result.wfa.overall_icir) >= 0.3 ? 'text-emerald-600' : 'text-slate-700'}`}>
                      {Number.isFinite(Number(result.wfa.overall_icir)) ? Number(result.wfa.overall_icir).toFixed(3) : '—'}
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 px-3 py-2 text-center">
                    <div className="text-[9px] font-semibold uppercase tracking-wider text-slate-400">正窗占比</div>
                    <div className="mt-0.5 text-sm font-bold text-slate-700">{Math.round(Number(result.wfa.positive_rate) * 100)}%</div>
                  </div>
                </div>

                <ResponsiveContainer width="100%" height={180}>
                  <ReBarChart
                    data={result.wfa.windows.map(w => ({
                      name: `W${w.window_idx + 1}`,
                      IC: Number(w.ic),
                      RankIC: Number(w.rank_ic),
                    }))}
                    barCategoryGap="25%"
                    margin={{ top: 10, right: 10, left: -10, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                    <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} axisLine={false} tickLine={false} tickFormatter={(v: number) => v.toFixed(2)} />
                    <Tooltip
                      contentStyle={{ borderRadius: 12, fontSize: 11 }}
                      formatter={(value: any, name: string, props: any) => {
                        const formatted = Number(value).toFixed(4);
                        const w = result.wfa?.windows?.[props?.payload?.payloadIndex ?? 0];
                        if (w && name === 'IC') {
                          return [formatted, `${w.val_start} ~ ${w.val_end}`];
                        }
                        return [formatted, name];
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 10 }} />
                    <ReferenceLine y={0} stroke="#cbd5e1" />
                    <Bar dataKey="IC" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                    <Bar dataKey="RankIC" fill="#a78bfa" radius={[4, 4, 0, 0]} />
                  </ReBarChart>
                </ResponsiveContainer>

                <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
                  <span className="font-mono">{result.wfa.windows.length} 个窗口</span>
                  <span>·</span>
                  <span>{result.wfa.strategy === 'rolling' ? '滚动窗口' : '扩张窗口'}</span>
                  <span>·</span>
                  <span>模型: {result.wfa.model_type}</span>
                  <span>·</span>
                  <span>IC 区间 [{Number(result.wfa.ic_min).toFixed(4)}, {Number(result.wfa.ic_max).toFixed(4)}]</span>
                </div>

                {/* 判断解读 */}
                <WfaInterpretation wfa={result.wfa} />
              </div>
            )}

            <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-400">后续动作</div>
              <div className="mt-2 text-sm text-slate-700">
                1. 将 metadata.json 写入模型目录<br/>
                2. 在模型管理页展示 T+N / label_formula<br/>
                3. 将相同口径带入回测中心复用
              </div>
            </div>
          </div>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="训练完成后，这里会展示元数据摘要" />
        )}
      </Card>
      </div>
      ) : null}

      {result?.metadata?.factor_selection ? (
        <FactorSelectionReport report={result.metadata.factor_selection} />
      ) : null}
    </div>
  );
};
