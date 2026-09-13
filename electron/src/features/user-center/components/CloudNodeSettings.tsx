import React, { useState, useEffect, useCallback } from 'react';
import { Button, Spin, Modal, Form, Input, InputNumber, message, Select, Tag, Tooltip, Popconfirm, Empty } from 'antd';
import { Server, Plus, Trash2, Pencil, PlugZap, Activity, Cpu, HardDrive, MemoryStick } from 'lucide-react';
import { adminService } from '../../admin/services/adminService';
import { parseSshSnippet, suggestAutodlNodeId } from '../utils/parseSshSnippet';

interface CloudNodeInfo {
  id: string;
  name?: string;
  host?: string;
  type?: 'local' | 'remote';
  description?: string;
  available?: boolean;
}

interface CloudNodeDetail {
  id: string;
  name?: string;
  host?: string;
  port?: number;
  user?: string;
  work_dir?: string;
  docker_image?: string;
  gpus?: string;
  exec_mode?: string;
  quantdb_dir?: string;
  has_password?: boolean;
  has_key?: boolean;
}

interface NodeStatusData {
  online?: boolean;
  error?: string;
  cpu_cores?: number;
  cpu_load?: number;
  mem_total_mb?: number;
  mem_used_mb?: number;
  disk_total_kb?: number;
  disk_used_kb?: number;
  gpus?: { util: number; mem_used_mb: number; mem_total_mb: number; temp_c: number; name: string }[];
  containers?: { name: string; status: string }[];
  training_active?: boolean;
  gpu_error?: string;
}

interface NodeFormValues {
  id?: string;
  name: string;
  host: string;
  port: number;
  user: string;
  ssh_password?: string;
  ssh_key?: string;
  work_dir: string;
  docker_image?: string;
  gpus: string;
  exec_mode: 'native_python' | 'ssh_docker';
  quantdb_dir: string;
}

const ENV = (import.meta as any).env || {};

const DEFAULT_FORM: NodeFormValues = {
  id: '',
  name: ENV.VITE_AUTODL_DEFAULT_NAME || '',
  host: ENV.VITE_AUTODL_DEFAULT_HOST || '',
  port: ENV.VITE_AUTODL_DEFAULT_PORT ? Number(ENV.VITE_AUTODL_DEFAULT_PORT) : 22,
  user: ENV.VITE_AUTODL_DEFAULT_USER || 'root',
  ssh_password: '',
  ssh_key: '',
  work_dir: ENV.VITE_AUTODL_DEFAULT_WORK_DIR || '/root/workspace',
  docker_image: '',
  gpus: 'all',
  exec_mode: 'native_python',
  quantdb_dir: '/root/autodl-fs/quantdb',
};

export const CloudNodeSettings: React.FC = () => {
  const [nodes, setNodes] = useState<CloudNodeInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [statusMap, setStatusMap] = useState<Record<string, NodeStatusData>>({});
  const [testingId, setTestingId] = useState<string | null>(null);
  const [statusLoadingId, setStatusLoadingId] = useState<string | null>(null);
  const [sshPaste, setSshPaste] = useState('');
  const [idTouched, setIdTouched] = useState(false);
  const [form] = Form.useForm<NodeFormValues>();
  const execMode = Form.useWatch('exec_mode', form);

  const loadNodes = useCallback(async () => {
    setIsLoading(true);
    try {
      const resp = await adminService.listTrainingNodes();
      const remoteNodes = (resp?.nodes || []).filter((n: CloudNodeInfo) => n.type === 'remote');
      setNodes(remoteNodes);
    } catch (error: any) {
      message.error(error.message || '加载节点列表失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNodes();
  }, [loadNodes]);

  const applySshPaste = (raw: string, opts?: { notify?: boolean }) => {
    setSshPaste(raw);
    const parsed = parseSshSnippet(raw);
    if (!parsed.host && !parsed.port && !parsed.user && !parsed.ssh_password && !parsed.ssh_key) {
      return;
    }
    const patch: Partial<NodeFormValues> = {};
    if (parsed.host) patch.host = parsed.host;
    if (parsed.port) patch.port = parsed.port;
    if (parsed.user) patch.user = parsed.user;
    if (parsed.ssh_password) patch.ssh_password = parsed.ssh_password;
    if (parsed.ssh_key) patch.ssh_key = parsed.ssh_key;
    const name = form.getFieldValue('name');
    if (!name && parsed.host) {
      patch.name = parsed.host.split('.')[0] || parsed.host;
    }
    if (!editingId && !idTouched) {
      patch.id = suggestAutodlNodeId(String(name || patch.name || parsed.host || ''));
    }
    form.setFieldsValue(patch);
    if (opts?.notify) {
      message.success('已从粘贴内容解析 SSH 连接信息');
    }
  };

  const openCreate = () => {
    setEditingId(null);
    setIdTouched(false);
    setSshPaste('');
    form.setFieldsValue(DEFAULT_FORM);
    setIsModalOpen(true);
  };

  const openEdit = async (node: CloudNodeInfo) => {
    setEditingId(node.id);
    setIdTouched(true);
    setSshPaste('');
    try {
      const resp = await adminService.getTrainingNodeDetail(node.id);
      if (resp?.success && resp.node) {
        const d: CloudNodeDetail = resp.node;
        form.setFieldsValue({
          id: d.id,
          name: d.name || '',
          host: d.host || '',
          port: d.port || 22,
          user: d.user || 'root',
          ssh_password: '',
          ssh_key: '',
          work_dir: d.work_dir || '/root/workspace',
          docker_image: d.docker_image || '',
          gpus: d.gpus || 'all',
          exec_mode: d.exec_mode === 'ssh_docker' ? 'ssh_docker' : 'native_python',
          quantdb_dir: d.quantdb_dir || '/root/autodl-fs/quantdb',
        });
      } else {
        form.setFieldsValue({ ...DEFAULT_FORM, name: node.name || '', host: node.host || '', id: node.id });
      }
      setIsModalOpen(true);
    } catch (error: any) {
      message.error(error.message || '加载节点详情失败');
    }
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const nodeId = editingId || suggestAutodlNodeId(values.id || values.name || values.host);
      if (!values.ssh_password && !values.ssh_key && !editingId) {
        message.error('请填写 SSH 密码或密钥路径');
        return;
      }
      const payload = {
        id: nodeId,
        name: values.name || nodeId,
        host: values.host,
        port: values.port,
        user: values.user,
        ssh_password: values.ssh_password || undefined,
        ssh_key: values.ssh_key || undefined,
        work_dir: values.work_dir,
        docker_image: values.exec_mode === 'native_python' ? '' : (values.docker_image || ''),
        gpus: values.gpus,
        exec_mode: values.exec_mode,
        quantdb_dir: values.quantdb_dir,
      };
      const resp = await adminService.saveTrainingNode(payload);
      if (resp?.success) {
        message.success(editingId ? '节点已更新' : '节点已创建');
        setIsModalOpen(false);
        await loadNodes();
      } else {
        message.error(resp?.error || '保存失败');
      }
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(error.message || '保存失败');
    }
  };

  const handleDelete = async (nodeId: string) => {
    try {
      const resp = await adminService.deleteTrainingNode(nodeId);
      if (resp?.success) {
        message.success('节点已删除');
        await loadNodes();
      } else {
        message.warning(resp?.success === false ? '节点不存在或删除失败' : '删除失败');
      }
    } catch (error: any) {
      message.error(error.message || '删除失败');
    }
  };

  const handleTest = async (nodeId: string) => {
    setTestingId(nodeId);
    try {
      const resp = await adminService.testTrainingNode(nodeId);
      if (resp?.success && resp.ssh) {
        if (resp.exec_mode === 'native_python' || resp.native_python) {
          message.success(`节点 ${nodeId} SSH 可用（免 Docker）`);
        } else if (resp.docker) {
          message.success(`节点 ${nodeId} SSH 与 Docker 均可用`);
        } else {
          message.warning(`节点 ${nodeId} SSH 可用，但 Docker 不可用（免 Docker 节点可忽略）`);
        }
      } else {
        message.error(resp?.error || '测试连接失败');
      }
    } catch (error: any) {
      message.error(error.message || '测试连接失败');
    } finally {
      setTestingId(null);
    }
  };

  const handleFetchStatus = async (nodeId: string) => {
    setStatusLoadingId(nodeId);
    try {
      const st = await adminService.getTrainingNodeStatus(nodeId);
      setStatusMap({ ...statusMap, [nodeId]: st as NodeStatusData });
    } catch (error: any) {
      message.error(error.message || '获取状态失败');
    } finally {
      setStatusLoadingId(null);
    }
  };

  const renderStatus = (node: CloudNodeInfo) => {
    const st = statusMap[node.id];
    if (!st) {
      return <Tag color="default">未采集</Tag>;
    }
    if (!st.online) {
      return <Tag color="red" style={{ marginRight: 0 }}>离线{st.error ? ` · ${st.error}` : ''}</Tag>;
    }
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Tag color="green" style={{ marginRight: 0 }}>在线</Tag>
        {st.cpu_cores ? <Tag style={{ marginRight: 0 }}><Cpu size={11} className="inline mr-0.5" />{st.cpu_cores} 核</Tag> : null}
        {st.mem_total_mb ? (
          <Tag style={{ marginRight: 0 }}>
            <MemoryStick size={11} className="inline mr-0.5" />
            {(st.mem_used_mb || 0) / 1024}/{st.mem_total_mb / 1024} GB
          </Tag>
        ) : null}
        {st.disk_total_kb ? (
          <Tag style={{ marginRight: 0 }}>
            <HardDrive size={11} className="inline mr-0.5" />
            {((st.disk_used_kb || 0) / 1024 / 1024).toFixed(1)}/{ (st.disk_total_kb / 1024 / 1024).toFixed(1)} GB
          </Tag>
        ) : null}
        {st.gpus && st.gpus.length > 0
          ? st.gpus.map((g, i) => (
              <Tag key={i} color="purple" style={{ marginRight: 0 }} title={g.name}>
                GPU{i} {g.util}% {g.temp_c}°C
              </Tag>
            ))
          : st.gpu_error
            ? <Tag color="orange" style={{ marginRight: 0 }}>{st.gpu_error}</Tag>
            : null}
        {st.training_active ? <Tag color="blue" style={{ marginRight: 0 }}>训练中</Tag> : null}
      </div>
    );
  };

  if (isLoading) {
    return (
      <div className="w-full pt-1">
        <div className="w-full rounded-xl border border-gray-200 bg-white p-8 flex items-center justify-center min-h-[200px]">
          <Spin />
        </div>
      </div>
    );
  }

  return (
    <div className="w-full pt-1 space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <div className="p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 bg-indigo-100 rounded-md">
                <Server className="w-4 h-4 text-indigo-600" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-800">云端节点</h3>
                <p className="text-[11px] text-gray-500">配置 AutoDL 远程 GPU 训练节点（默认免 Docker）</p>
              </div>
            </div>
            <Button type="primary" size="small" icon={<Plus className="w-3.5 h-3.5" />} onClick={openCreate} className="!rounded-[8px]">
              新建节点
            </Button>
          </div>

          {nodes.length === 0 ? (
            <Empty description="暂无云端节点，点击「新建节点」粘贴 SSH 命令即可" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <div className="space-y-3">
              {nodes.map((node) => (
                <div key={node.id} className="rounded-xl border border-gray-200 bg-slate-50/50 p-3.5 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 min-w-0">
                      <Server className="w-4 h-4 text-indigo-500 shrink-0" />
                      <span className="text-sm font-bold text-gray-800 truncate">{node.name}</span>
                      <Tag color="blue" style={{ marginRight: 0 }}>{node.id}</Tag>
                      <Tag style={{ marginRight: 0 }}>{node.host}</Tag>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <Tooltip title="测试连接">
                        <Button
                          size="small"
                          type="text"
                          icon={<PlugZap className="w-3.5 h-3.5" />}
                          loading={testingId === node.id}
                          onClick={() => void handleTest(node.id)}
                        />
                      </Tooltip>
                      <Tooltip title="采集状态">
                        <Button
                          size="small"
                          type="text"
                          icon={<Activity className="w-3.5 h-3.5" />}
                          loading={statusLoadingId === node.id}
                          onClick={() => void handleFetchStatus(node.id)}
                        />
                      </Tooltip>
                      <Tooltip title="编辑">
                        <Button size="small" type="text" icon={<Pencil className="w-3.5 h-3.5" />} onClick={() => void openEdit(node)} />
                      </Tooltip>
                      <Popconfirm
                        title="确认删除此节点？"
                        description="将从配置中移除该 AutoDL 节点"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={() => void handleDelete(node.id)}
                      >
                        <Button size="small" type="text" danger icon={<Trash2 className="w-3.5 h-3.5" />} />
                      </Popconfirm>
                    </div>
                  </div>
                  <div>{renderStatus(node)}</div>
                </div>
              ))}
            </div>
          )}

          <div className="text-[11px] text-gray-400 space-y-0.5 pt-1 border-t border-gray-100">
            <p>• 可直接粘贴 AutoDL 控制台的 ssh 命令（含端口、账号），密码可写在下一行</p>
            <p>• 默认免 Docker：不需要训练镜像。节点配置保存在服务器 training_nodes.yaml</p>
            <p>• SSH 密码只存在服务端，编辑时留空表示保持原值</p>
          </div>
        </div>
      </div>

      <Modal
        title={editingId ? '编辑云端节点' : '新建云端节点'}
        open={isModalOpen}
        onOk={() => void handleSave()}
        onCancel={() => setIsModalOpen(false)}
        okText="保存"
        cancelText="取消"
        width={560}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={DEFAULT_FORM} className="!pt-2">
          <div className="mb-3">
            <div className="text-xs text-gray-500 mb-1">粘贴 SSH 命令（自动解析地址 / 端口 / 用户 / 密码）</div>
            <Input.TextArea
              value={sshPaste}
              rows={3}
              placeholder={'ssh -p 27045 root@connect.bjb2.seetacloud.com\n密码 xxxxxxxx'}
              className="!rounded-[8px]"
              onChange={(e) => applySshPaste(e.target.value)}
              onPaste={(e) => {
                const text = e.clipboardData.getData('text');
                if (text) {
                  window.setTimeout(() => applySshPaste(text, { notify: true }), 0);
                }
              }}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Form.Item name="name" label="节点名称" rules={[{ required: true, message: '请输入节点名称' }]}>
              <Input
                placeholder="如 autodl4090"
                className="!h-8 !rounded-[8px]"
                onChange={(e) => {
                  if (!editingId && !idTouched) {
                    form.setFieldValue('id', suggestAutodlNodeId(e.target.value || form.getFieldValue('host') || ''));
                  }
                }}
              />
            </Form.Item>
            <Form.Item
              name="id"
              label="节点 ID"
              extra="须以 autodl 开头，训练页靠它调度"
              rules={[{ required: !editingId, message: '请填写节点 ID' }]}
            >
              <Input
                disabled={!!editingId}
                placeholder="autodl-4090"
                className="!h-8 !rounded-[8px]"
                onChange={() => setIdTouched(true)}
              />
            </Form.Item>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Form.Item name="host" label="节点地址" rules={[{ required: true, message: '请输入 IP/域名' }]}>
              <Input placeholder="connect.xxx.seetacloud.com" className="!h-8 !rounded-[8px]" />
            </Form.Item>
            <Form.Item name="port" label="SSH 端口" rules={[{ required: true, message: '请输入端口' }]}>
              <InputNumber min={1} max={65535} className="!w-full !h-8 !rounded-[8px]" placeholder="22" />
            </Form.Item>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Form.Item name="user" label="SSH 用户" rules={[{ required: true, message: '请输入用户' }]}>
              <Input placeholder="root" className="!h-8 !rounded-[8px]" />
            </Form.Item>
            <Form.Item name="exec_mode" label="执行模式">
              <Select
                className="[&_.ant-select-selector]:!h-8 [&_.ant-select-selector]:!rounded-[8px] [&_.ant-select-selector]:!items-center"
                options={[
                  { value: 'native_python', label: '免 Docker（AutoDL 推荐）' },
                  { value: 'ssh_docker', label: '远端 Docker 镜像' },
                ]}
              />
            </Form.Item>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Form.Item name="ssh_password" label="SSH 密码" extra={editingId ? '留空表示保持原值' : '可与 ssh 命令一起粘贴'}>
              <Input.Password placeholder="密码或留空" className="!h-8 !rounded-[8px]" />
            </Form.Item>
            <Form.Item name="ssh_key" label="SSH 密钥路径" extra="填主节点容器内路径，一般留空用密码">
              <Input placeholder="可选" className="!h-8 !rounded-[8px]" />
            </Form.Item>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Form.Item name="work_dir" label="远端工作目录">
              <Input placeholder="/root/workspace" className="!h-8 !rounded-[8px]" />
            </Form.Item>
            <Form.Item name="gpus" label="GPU">
              <Select
                className="[&_.ant-select-selector]:!h-8 [&_.ant-select-selector]:!rounded-[8px] [&_.ant-select-selector]:!items-center"
                options={[
                  { value: 'all', label: '全部 GPU' },
                  { value: '0', label: '不使用 GPU(CPU)' },
                  { value: '1', label: '1 块 GPU' },
                  { value: '2', label: '2 块 GPU' },
                ]}
              />
            </Form.Item>
          </div>
          <Form.Item
            name="quantdb_dir"
            label="QuantDB 数据目录"
            extra="AutoDL 数据盘，重启不丢"
          >
            <Input placeholder="/root/autodl-fs/quantdb" className="!h-8 !rounded-[8px]" />
          </Form.Item>
          {execMode === 'ssh_docker' && (
            <Form.Item name="docker_image" label="训练镜像" extra="仅远端 Docker 模式需要">
              <Input placeholder="quantmind-train:latest" className="!h-8 !rounded-[8px]" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
};

export default CloudNodeSettings;
