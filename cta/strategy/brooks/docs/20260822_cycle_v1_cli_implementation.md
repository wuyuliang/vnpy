# Cycle V1 独立 CLI 实施计划

1. 增加周期值对象与解析器，验证分钟/小时单位及长中短严格降序。
2. 增加动态日线 EMA 品种池，覆盖前一日生效、prefix invariance 和换月重置测试。
3. 扩展只读 scalp adapter，发现根品种并加载标准化真实合约分钟数据与历史成本字段。
4. 增加 cycle 扫描流水线：完整周期聚合、因果特征、市场周期分类、完成快照 as-of 对齐和 EMA 过滤。
5. 增加独立 runner，输出审计 CSV、严格 summary/report，并记录执行元数据缺口。
6. 更新主策略文档中的命令和边界，运行 cycle_v1、legacy scalp、ruff、compile 和真实数据冒烟检查。
