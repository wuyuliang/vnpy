# cta/sim/

## 主要做什么

**仿真平台接入**：把研究阶段的策略 + 三段模型搬到仿真盘上跑（不发真实订单），逐日产出仿真成交、对比 OOT 期望、生成 parity 报告，作为实盘前的最后一道关。

## 关键文件

| 文件 | 作用 |
|---|---|
| [sim_runner.py](sim_runner.py) | 仿真主循环，类似 [cta/live/live_runner.py](../live/live_runner.py) 但 gateway 是仿真 |
| [parity_check.py](parity_check.py) | 仿真单笔 vs OOT 单笔逐笔对账：fill_price / fill_time / cost / pnl 偏差 |
| [daily_parity_report.py](daily_parity_report.py) | 每日仿真收盘后生成 parity_report.md，含 diff 明细与告警 |
| [cfg_consistency_check.py](cfg_consistency_check.py) | 对比 OOT/sim/live 的 `cfg_fingerprint.json` 关键字段，防配置漂移 |
| [tests/](tests/) | parity 与 runner 测试 |

## 详细过程

```
[启动]
    sim_runner.start()
        ├─ 加载 cluster_registry.json + 三段模型 + score_calibration
        ├─ 订阅仿真行情
        └─ 复用 cta/live/online_feature.py / cta/portfolio_logic/* / cta/model/oot_*
            （和实盘共用同一份决策代码）

[每笔成交]
    sim_runner.on_trade(trade)
        ├─ 落盘到 sim_trades.csv
        └─ parity_check.compare_with_oot_expected()
              ├─ 找到对应 OOT 候选（按 symbol/exchange/entry_time）
              ├─ diff fill_price / fill_time / cost / pnl
              └─ 累计到 parity_log

[收盘]
    daily_parity_report.generate()
        ├─ 当日仿真 PnL vs OOT 期望 PnL
        ├─ 累计 diff 趋势
        └─ 告警：连续 N 日 diff > 阈值
```

## 注意事项

- **sim 与 live 共用决策代码**：任何 fill / cost / 决策都不能"sim 一套、live 一套"。本目录只负责调度与对账，不重写决策。
- **OOT 期望是 ground truth**：仿真和 live 都必须以 OOT real-execution 的输出（`*_oot_trade_details.csv`）为对账基准。出现 diff 优先怀疑数据/时区/合约展期，最后再怀疑 OOT 本身。
- **状态恢复**：仿真进程重启后能从 `sim_trades.csv` 重建 open positions、parity log；不能丢历史 diff。
- **不发真单**：sim 用的 gateway 必须是 vnpy 的仿真接口（如 simnow），任何路径上"误打"到真实 broker 会被 [cta/live/kill_switch.py](../live/kill_switch.py) 兜底——但 sim 不应当依赖这个兜底，禁止 import live gateway。
- **测试**：跑 `pytest cta/sim/tests/ -v`；`test_parity_check.py` 必须绿。
- **切实盘前置条件**：连续 5 个交易日 parity_report 全部通过（diff < 0.5%），且无 critical 漂移。

---

## 环境装机（P0-6）

### 系统与 Python

- **OS**：macOS 12+ / Linux x86_64（Windows 也支持但不推荐生产）
- **Python**：3.10+ （本仓库基线 3.13.3）
- **vnpy core**：本仓库已锁定 `4.3.0`（见 [vnpy/__init__.py](../../vnpy/__init__.py) 或 conda env 标记）

### 必装的 vnpy 扩展包

```bash
# CTP gateway（必须）
pip install vnpy_ctp

# CtaStrategy app（策略容器，必须）
pip install vnpy_ctastrategy

# 数据存储 / 数据库（按需）
pip install vnpy_sqlite          # 或 vnpy_mysql / vnpy_mongodb
pip install vnpy_datamanager     # 仅在用 K 线 CSV 导入时
```

> macOS arm64 装 `vnpy_ctp` 可能需要 Rosetta：先在 x86 Python venv 下装；
> Apple Silicon 原生暂未发布稳定 wheel。

### 申请 SimNow 账号（公开仿真）

1. 访问 [http://www.simnow.com.cn](http://www.simnow.com.cn)
2. 注册并获取 **6 位 SimNow userid + password**
3. SimNow 提供两套服务器：
   - **7x24 仿真**（任意时段，行情是历史回放）：`tcp://180.168.146.187:10130 / :10131`
   - **实盘交易日仿真**（与真实交易时段同步）：`tcp://180.168.146.187:10101 / :10111`
4. broker_id 固定为 `"9999"`，auth_code 用 `"0000000000000000"`

### 凭据配置（P0-4）

```bash
cp cta/config/sim_credentials_template.py cta/config/sim_credentials.py
# 编辑 cta/config/sim_credentials.py，把 REPLACE_ME 替换成你的 SimNow 账号
```

`.gitignore` 已经把 `sim_credentials.py` 排除。**永远不要 git add 真实凭据**。

### 网络验证（一键脚本）

```bash
# 4 步装机验证（roadmap §2.1 P0-6 验收门槛）
bash cta/sim/scripts/verify_install.sh
```

脚本检查：
1. Python + vnpy / vnpy_ctastrategy 核心包就位
2. vnpy_ctp 可 import
3. SimNow td/md 端口连通（180.168.146.187:10130/10131）— 不通仅 WARN
4. sim_runner / sim_credentials / contract_resolver 可 import

退出码 `0` = 全过；`1` = 任一硬检查（1/2/4）失败。

### 数据 parity 验证（roadmap §5.1 sim soak 启动门槛）

```bash
# 1 symbol × 1 day × 60min 上 max|diff| < 1e-6
python3 -m cta.sim.feature_parity_sim_soak --symbol RB0 --interval day
# 期望：✓ sim soak parity gate PASSED: 426 columns within 1e-06 tolerance
```

退出码 `0` = parity 通过，可进入 sim soak。

### 验收门槛

| 检查项 | 标准 |
|---|---|
| 装包 | `pip show vnpy vnpy_ctp vnpy_ctastrategy` 都有版本号 |
| 网络 | `nc -zv` SimNow td/md 端口都通 |
| 凭据 | `cta/config/sim_credentials.py` 存在且不在 `git status` |
| 启动 | `python3 -m cta.sim.sim_runner --smoke` 不崩 + 收到 ≥10 个 tick |
| 单测 | `pytest cta/sim/tests/ cta/live/tests/ -q` 全过 |

### 常见问题

| 现象 | 排查 |
|---|---|
| `ImportError: vnpy_ctp not installed` | 见上方"必装"；ARM Mac 需 x86 venv |
| 连接 SimNow 失败 / 超时 | 端口被防火墙挡；切到 7x24 / 实盘交易日服务器对照 |
| `登录失败：错误的用户名或密码` | SimNow 账号是 6 位数字，注意复制空格 |
| `认证失败` | broker_id / app_id / auth_code 必须用 SimNow 默认值 `9999 / simnow_client_test / 0000000000000000` |
| 收到行情但下单 reject | 检查交易时段（7x24 仿真可下；实盘交易日仿真有时段限制） |
| Docker 部署 | 暂无官方 base image，可参考 [VeighNa Studio](https://www.vnpy.com) 自建 |
