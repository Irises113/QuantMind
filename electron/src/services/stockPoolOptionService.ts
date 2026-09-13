/**
 * 用户态股票池只读服务（供回测 / 训练 / 推理等功能页选择股票池）。
 * 对接 engine 服务 `GET /api/v1/stock-pools/options`，仅读不写。
 */

import axios from 'axios';
import { SERVICE_URLS } from '../config/services';
import { authService } from '../features/auth/services/authService';

export interface StockPoolOption {
  pool_id: string;
  code: string;
  name: string;
  description?: string | null;
  market: string;
  pool_type: string;
  scope: string;
  status: string;
  symbol_count: number;
  checksum?: string | null;
  is_system: boolean;
}

const baseURL = () => `${String(SERVICE_URLS.ENGINE_SERVICE || '').replace(/\/+$/, '')}/api/v1`;

export async function listStockPoolOptions(params?: {
  market?: string;
  pool_type?: string;
  include_system?: boolean;
}): Promise<StockPoolOption[]> {
  const token = authService.getAccessToken();
  const resp = await axios.get(`${baseURL()}/stock-pools/options`, {
    params: {
      market: params?.market,
      pool_type: params?.pool_type,
      include_system: params?.include_system ?? true,
    },
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    timeout: 30000,
  });
  const items = (resp.data?.items || []) as StockPoolOption[];
  return items;
}

export async function getStockPoolMembers(poolId: string): Promise<string[]> {
  const token = authService.getAccessToken();
  const resp = await axios.get(`${baseURL()}/stock-pools/${encodeURIComponent(poolId)}/members`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    timeout: 30000,
  });
  const symbols = (resp.data?.symbols || resp.data?.items || []) as string[];
  return Array.isArray(symbols) ? symbols : [];
}
