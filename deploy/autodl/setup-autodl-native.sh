#!/bin/bash
# QuantMind AutoDL 免 Docker 训练节点交互式初始化脚本
# =====================================================
# 【在哪里运行】 在 AutoDL 节点本机终端执行（用户用 SSH 软件登录 AutoDL 容器后跑），
#               例如：bash setup-autodl-native.sh
# 【做什么】
#   1. 安装训练依赖（torch 已装 + lightgbm/xgboost/catboost/pyqlib/duckdb/quantdb-sdk 等）
#   2. 创建训练工作目录 /root/workspace 与数据盘目录 /root/autodl-fs/quantdb
#   3. 交互式要求填入 QUANTDB_API_KEY，写入环境变量（自动下载 QuantDB 因子数据所需）
#   4. 询问是否自动下载近 3 年训练数据集（l1/l2 因子 parquet）
#       - 是 → 脚本内部调用 quantdb-sdk 增量下载到数据盘
#       - 否 → 引导用户下载离线数据包并上传到指定目录
#
# 依赖：AutoDL 容器自带 Python（/root/miniconda3/bin/python）与 GPU 驱动。
# 幂等：重复执行安全，已装的依赖 / 已有数据自动跳过。
# 说明：AutoDL 自定义镜像不能跨账户共享，故不采用固化镜像交付，而是节点现场安装。
set -euo pipefail

# ── 彩色日志 ─────────────────────────────────────────
info()  { printf "\033[0;36m[INFO]\033[0m  %s\n" "$1"; }
ok()    { printf "\033[0;32m[OK]\033[0m    %s\n" "$1"; }
warn()  { printf "\033[0;33m[WARN]\033[0m  %s\n" "$1"; }
error() { printf "\033[0;31m[ERROR]\033[0m %s\n" "$1"; exit 1; }
ask()   { printf "\033[0;33m[?]\033[0m    %s " "$1"; }

# ── 路径约定 ─────────────────────────────────────────
PYTHON_BIN="${PYTHON_BIN:-/root/miniconda3/bin/python}"
WORK_DIR="${WORK_DIR:-/root/workspace}"                 # 训练工作目录（train.py / 产物 / 临时）
# AutoDL 数据盘（持久，重启不丢）、200G 起步；训练数据集放这里
QUANTDB_DIR="${QUANTDB_DIR:-/root/autodl-fs/quantdb}"
ENV_FILE="${ENV_FILE:-/etc/profile.d/quantmind_sh.sh}"

# ── 0. 平台自检 ──────────────────────────────────────
echo "=============================================="
echo " QuantMind AutoDL 训练节点初始化"
echo "=============================================="

[ "$(id -u)" = "0" ] || warn "建议以 root 运行（AutoDL 默认 root）"

if [ ! -x "$PYTHON_BIN" ]; then
    # 尝试兜底查找
    CANDIDATES=(/root/miniconda3/bin/python /opt/conda/bin/python /usr/bin/python3 /usr/local/bin/python3)
    for c in "${CANDIDATES[@]}"; do [ -x "$c" ] && PYTHON_BIN="$c" && break; done
fi
[ -x "$PYTHON_BIN" ] || error "未找到 Python。请先确认 AutoDL 具备 Python 环境，或用 PYTHON_BIN 指定。"
info "使用 Python: $PYTHON_BIN  ($("$PYTHON_BIN" --version 2>&1))"

echo ""
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_LINE="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || true)"
    if [ -n "$GPU_LINE" ]; then
        info "检测到 GPU: $GPU_LINE"
    else
        warn "nvidia-smi 存在但未返回 GPU 信息"
    fi
else
    warn "未检测到 nvidia-smi（无 GPU 或驱动未装），将以 CPU 训练"
fi

# ── 1. 安装训练依赖（幂等） ─────────────────────────
echo ""
info "[1/4] 安装训练依赖（已存在的包自动跳过）..."
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel --quiet >/dev/null 2>&1 || true

# 与 Dockerfile.trainer 对齐 + QuantDB 直读所需 quantdb-sdk
CORE_PKGS="numpy pandas scipy pyarrow scikit-learn lightgbm xgboost catboost optuna pyyaml requests psutil shap duckdb"
info "核心依赖: $CORE_PKGS"
"$PYTHON_BIN" -m pip install --disable-pip-version-check $CORE_PKGS 2>&1 | tail -2 || error "核心依赖安装失败"
ok "核心依赖安装完成"

if ! "$PYTHON_BIN" -c "import pyqlib" 2>/dev/null; then
    info "安装 pyqlib（体积较大，可能需要几分钟）..."
    "$PYTHON_BIN" -m pip install --disable-pip-version-check pyqlib 2>&1 | tail -3 || warn "pyqlib 安装失败（不影响 QuantDB 直读训练）"
else
    ok "pyqlib 已安装"
fi

if ! "$PYTHON_BIN" -c "import quantdb_sdk" 2>/dev/null; then
    info "安装 quantdb-sdk（QuantDB 因子数据下载所需）..."
    "$PYTHON_BIN" -m pip install --disable-pip-version-check quantdb-sdk 2>&1 | tail -3 || error "quantdb-sdk 安装失败"
else
    ok "quantdb-sdk 已安装"
fi

# ── 2. 创建目录 ──────────────────────────────────────
echo ""
info "[2/4] 创建训练目录..."
mkdir -p "$WORK_DIR" "$WORK_DIR/modules" "$WORK_DIR/templates" "$QUANTDB_DIR"
ok "工作目录: $WORK_DIR"
ok "数据目录: $QUANTDB_DIR  ($(df -h "$QUANTDB_DIR" 2>/dev/null | awk 'NR==2{print $2" 可用 "$4}'))"

# ── 3. 配置 QuantDB API Key ──────────────────────────
echo ""
info "[3/4] 配置 QuantDB 数据源..."
EXISTING_KEY=""
if [ -f "$ENV_FILE" ]; then EXISTING_KEY="$(grep -E '^QUANTDB_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"; fi

if [ -n "$EXISTING_KEY" ]; then
    MASKED="$(echo "$EXISTING_KEY" | cut -c1-6)…"
    ask "已检测到 QUANTDB_API_KEY（$MASKED），是否保持不变？[Y/n]"
    read -r KEEP
    case "${KEEP:0:1}" in
        n|N) KEEP=no ;;
        *) KEEP=yes ;;
    esac
else
    KEEP=no
fi

API_KEY="$EXISTING_KEY"
if [ "$KEEP" != "yes" ]; then
    ask "请输入 QUANTDB_API_KEY（用于下载 QuantDB 因子数据，将写入 $ENV_FILE）:"
    read -r API_KEY
    [ -n "$API_KEY" ] || error "未输入 API Key"
    export QUANTDB_API_KEY="$API_KEY"

    # 写入全局环境变量（登录 shell / 编排器 ssh 都能读到）
    [ -d "$(dirname "$ENV_FILE")" ] || mkdir -p "$(dirname "$ENV_FILE")"
    if [ -f "$ENV_FILE" ]; then
        grep -vE '^QUANTDB_API_KEY=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
        mv "$ENV_FILE.tmp" "$ENV_FILE"
    fi
    echo "export QUANTDB_API_KEY=\"$API_KEY\"" >> "$ENV_FILE"
    # 同时追加到 bashrc 兜底
    grep -qE '^export QUANTDB_API_KEY=' "$HOME/.bashrc" 2>/dev/null || echo "export QUANTDB_API_KEY=\"$API_KEY\"" >> "$HOME/.bashrc" 2>/dev/null || true

    ok "QUANTDB_API_KEY 已保存到环境变量"
    # 当前会话立即生效
    export QUANTDB_API_KEY="$API_KEY"
    # 让后续 source 的文件存在（非登录 ssh 由 profile.d 注入）
    if [ "$ENV_FILE" = "/etc/profile.d/quantmind_sh.sh" ]; then
        grep -q "quantmind_sh.sh" /etc/profile 2>/dev/null || echo ". /etc/profile.d/quantmind_sh.sh" >> /etc/profile 2>/dev/null || true
    fi
else
    ok "沿用已有 QUANTDB_API_KEY"
fi

# ── 4. 训练数据集准备（自动下载 或 离线上传） ────────
echo ""
info "[4/4] 训练数据集..."
DATA_PRESENT=no
if [ -d "$QUANTDB_DIR/6_ml_datasets" ] && [ -n "$(ls -A "$QUANTDB_DIR/6_ml_datasets" 2>/dev/null)" ]; then
    DATA_PRESENT=yes
    SIZE="$(du -sh "$QUANTDB_DIR/6_ml_datasets" 2>/dev/null | cut -f1)"
    ok "检测到已有 QuantDB 数据集（$SIZE），跳过下载"
fi

if [ "$DATA_PRESENT" = "no" ]; then
    ask "是否自动下载近 3 年训练数据集（l1_factors/l2_factors 因子 parquet）？[Y/n, 或输 s 跳过]"
    read -r AUTO_DL
    case "${AUTO_DL:0:1}" in
        n|N|s|S) AUTO_DL=no ;;
        *) AUTO_DL=yes ;;
    esac

    if [ "$AUTO_DL" = "yes" ]; then
        [ -n "$API_KEY" ] || { warn "未配置 QUANTDB_API_KEY，无法自动下载"; API_KEY=""; }
        # 下载近 3 年（默认；可用 AUTODL_SINCE 覆盖）。量级约 700+ 分区、数 GB。
        SINCE="${AUTODL_SINCE:-$(date -d '3 years ago' +%Y-%m-%d 2>/dev/null || echo 2023-09-13)}"
        info "开始下载 QuantDB 因子数据（近 3 年，since=$SINCE）到 $QUANTDB_DIR ..."
        # 直接用 quantdb-sdk 增量同步 l1_factors（训练直读主源）
        QUANTDB_API_KEY="${API_KEY:-$QUANTDB_API_KEY}" \
        QM_QUANTDB_DATA_DIR="$QUANTDB_DIR" \
        "$PYTHON_BIN" - <<PYEOF || warn "数据集下载未完成（可重跑脚本继续续传，SDK 支持增量）"
import os, sys
try:
    from quantdb_sdk import QuantDBClient
except Exception as e:
    print(f"[ERROR] 导入 quantdb_sdk 失败: {e}", file=sys.stderr)
    sys.exit(2)
key = os.environ.get("QUANTDB_API_KEY", "")
if not key:
    print("[ERROR] 未配置 QUANTDB_API_KEY，无法下载", file=sys.stderr)
    sys.exit(3)
save_dir = os.environ.get("QM_QUANTDB_DATA_DIR", ".")
os.makedirs(save_dir, exist_ok=True)
client = QuantDBClient(api_key=key, timeout=(15, 600), max_retries=3)
for ds in ("l1_factors", "l2_factors", "l1_l2_factors"):
    print(f"[SYNC] {ds} ...", flush=True)
    try:
        r = client.sync_dataset(ds, save_dir=save_dir)
        print(f"[SYNC] {ds} 完成: synced={r.get('synced')} matched={r.get('matched')} errors={len(r.get('errors', []))}", flush=True)
    except Exception as e:
        print(f"[WARN] {ds} 同步失败: {e}", flush=True)
print("[DONE] 数据集同步结束", flush=True)
PYEOF
        if [ -d "$QUANTDB_DIR/6_ml_datasets" ] && [ -n "$(ls -A "$QUANTDB_DIR/6_ml_datasets" 2>/dev/null)" ]; then
            ok "数据集已下载到 $QUANTDB_DIR（$(du -sh "$QUANTDB_DIR/6_ml_datasets" | cut -f1)）"
        else
            warn "未检测到数据集，请检查 API Key / 网络，或改用离线方式上传"
        fi
    else
        echo ""
        warn "已选择跳过自动下载。离线方式："
        echo "  1) 在任意有数据的机器执行 QuantDB 同步，得到 6_ml_datasets/ 目录"
        echo "  2) 将其上传（上传/拖拽 或 scp）到指定目录："
        ok "   $QUANTDB_DIR/6_ml_datasets/"
        echo "  3) 完成后可重跑本脚本确认数据已就位"
    fi
fi

# ── 收尾 ─────────────────────────────────────────────
echo ""
echo "=============================================="
ok "初始化完成！该节点已具备免 docker 直跑 train.py 能力。"
echo ""
info "后续（编排器 / 下次训练自动完成，本机无需再操作）:"
info "  1. 主节点 rsync 推送 train.py + docker/training 包 + backend 直读子树到 $WORK_DIR"
info "  2. 直读训练直接读取 $QUANTDB_DIR 的 QuantDB 因子 parquet"
info "  3. nohup python train.py --config config.yaml → 回传产物到主节点"
echo ""
info "如断开重连后想手工验证环境:"
echo "  export QUANTDB_API_KEY=\$(grep QUANTDB_API_KEY $ENV_FILE | cut -d= -f2)"
echo "  $PYTHON_BIN -c \"import lightgbm, duckdb, quantdb_sdk; print('deps OK')\""
echo "=============================================="
ok "脚本完成"