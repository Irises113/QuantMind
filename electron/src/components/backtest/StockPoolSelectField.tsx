/**
 * 内联股票池选择控件：展示当前池 + 打开 StockPoolPickerModal。
 */

import React, { useState } from 'react';
import { Layers, X } from 'lucide-react';
import type { StockPoolOption } from '../../services/stockPoolOptionService';
import { StockPoolPickerModal } from './StockPoolPickerModal';

export interface StockPoolSelection {
  poolId: string | null;
  code: string | null;
  name: string | null;
  /** pool:code 形式，供后端 pool_id / strategy_params.pool_id */
  ref: string | null;
}

interface StockPoolSelectFieldProps {
  value: StockPoolSelection | null;
  onChange: (next: StockPoolSelection | null) => void;
  market?: string;
  title?: string;
  compact?: boolean;
  /** 右侧栏：按钮全宽、纵向排列 */
  stacked?: boolean;
  className?: string;
}

export const toPoolRef = (code: string) => `pool:${code}`;

export const selectionFromPool = (pool: StockPoolOption): StockPoolSelection => ({
  poolId: pool.pool_id,
  code: pool.code,
  name: pool.name,
  ref: toPoolRef(pool.code),
});

export const StockPoolSelectField: React.FC<StockPoolSelectFieldProps> = ({
  value,
  onChange,
  market = 'CN',
  title = '选择股票池',
  compact = false,
  stacked = false,
  className = '',
}) => {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div
        className={`${stacked ? 'flex flex-col gap-1.5 w-full' : 'flex items-center gap-2'} ${className}`}
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          className={`inline-flex items-center gap-1.5 border rounded-xl font-semibold transition-all ${
            stacked ? 'w-full justify-center px-2 py-2 text-[11px]' : ''
          } ${
            compact || stacked
              ? 'px-2.5 py-1.5 text-[11px] border-slate-200 bg-white hover:border-blue-300 hover:bg-blue-50/40 text-slate-700'
              : 'px-3 py-2 text-xs border-gray-200 bg-white hover:border-blue-300 hover:bg-blue-50/40 text-slate-700'
          }`}
        >
          <Layers className={compact || stacked ? 'w-3 h-3 text-blue-500 shrink-0' : 'w-3.5 h-3.5 text-blue-500'} />
          <span className="truncate">{value?.name ? value.name : '全市场'}</span>
        </button>
        {value?.ref && (
          <div className={`flex items-center gap-1 ${stacked ? 'justify-between w-full' : ''}`}>
            {value.code && (
              <span className="text-[10px] font-mono text-slate-400 truncate" title={value.ref || undefined}>
                {value.code}
              </span>
            )}
            <button
              type="button"
              onClick={() => onChange(null)}
              className="p-0.5 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-100 shrink-0"
              title="清除股票池限制"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>
      <StockPoolPickerModal
        open={open}
        onClose={() => setOpen(false)}
        selectedPoolId={value?.poolId}
        market={market}
        title={title}
        onSelect={(pool) => {
          onChange(selectionFromPool(pool));
          setOpen(false);
        }}
      />
    </>
  );
};
