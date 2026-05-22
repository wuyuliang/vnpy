# cta/model/tools/

## 主要做什么

**离线辅助小工具**：审计特征穿越、自动 flag 持续亏损品种、生成 causality manifest 种子文件。这些是研究节奏中"偶尔跑、关键时跑"的运维脚本，不进 pipeline 主路径。

## 关键文件

| 文件 | 作用 |
|---|---|
| [leakage_audit.py](leakage_audit.py) | **特征穿越审计**：扫描 training dataset 的所有特征，按"shift / 时间一致性 / future_*"规则报警；上线策略前必跑 |
| [auto_flag_persistent_loss_symbols.py](auto_flag_persistent_loss_symbols.py) | 扫 `cta/report/backtest/*_oot_summary.csv`，找出连续 N 个 OOT 窗口净亏的品种，写到 [cta/config/symbol_disable.py](../../config/symbol_disable.py) 用的 manifest |
| [generate_causality_manifest_seed.py](generate_causality_manifest_seed.py) | 给 [cta/feature/causality_manifest.csv](../../feature/causality_manifest.csv) 生成初始种子条目（新增特征时） |

## 详细过程

### leakage_audit
```bash
python -m cta.model.tools.leakage_audit \
    --pred-csv cta/report/backtest/<run_tag>/<...>_predictions.csv \
    --report-out /tmp/leak_report.md
```
内部逻辑：
1. 按 (symbol, entry_datetime) 把 train / valid / test split 与特征 column join。
2. 对每个 column 跑 sanity check：是否包含 entry_datetime 之后的 bar 信息。
3. 触发规则的 column 输出到 report，附 IC / 重要度，方便核对是否是真穿越。

### auto_flag_persistent_loss_symbols
```bash
python -m cta.model.tools.auto_flag_persistent_loss_symbols \
    --backtest-root cta/report/backtest \
    --min-windows 3 \
    --out cta/config/symbol_disable_manifest.csv
```
- 默认要求"最近 3 个 OOT 窗口连续净亏"才 flag，避免单窗口噪声触发。
- manifest 写完后由 [cta/config/symbol_disable.py](../../config/symbol_disable.py) 在下次跑批时自动 filter。

### generate_causality_manifest_seed
新增特征文件后跑一次，把所有未在 `causality_manifest.csv` 出现的特征列追加成 placeholder 行；之后再人工填"它用了未来信息吗"。

## 注意事项

- **leakage_audit 是上线前 gate**：任何新模型/新策略在 PR / 实盘前都要附 leakage_audit 结果（含 report-out 输出）。
- **auto_flag 不主动改 config**：脚本只写 manifest csv，**人工 review 后**再把 manifest 切到 `cta/config/`。避免误 flag 把活跃品种禁掉。
- **manifest 种子不是终态**：[generate_causality_manifest_seed.py](generate_causality_manifest_seed.py) 输出的是"placeholder 行"，需要人补充每个特征的因果性标注。
- **本目录禁止进 pipeline 主路径**：`pipeline_orchestrator_*.py` 不许 `from cta.model.tools import ...`。这些工具是离线运维，不上生产。
- **没有 tests/ 子目录**：本目录工具的测试散落在 [cta/model/tests/test_auto_flag_persistent_loss_symbols.py](../tests/test_auto_flag_persistent_loss_symbols.py)、[test_leakage_audit.py](../tests/test_leakage_audit.py)，跑 `pytest cta/model/tests/ -k "leakage or auto_flag"` 即可。
