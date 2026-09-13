# AutoDL 免 Docker 训练节点部署

在 AutoDL 的 Python 容器上部署免 docker 直跑训练（`RemoteSSHOrchestrator` 的 `native_python` 执行模式）。

## 背景

AutoDL 显卡实例默认是 Python 容器（有 torch + GPU，但**没有 docker**）。项目训练链路支持两种执行模式：

| 模式 | 节点形态 | 脚本 |
|------|----------|------|
| `ssh_docker`（默认） | 已装 docker + nvidia-container-toolkit + 训练镜像 | `scripts/setup/build-autodl-remote.sh` |
| `native_python`（免 docker） | 纯 AutoDL Python 容器 | **本目录 `setup-autodl-native.sh`** |

> AutoDL 自定义镜像**不能跨账户共享**，因此不采用「固化镜像」交付，改用「节点现场安装依赖」。
> 代码（train.py / training 包 / backend 直读子树）不入镜像，由编排器每次 rsync 流式推送。

## 快速开始（新机器部署步骤）

1. **开通 AutoDL 实例**，选择带 GPU + Python 的镜像（如 PyTorch 基础镜像）。
2. **SSH 登录 AutoDL 终端**（用 SSH 软件，如 XShell / PuTTY / 终端）：
   ```bash
   ssh -p <端口> root@connect.xxx.seetacloud.com
   ```
3. **上传并运行初始化脚本**（在 AutoDL 本机执行，不是主节点）：
   ```bash
   bash setup-autodl-native.sh
   ```
   脚本会交互式完成：
   - 检测 Python / GPU
   - 安装训练依赖（lightgbm / xgboost / catboost / pyqlib / duckdb / quantdb-sdk 等，幂等）
   - 创建目录：工作区 `/root/workspace`、数据盘 `/root/autodl-fs/quantdb`
   - 交互式填写 `QUANTDB_API_KEY` 并写入环境变量
   - 询问是否自动下载近 3 年训练数据集（或跳过走离线上传）
4. **把脚本分发到新机器**：新机器只需能拿到这个脚本（已提交 git，`git pull` 或 scp 即可）。

## 数据集准备

训练直读 QuantDB 因子 parquet，数据放 **AutoDL 数据盘** `/root/autodl-fs/quantdb`（持久，重启不丢；系统盘 `/` 重启会清）。

- **自动下载**：脚本内选择 `Y`，调 quantdb-sdk 增量同步近 3 年 `l1_factors/l2_factors/l1_l2_factors` 到数据盘。
- **离线方式**：在其他机器同步好 `6_ml_datasets/` 后上传到指定目录：
  ```
  /root/autodl-fs/quantdb/6_ml_datasets/
  ```

## 环境变量持久化

`QUANTDB_API_KEY` 写入：
- `/etc/profile.d/quantmind_sh.sh`（全局，登录/编排器 ssh 都能读到）
- `~/.bashrc`（兜底）

手工验证：
```bash
export QUANTDB_API_KEY=$(grep QUANTDB_API_KEY /etc/profile.d/quantmind_sh.sh | cut -d= -f2)
/root/miniconda3/bin/python -c "import lightgbm, duckdb, quantdb_sdk; print('deps OK')"
```

## 在项目侧注册节点

初始化完成后，编辑项目根 `config/training_nodes.yaml` 增加该节点（不会被 gitignore 提交的密钥：
```yaml
  - id: autodl-2
    name: "AutoDL RTX4090 (免Docker)"
    host: "connect.xxx.seetacloud.com"
    port: <端口>
    user: "root"
    ssh_key: "C:\\Users\\<you>\\.ssh\\id_ed25519"   # 或 ssh_password
    work_dir: "/root/workspace"
    exec_mode: "native_python"
    gpus: "all"
    quantdb_dir: "/root/autodl-fs/quantdb"
```

> `config/training_nodes.yaml` 被 `.gitignore` 忽略，含明文凭证，不会提交。

## 验证

节点侧（SSH 登录 AutoDL 后）：
```bash
# 依赖就绪
/root/miniconda3/bin/python -c "import lightgbm, duckdb, quantdb_sdk; print('deps OK')"
# GPU 就绪
nvidia-smi
# 数据集就绪
du -sh /root/autodl-fs/quantdb/6_ml_datasets
```

主节点侧（提交一次小训练，`node_id=该节点id`），观察：
- redis 日志流出现「原生训练进程已启动」
- 产物回传 `/data/training_jobs/{run_id}/`
- 模型注册 `qm_user_models`

## 文件

- `setup-autodl-native.sh` — AutoDL 节点交互式初始化脚本（本机执行）
- `atudol-native-sync.py`（可选，如需要独立下载脚本时补充）