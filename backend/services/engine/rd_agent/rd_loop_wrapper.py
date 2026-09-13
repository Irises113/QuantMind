"""RDLoop 包装器 — 将 RD-Agent 的 FactorRDLoop 适配到 QuantMind

提供统一接口:
- 接收 MarketAdapter 配置市场参数
- 启动/监控/取消 RDLoop
- 从日志中提取因子结果
"""

from __future__ import annotations

import asyncio
import logging
import os
import pickle
import re
import shutil
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .market_adapters import get_adapter, list_markets
from .market_adapters.base import MarketAdapter

logger = logging.getLogger(__name__)


class RDLoopWrapper:
    """封装 RD-Agent FactorRDLoop，提供 QuantMind 兼容接口"""

    def __init__(self, market: str = "a_share") -> None:
        self.adapter: MarketAdapter = get_adapter(market)
        if self.adapter is None:
            raise ValueError(f"Unknown market: {market}. Available: {[m['market_id'] for m in list_markets()]}")
        self.market = market
        self._loop = None
        self._running = False
        self._cancelled = False

    @property
    def market_name(self) -> str:
        return self.adapter.market_name

    def _configure_env(self, task_log_dir: str) -> dict[str, str]:
        """从 MarketAdapter 构建环境变量"""
        env_overrides = self.adapter.get_env_overrides()
        env = {
            **os.environ,
            **env_overrides,
            "LOG_TRACE_PATH": task_log_dir,
            "PYTHONPATH": os.getenv("PYTHONPATH") or "/app",
        }
        # 设置数据文件路径环境变量
        data_file = "/app/alphaagent/scenarios/qlib/experiment/factor_data_template/daily_pv_all.h5"
        if os.path.exists(data_file):
            env["FACTOR_DATA_PATH"] = data_file
        # Ensure critical LLM settings are present
        if not env.get("OPENAI_BASE_URL"):
            env["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "")
        if not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = os.getenv("AI_IDE_LLM_API_KEY", "")
        if not env.get("CHAT_MODEL"):
            env["CHAT_MODEL"] = os.getenv("CHAT_MODEL", "")
        # litellm needs provider prefix for non-OpenAI models
        model = env.get("CHAT_MODEL", "")
        if model and not model.startswith(("openai/", "azure/", "anthropic/", "huggingface/")):
            env["CHAT_MODEL"] = f"openai/{model}"
        env["REASONING_MODEL"] = env.get("CHAT_MODEL", "")
        env["CHAT_STREAM"] = "false"
        # 回测数据从 2016 年开始 (默认 2008 太慢)
        env.setdefault("QLIB_FACTOR_TRAIN_START", os.getenv("QLIB_FACTOR_TRAIN_START", "2016-01-01"))
        env.setdefault("QLIB_FACTOR_VALID_START", os.getenv("QLIB_FACTOR_VALID_START", "2021-01-01"))
        env.setdefault("QLIB_FACTOR_VALID_END", os.getenv("QLIB_FACTOR_VALID_END", "2022-12-31"))
        env.setdefault("QLIB_FACTOR_TEST_START", os.getenv("QLIB_FACTOR_TEST_START", "2023-01-01"))
        env.setdefault("QLIB_FACTOR_TEST_END", os.getenv("QLIB_FACTOR_TEST_END", "2025-12-31"))
        # 因子处理并行数
        env.setdefault("MULTI_PROC_N", os.getenv("MULTI_PROC_N", "4"))
        # 补齐 RD-Agent litellm 后端需要的 LITELLM_ 前缀变量（deepseek 优先）
        from backend.services.engine.rd_agent.llm_env import build_llm_env
        build_llm_env(env)
        return env

    # 需要注入中文/研究方向指令的 prompt key（RD-Agent prompts.yaml 中的顶层键）
    _INJECT_TARGET_KEYS = ("qlib_factor_background", "qlib_quant_background")

    def _build_prompt_suffix(self) -> str:
        """构造追加到因子背景 prompt 末尾的中文与研究方向指令。"""
        suffix = (
            "\n\n====== 语言要求 / Language Requirement ======\n"
            "所有因子的 description 字段必须使用中文撰写。"
            "hypothesis 和 reason 也请用中文。\n"
            "All factor descriptions MUST be written in Chinese (中文). "
            "Hypothesis and reason should also be in Chinese.\n"
        )
        direction = getattr(self, "_direction", "")
        if direction:
            suffix += (
                "\n\n====== 研究方向 / Research Direction ======\n"
                f"用户的研究方向/假设: {direction}\n"
                f"User's research direction/hypothesis: {direction}\n"
                "请围绕此方向进行因子探索。Focus factor exploration on this theme.\n"
            )
        return suffix

    def _patch_prompts_for_chinese(self):
        """注入中文与研究方向指令到 RD-Agent 提示词。

        RD-Agent 的 prompt 存于各包内的 prompts.yaml，经 `utils.agent.tpl.load_content`
        每次按需读取（无缓存），因此在该函数返回值上追加指令是唯一可靠的注入点 ——
        早期实现 import `...experiment.prompts` 模块，但该模块并不存在，注入从未生效。
        """
        try:
            from rdagent.utils.agent import tpl as _tpl

            if getattr(_tpl, "_qm_patched", False):
                _tpl._qm_suffix = self._build_prompt_suffix()
                return

            suffix_holder = self._build_prompt_suffix()
            _tpl._qm_suffix = suffix_holder
            original_load = _tpl.load_content
            target_keys = self._INJECT_TARGET_KEYS

            def patched_load(uri: str, *args, **kwargs):
                content = original_load(uri, *args, **kwargs)
                if not isinstance(content, str):
                    return content
                if not any(uri.endswith(f":{k}") for k in target_keys):
                    return content
                if "语言要求" in content:
                    return content
                return content + getattr(_tpl, "_qm_suffix", "")

            _tpl.load_content = patched_load
            _tpl._qm_patched = True
            logger.info(
                "[%s] Patched RD-Agent prompt loader (Chinese + direction=%s)",
                self.market,
                bool(getattr(self, "_direction", "")),
            )
        except Exception as e:
            logger.error("Failed to patch prompts for Chinese: %s", e)

    def _create_loop(self):
        """创建 FactorRDLoop 实例"""
        from rdagent.app.qlib_rd_loop.conf import FactorBasePropSetting
        from rdagent.app.qlib_rd_loop.factor import FactorRDLoop
        from rdagent.core.utils import import_class

        # 注入中文指令
        self._patch_prompts_for_chinese()

        prop_setting_path = self.adapter.get_prop_setting_class()
        prop_cls = import_class(prop_setting_path)
        prop_setting = prop_cls()

        loop = FactorRDLoop(prop_setting)
        return loop

    async def run(
        self,
        loop_n: int = 3,
        task_log_dir: str = "",
        direction: str = "",
    ) -> dict[str, Any]:
        """执行因子挖掘循环

        Args:
            loop_n: 循环轮数
            task_log_dir: 日志输出目录
            direction: 挖掘方向/假设

        Returns:
            包含 factors 和 metadata 的结果字典
        """
        self._running = True
        self._cancelled = False
        self._direction = direction

        try:
            # 配置环境变量
            env = self._configure_env(task_log_dir)
            for k, v in env.items():
                os.environ[k] = v

            # 确保 daily_pv.h5 数据文件可用
            self._ensure_data_file(task_log_dir)

            # RD-Agent workspace / 数据目录对齐：设绝对路径 env 变量 + 切 cwd。
            # RD-Agent 的 FACTOR_COSTEER_SETTINGS.data_folder 与 workspace_path 默认用
            # Path.cwd() 相对解析（import 时冻结），必须显式指到 task_log_dir，
            # 否则因子执行时找不到 daily_pv.h5（默认 workspace 在 /tmp/git_ignore_folder/...）。
            if task_log_dir:
                os.environ["WORKSPACE_PATH"] = task_log_dir
                os.environ["FACTOR_CoSTEER_data_folder"] = os.path.join(
                    task_log_dir, "git_ignore_folder", "factor_implementation_source_data"
                )
                os.environ["FACTOR_CoSTEER_data_folder_debug"] = os.path.join(
                    task_log_dir, "git_ignore_folder", "factor_implementation_source_data_debug"
                )
                os.chdir(task_log_dir)

            logger.info("[%s] RDLoop starting: market=%s, loops=%d, log_dir=%s",
                        self.market, self.adapter.market_name, loop_n, task_log_dir)

            # 创建 RDLoop
            self._loop = self._create_loop()

            # 注入 base_features_path（L1/L2 因子集供 LLM 参考）
            base_features_path = getattr(self, "_base_features_path", None)
            if base_features_path:
                self._loop._init_base_features(base_features_path)
                logger.info("[%s] Loaded base features from %s", self.market, base_features_path)
            step_count = len(self._loop.steps)
            total_steps = loop_n * step_count

            logger.info("[%s] Steps per loop: %d, total steps: %d", self.market, step_count, total_steps)
            logger.info("[%s] Step flow: %s", self.market,
                        " → ".join(getattr(s, '__name__', s.__class__.__name__) for s in self._loop.steps))

            # 运行循环
            t0 = time.time()
            await self._loop.run(step_n=total_steps)
            elapsed = time.time() - t0

            logger.info("[%s] RDLoop completed in %.1fs", self.market, elapsed)

            # 提取结果
            factors = self._extract_factors(task_log_dir)

            return {
                "market": self.market,
                "market_name": self.adapter.market_name,
                "loop_n": loop_n,
                "elapsed_seconds": elapsed,
                "factors": factors,
                "total_factors": len(factors),
                "log_dir": task_log_dir,
                "success": True,
            }

        except asyncio.CancelledError:
            self._cancelled = True
            logger.info("[%s] RDLoop cancelled", self.market)
            return {"market": self.market, "cancelled": True, "factors": [], "success": False}

        except Exception as e:
            logger.exception("[%s] RDLoop failed: %s", self.market, e)
            return {"market": self.market, "error": str(e), "factors": [], "success": False}

        finally:
            self._running = False

    def cancel(self):
        """请求取消运行"""
        self._cancelled = True
        logger.info("[%s] Cancellation requested", self.market)

    def _ensure_data_file(self, task_log_dir: str = ""):
        """确保 daily_pv.h5 数据文件和 Qlib 数据目录在 RD-Agent 期望的位置可用

        RD-Agent 的 subprocess 以 task_log_dir 为 cwd 运行，
        FACTOR_COSTEER_SETTINGS.data_folder (git_ignore_folder/factor_implementation_source_data)
        相对于 cwd 解析。需要在 task_log_dir 下创建该目录并复制数据文件。

        同时确保对应的 Qlib provider_uri 目录可用。
        """
        import shutil

        # 根据市场选择数据源
        market_data_map = {
            "crypto": {
                "source_all": "/app/db/crypto_data/5min_pv.h5",
                "source_debug": "/app/db/crypto_data/5min_pv.h5",
                "qlib_source": "/app/db/qlib_data/crypto_data",
                "qlib_target_name": "crypto_data",
            },
            "hong_kong": {
                "source_all": "/app/db/hk_data/daily_pv.h5",
                "source_debug": "/app/db/hk_data/daily_pv.h5",
                "qlib_source": "/app/db/qlib_data/hk_data",
                "qlib_target_name": "hk_data",
            },
            "us_stock": {
                "source_all": "/app/db/us_data/daily_pv.h5",
                "source_debug": "/app/db/us_data/daily_pv.h5",
                "qlib_source": "/app/db/qlib_data/us_data",
                "qlib_target_name": "us_data",
            },
            "futures": {
                "source_all": "/app/db/futures_data/daily_pv.h5",
                "source_debug": "/app/db/futures_data/daily_pv.h5",
                "qlib_source": "/data/quantfutures/.qlib_cache/futures_data",
                "qlib_target_name": "futures_data",
            },
        }

        # 默认 A 股
        market_cfg = market_data_map.get(self.market, {
            "source_all": "/app/alphaagent/scenarios/qlib/experiment/factor_data_template/daily_pv_all.h5",
            "source_debug": "/app/alphaagent/scenarios/qlib/experiment/factor_data_template/daily_pv_debug.h5",
            "qlib_source": "/data/qlib/cn_data",
            "qlib_target_name": "cn_data",
        })

        source_all = market_cfg["source_all"]
        source_debug = market_cfg["source_debug"]

        # base_dir: RD-Agent subprocess cwd (task log dir)
        base_dir = task_log_dir if task_log_dir else os.getcwd()

        # RD-Agent data folders (relative to subprocess cwd)
        target_all = os.path.join(base_dir, "git_ignore_folder/factor_implementation_source_data/daily_pv.h5")
        target_debug = os.path.join(base_dir, "git_ignore_folder/factor_implementation_source_data_debug/daily_pv.h5")

        # A 股：优先从 QuantDB parquet 生成 .h5，失败则 fallback 到预生成文件
        if self.market == "a_share":
            quantdb_dir = self._resolve_quantdb_dir()
            h5_generated = False
            if quantdb_dir:
                ok_all = self._generate_h5_from_parquet(quantdb_dir, target_all, debug=False)
                ok_debug = self._generate_h5_from_parquet(quantdb_dir, target_debug, debug=True)
                h5_generated = ok_all and ok_debug
            if not h5_generated:
                logger.warning("[%s] H5 generation failed or QuantDB dir missing, falling back to copy", self.market)
                self._copy_h5_source(source_all, target_all, source_debug, target_debug)
        elif self.market == "futures":
            # 期货：从 QuantFutures parquet 生成（无预生成文件，fut_ 前缀 instrument）
            futures_dir = self._resolve_market_data_dir("/data/quantfutures")
            h5_generated = False
            if futures_dir.is_dir():
                ok_all = self._generate_futures_h5(futures_dir, target_all, debug=False)
                ok_debug = self._generate_futures_h5(futures_dir, target_debug, debug=True)
                h5_generated = ok_all and ok_debug
            if not h5_generated:
                logger.warning(
                    "[%s] Futures h5 generation failed or QuantFutures dir missing, falling back to copy",
                    self.market,
                )
                self._copy_h5_source(source_all, target_all, source_debug, target_debug)
        else:
            self._copy_h5_source(source_all, target_all, source_debug, target_debug)

        # Ensure Qlib provider_uri data is available at ~/.qlib/qlib_data/<market>
        qlib_source = market_cfg["qlib_source"]
        qlib_target_name = market_cfg["qlib_target_name"]

        # parquet 单源市场：优先固定目录/各市场本地派生缓存（统一走 qlib_paths）
        market_cache = {
            "a_share": ("CN", ("/data/quantdb", ".qlib_cache", "cn_data")),
            "hong_kong": ("HK", ("/data/quanthk", ".qlib_cache", "hk_data")),
            "us_stock": ("US", ("/data/quantus", ".qlib_cache", "us_data")),
        }
        if self.market in market_cache:
            mkt_key, (base_dir_cfg, cache_sub, leaf) = market_cache[self.market]
            candidates = [Path(base_dir_cfg) / cache_sub / leaf,
                          self._resolve_market_data_dir(base_dir_cfg) / cache_sub / leaf]
            try:
                from backend.shared.qlib_paths import resolve_qlib_provider_uri
                candidates.insert(0, Path(resolve_qlib_provider_uri(mkt_key)))
            except Exception:
                pass
            for candidate in candidates:
                if candidate.is_dir():
                    qlib_source = str(candidate)
                    break

        qlib_target = os.path.expanduser(f"~/.qlib/qlib_data/{qlib_target_name}")
        if os.path.isdir(qlib_source):
            # 修复过期 symlink：若指向错误路径则重建
            need_create = False
            if os.path.islink(qlib_target):
                current_target = os.readlink(qlib_target)
                if os.path.abspath(current_target) != os.path.abspath(qlib_source):
                    logger.info("[%s] Replacing stale symlink: %s -> %s (was %s)",
                                self.market, qlib_target, qlib_source, current_target)
                    os.unlink(qlib_target)
                    need_create = True
            elif os.path.isdir(qlib_target):
                # 真实目录而非 symlink，rename 后建 symlink
                backup = qlib_target + "_backup"
                logger.info("[%s] Replacing real directory with symlink: %s -> %s (backup: %s)",
                            self.market, qlib_target, qlib_source, backup)
                try:
                    if os.path.exists(backup):
                        shutil.rmtree(backup)
                    os.rename(qlib_target, backup)
                    need_create = True
                except Exception as e:
                    logger.warning("[%s] Failed to replace directory with symlink: %s", self.market, e)
            else:
                need_create = True

            if need_create:
                try:
                    os.makedirs(os.path.dirname(qlib_target), exist_ok=True)
                    os.symlink(qlib_source, qlib_target)
                    logger.info("[%s] Created symlink: %s -> %s", self.market, qlib_target, qlib_source)
                except Exception as e:
                    logger.warning("[%s] Failed to create Qlib symlink: %s", self.market, e)

        # A 股：生成 base_factors.json 供 RD-Agent LLM 参考
        if self.market == "a_share" and hasattr(self.adapter, "generate_base_factors_json"):
            data_dir = os.path.join(base_dir, "git_ignore_folder", "factor_implementation_source_data")
            os.makedirs(data_dir, exist_ok=True)
            bf_path = self.adapter.generate_base_factors_json(data_dir)
            if bf_path:
                self._base_features_path = data_dir
                logger.info("[%s] base_factors.json ready at %s", self.market, data_dir)
            else:
                self._base_features_path = None
        else:
            self._base_features_path = None

    def _copy_h5_source(self, source_all, target_all, source_debug, target_debug):
        """从预生成的 h5 文件复制。"""
        for target, source in [(target_all, source_all), (target_debug, source_debug)]:
            if not os.path.exists(source):
                logger.warning("[%s] Source data file not found: %s", self.market, source)
                continue
            target_dir = os.path.dirname(target)
            os.makedirs(target_dir, exist_ok=True)
            if not os.path.exists(target) or os.path.getmtime(source) > os.path.getmtime(target):
                try:
                    shutil.copy2(source, target)
                    logger.info("[%s] Copied data file: %s -> %s", self.market, source, target)
                except Exception as e:
                    logger.warning("[%s] Failed to copy data file to %s: %s", self.market, target, e)

    @staticmethod
    def _resolve_quantdb_dir() -> str | None:
        """解析 QuantDB 数据目录，返回存在的路径或 None。"""
        env_dir = os.getenv("QM_QUANTDB_DATA_DIR", "").strip()
        candidates = [env_dir] if env_dir else []
        candidates += ["/data/quantdb", "/app/data/quantdb"]
        for d in candidates:
            if d and os.path.isdir(d):
                return d
        return None

    @staticmethod
    def _resolve_market_data_dir(container_path: str) -> Path:
        """解析市场本地 parquet 数据目录（容器挂载 /data/quantX）。

        支持 env 覆盖（QM_QUANTHK_DATA_DIR 等）；容器内为 /data/quantX，
        宿主机构回退到项目 data/quantX。返回不存在的候选也不报错，
        由调用方用 .is_dir() 判断。
        """
        env_map = {
            "/data/quanthk": "QM_QUANTHK_DATA_DIR",
            "/data/quantus": "QM_QUANTUS_DATA_DIR",
            "/data/quantbc": "QM_QUANTBC_DATA_DIR",
            "/data/quantdb": "QM_QUANTDB_DATA_DIR",
            "/data/quantfutures": "QM_QUANTFUTURES_DATA_DIR",
        }
        env_name = env_map.get(container_path)
        if env_name:
            env_val = os.getenv(env_name, "").strip()
            if env_val:
                return Path(env_val)
        project_data = Path(__file__).resolve().parents[4] / "data" / Path(container_path).name
        return project_data

    def _generate_h5_from_parquet(self, quantdb_dir: str, output_path: str, *, debug: bool = False) -> bool:
        """从 QuantDB parquet 生成 RD-Agent 期望的 daily_pv.h5 文件。

        H5 格式:
        - Key: "data"
        - Index: MultiIndex [datetime, instrument]
        - Columns: ["$open", "$high", "$low", "$close", "$volume", "$factor"]
        - instrument 格式: Qlib 格式 sh600036

        Returns:
            True if h5 file was generated/already current, False on failure.
        """
        if os.path.exists(output_path):
            # 检查 parquet 最新日期是否比 h5 新
            try:
                h5_mtime = os.path.getmtime(output_path)
                kline_dir = os.path.join(quantdb_dir, "1_kline_data", "daily_forward")
                if os.path.isdir(kline_dir):
                    partitions = [d for d in os.listdir(kline_dir) if d.startswith("dt=")]
                    if partitions:
                        latest_dt = max(partitions)
                        latest_parquet = os.path.join(kline_dir, latest_dt, "data.parquet")
                        if os.path.exists(latest_parquet) and os.path.getmtime(latest_parquet) > h5_mtime:
                            logger.info("[%s] Parquet newer than h5, regenerating: %s", self.market, output_path)
                        else:
                            return True
                    else:
                        return True
                else:
                    return True
            except Exception:
                return True

        try:
            from backend.services.engine.data_platform.quantdb_hub import QuantDBDataHub
            hub = QuantDBDataHub(quantdb_dir)
            if not hub.available:
                logger.error("[%s] QuantDBDataHub not available for h5 generation", self.market)
                return False

            # 获取股票列表
            df_stocks = hub.fetch_stock_list()
            if df_stocks.empty:
                logger.error("[%s] No stock list from QuantDB", self.market)
                return False

            symbol_col = "Symbol" if "Symbol" in df_stocks.columns else "symbol"
            symbols = df_stocks[symbol_col].dropna().unique()

            if debug:
                # Debug 模式：只取少量 symbol
                symbols = symbols[:50]

            import numpy as np

            # 批量读取：一次查全部 symbol，避免逐股票 N 次分区扫描
            # （早期实现对 ~5400 只股票各查 2 次，单次生成需 40 分钟以上）
            df = hub.fetch_daily_kline_batch(
                [str(s) for s in symbols], date(2020, 1, 1), date(2026, 12, 31), adjust="qfq"
            )
            if df is None or df.empty:
                logger.error("[%s] No K-line data read from QuantDB", self.market)
                return False
            df_unadj = hub.fetch_daily_kline_batch(
                [str(s) for s in symbols], date(2020, 1, 1), date(2026, 12, 31), adjust="none"
            )

            # 前复权价可能为负（高分红股票多年除权后 qfq 价转负），会污染 Qlib 因子
            # 计算，这里整体剔除这些行。
            valid = df["close"].to_numpy(dtype="float64") > 0
            dropped = int((~valid).sum())
            if dropped:
                logger.warning(
                    "[%s] Dropped %d rows with non-positive qfq close (negative 前复权价)",
                    self.market,
                    dropped,
                )
                df = df.loc[valid].reset_index(drop=True)
            if df.empty:
                logger.error("[%s] No positive-price K-line rows from QuantDB", self.market)
                return False

            # 按 (symbol, trade_date) 对齐不复权收盘价以计算 $factor
            if df_unadj is not None and not df_unadj.empty:
                unadj = df_unadj[["symbol", "trade_date", "close"]].rename(
                    columns={"close": "_close_unadj"}
                )
                df = df.merge(unadj, on=["symbol", "trade_date"], how="left")
                factor = np.where(
                    df["_close_unadj"].to_numpy(dtype="float64", na_value=0.0) > 0,
                    df["close"].to_numpy(dtype="float64")
                    / df["_close_unadj"].to_numpy(dtype="float64", na_value=1.0),
                    1.0,
                )
            else:
                factor = np.ones(len(df))

            instruments = [self._to_qlib_symbol(str(s)) for s in df["symbol"]]
            combined = pd.DataFrame(
                {
                    "$open": df["open"].to_numpy(dtype="float64"),
                    "$high": df["high"].to_numpy(dtype="float64"),
                    "$low": df["low"].to_numpy(dtype="float64"),
                    "$close": df["close"].to_numpy(dtype="float64"),
                    "$volume": df["volume"].to_numpy(dtype="float64"),
                    "$factor": factor,
                },
                index=pd.MultiIndex.from_arrays(
                    [pd.to_datetime(df["trade_date"]), instruments],
                    names=["datetime", "instrument"],
                ),
            )
            combined = combined.sort_index()

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            combined.to_hdf(output_path, key="data", mode="w")
            logger.info("[%s] Generated h5 from parquet: %s (%d rows)", self.market, output_path, len(combined))
            return True

        except Exception as exc:
            logger.error("[%s] Failed to generate h5 from parquet: %s", self.market, exc)
            return False

    @staticmethod
    def _to_qlib_symbol(symbol: str) -> str:
        """suffix 格式 600036.SH -> Qlib 格式 sh600036。"""
        s = symbol.strip()
        if "." in s:
            code, exchange = s.split(".", 1)
            return f"{exchange.lower()}{code}"
        return s.lower()

    def _generate_futures_h5(
        self, futures_dir: Path | str, output_path: str, *, debug: bool = False
    ) -> bool:
        """从 QuantFutures parquet 生成 RD-Agent 的 daily_pv.h5。

        与 A 股版区别:
        - instrument 用 fut_ 前缀（对齐 QlibDataBuilder._MARKET_QLIB_PREFIX）
        - 期货无复权概念，$factor 恒为 1
        - 行情从 2016 年起量价才完整，起点对齐 Qlib 因子训练窗口
        """
        import numpy as np

        if os.path.exists(output_path):
            try:
                h5_mtime = os.path.getmtime(output_path)
                kline_dir = Path(futures_dir) / "1_kline_data" / "daily_forward"
                if kline_dir.is_dir():
                    partitions = sorted(d for d in os.listdir(kline_dir) if d.startswith("dt="))
                    if partitions:
                        latest_parquet = kline_dir / partitions[-1] / "data.parquet"
                        if os.path.exists(latest_parquet) and os.path.getmtime(latest_parquet) > h5_mtime:
                            logger.info("[%s] Parquet newer than h5, regenerating: %s", self.market, output_path)
                        else:
                            return True
                    else:
                        return True
                else:
                    return True
            except Exception:
                return True

        try:
            from backend.services.engine.data_platform.quantfutures_hub import (
                QuantFuturesDataHub,
            )

            hub = QuantFuturesDataHub(Path(futures_dir))
            if not hub.available:
                logger.error("[%s] QuantFuturesDataHub not available for h5 generation", self.market)
                return False

            # 期货无 instrument_detail parquet，从 daily_forward 分区推导 symbol 列表
            symbols: set[str] = set()
            kline_dir = Path(futures_dir) / "1_kline_data" / "daily_forward"
            if not kline_dir.is_dir():
                logger.error("[%s] QuantFutures daily_forward missing", self.market)
                return False
            import duckdb

            con = duckdb.connect(config={"memory_limit": "4GB", "threads": "2"})
            try:
                df_syms = con.execute(
                    f"SELECT DISTINCT symbol FROM read_parquet('{kline_dir / 'dt=*' / 'data.parquet'}', hive_partitioning=1)"
                ).fetchdf()
            finally:
                con.close()
            symbols = {str(s) for s in df_syms["symbol"].dropna().unique()}
            if not symbols:
                logger.error("[%s] No futures symbols found in parquet", self.market)
                return False

            symbol_list = sorted(symbols)
            if debug:
                symbol_list = symbol_list[:50]

            # 行情起点对齐 Qlib 因子训练窗口（2016 起量价完整）
            df = hub.fetch_daily_kline_batch(
                symbol_list, date(2016, 1, 1), date(2026, 12, 31), adjust="qfq"
            )
            if df is None or df.empty:
                logger.error("[%s] No futures K-line data read", self.market)
                return False

            # 剔除无效行情行（价格 <= 0 或非有限值会污染因子计算）
            cols = ["open", "high", "low", "close"]
            valid = np.isfinite(df[cols].to_numpy(dtype="float64")).all(axis=1) & (
                df[cols].to_numpy(dtype="float64") > 0
            ).all(axis=1)
            dropped = int((~valid).sum())
            if dropped:
                logger.warning("[%s] Dropped %d invalid futures rows", self.market, dropped)
                df = df.loc[valid].reset_index(drop=True)
            if df.empty:
                logger.error("[%s] No valid futures K-line rows", self.market)
                return False

            instruments = [f"fut_{s}" for s in df["symbol"]]
            combined = pd.DataFrame(
                {
                    "$open": df["open"].to_numpy(dtype="float64"),
                    "$high": df["high"].to_numpy(dtype="float64"),
                    "$low": df["low"].to_numpy(dtype="float64"),
                    "$close": df["close"].to_numpy(dtype="float64"),
                    "$volume": df["volume"].to_numpy(dtype="float64"),
                    "$factor": np.ones(len(df)),
                },
                index=pd.MultiIndex.from_arrays(
                    [pd.to_datetime(df["trade_date"]), instruments],
                    names=["datetime", "instrument"],
                ),
            )
            combined = combined.sort_index()

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            combined.to_hdf(output_path, key="data", mode="w")
            logger.info(
                "[%s] Generated futures h5 from parquet: %s (%d rows, %d symbols)",
                self.market, output_path, len(combined), len(symbol_list),
            )
            return True

        except Exception as exc:
            logger.error("[%s] Failed to generate futures h5 from parquet: %s", self.market, exc)
            return False

    @property
    def is_running(self) -> bool:
        return self._running

    def _extract_factors(self, log_dir: str) -> list[dict[str, Any]]:
        """从 RD-Agent 日志目录提取因子

        RDLoop 输出结构 (pickle):
        - experiment generation/**/*.pkl → 实验任务 (因子名 + 表达式)
        - coder result/**/*.pkl → 编码结果 (因子代码)
        - feedback/**/*.pkl → 反馈 (IC 等指标)
        """
        log_path = Path(log_dir)
        if not log_path.exists():
            logger.warning("[%s] Log dir not found: %s", self.market, log_dir)
            return []

        pkl_count = len(list(log_path.glob("**/*.pkl")))
        logger.info("[%s] Scanning log dir: %s (%d pkl files)", self.market, log_dir, pkl_count)

        # 1. Factor metadata
        factor_meta: dict[str, dict] = {}
        for pkl_path in sorted(log_path.glob("**/experiment generation/**/*.pkl")):
            try:
                with open(pkl_path, "rb") as f:
                    tasks = pickle.load(f)
                if not isinstance(tasks, list):
                    tasks = [tasks]
                for t in tasks:
                    name = getattr(t, "factor_name", None) or getattr(t, "name", None)
                    if not name:
                        continue
                    factor_meta[name] = {
                        "name": name,
                        "formulation": getattr(t, "factor_formulation", "") or "",
                        "description": getattr(t, "description", "") or "",
                        "category": getattr(t, "category", "") or "",
                    }
            except Exception as e:
                logger.debug("Failed to read %s: %s", pkl_path, e)

        # 2. Factor code —— 直接用 workspace.target_task.factor_name 映射，
        #    不再依赖函数名与因子名一致（LLM 函数命名可能与因子名不同）。
        factor_code: dict[str, str] = {}
        for pkl_path in sorted(log_path.glob("**/coder result/**/*.pkl")):
            try:
                with open(pkl_path, "rb") as f:
                    workspaces = pickle.load(f)
                if not isinstance(workspaces, list):
                    workspaces = [workspaces]
                for ws in workspaces:
                    file_dict = getattr(ws, "file_dict", None) or {}
                    code = file_dict.get("factor.py", "")
                    if not code:
                        for v in file_dict.values():
                            if isinstance(v, str) and "def " in v:
                                code = v
                                break
                    if not code:
                        continue
                    name = ""
                    target = getattr(ws, "target_task", None)
                    if target is not None:
                        name = str(
                            getattr(target, "factor_name", None)
                            or getattr(target, "name", None)
                            or ""
                        ).strip()
                    if not name:
                        fn_match = re.search(r"def\s+(\w+)\s*\(", code)
                        name = (
                            fn_match.group(1).removeprefix("calculate_")
                            if fn_match
                            else f"factor_{len(factor_code)}"
                        )
                    factor_code[name] = code
            except Exception as e:
                logger.debug("Failed to read coder result %s: %s", pkl_path, e)

        # 3. Feedback
        feedback_text = ""
        for pkl_path in sorted(log_path.glob("**/feedback/**/*.pkl")):
            try:
                with open(pkl_path, "rb") as f:
                    fb = pickle.load(f)
                if isinstance(fb, str):
                    feedback_text += fb + "\n"
                elif isinstance(fb, (list, tuple)):
                    for item in fb:
                        if isinstance(item, str):
                            feedback_text += item + "\n"
            except Exception as e:
                logger.debug("Failed to read feedback %s: %s", pkl_path, e)

        # 4. Merge —— 只保留**已完成 coding 阶段**的因子（有代码）。
        #    半成品（如 loop_n 预算截断时第二轮只有 experiment generation、无 coder result）
        #    不落库，避免因子库里出现无代码、无法回测的条目。
        factors: list[dict] = []
        for name, code in sorted(factor_code.items()):
            meta = factor_meta.get(name, {})
            factors.append({
                "name": meta.get("name", name),
                "formulation": meta.get("formulation", ""),
                "description": meta.get("description", ""),
                "category": meta.get("category", ""),
                "code": code,
                "market": self.market,
                "feedback": feedback_text[:5000] if feedback_text else "",
            })

        missing = sorted(set(factor_meta.keys()) - set(factor_code.keys()))
        if missing:
            logger.info(
                "[%s] %d factors generated without code (partial/incomplete loop), skipped: %s",
                self.market, len(missing), missing,
            )

        logger.info("[%s] Extracted %d factors", self.market, len(factors))
        return factors


# ── Runner script entry point (subprocess) ──


def run_factor_mining_subprocess(
    market: str,
    task_id: str,
    user_id: str,
    loop_n: int,
    log_dir: str,
    direction: str = "",
) -> dict[str, Any]:
    """在子进程中执行因子挖掘（用于 launcher 调用）

    这是同步入口点，由 launcher 的 subprocess 调用。
    """
    wrapper = RDLoopWrapper(market=market)
    logger.info("Starting factor mining: market=%s, task=%s, loops=%d", market, task_id, loop_n)
    result = asyncio.run(wrapper.run(
        loop_n=loop_n,
        task_log_dir=log_dir,
        direction=direction,
    ))
    result["task_id"] = task_id
    result["user_id"] = user_id
    return result
