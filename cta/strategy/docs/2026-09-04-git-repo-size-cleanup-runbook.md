# 仓库瘦身 runbook（push 超过 2GB）

诊断时间：2026-09-04　仓库：`git@github.com:wuyuliang/vnpy.git`　分支：`feature`

---

## 一、结论：不是 PNG，是几十个几百 MB 到 2GB 的回测中间产物 CSV

```
.git 总占用            19 GB
  size-pack          14.88 GB   ← 真正的历史
  size-garbage        2.26 GB   ← 23 个 push 失败留下的 tmp_pack_*，可直接回收
  loose objects       1.70 GB
```

历史里 **≥100MB 的对象有 58 个，未压缩合计 22.7 GB**：

| 体积 | 个数 | 路径 |
|---:|---:|---|
| 4.81 GB | 11 | `stock/etf/output` |
| 4.64 GB | 13 | `cta/backtest/20260610_GROUP_POOL_CLUSTER_...` |
| 4.34 GB | 11 | `cta/backtest/20260607_GROUP_POOL_CLUSTER_...` |
| 3.60 GB | 6 | `cta/strategy/brooks/report` |
| 1.39 GB | 5 | `cta/backtest/20260627_GROUP_POOL_CLUSTER_...` |
| 1.21 GB | 2 | `cta/backtest/20260627_GRP_CLUSTER_OTHER_minute30_...` |
| 0.39 GB | 2 | `cta/_xfer` |
| … | | 其余 `cta/backtest/2026*` 若干 |

单个最大的文件：

```
2049.6 MB  cta/strategy/brooks/report/cycle_v1/20260826_.../cycle_snapshots.csv
1121.2 MB  cta/backtest/20260610_.../grp_cluster_other_30min/..._feature_table.csv
1117.4 MB  cta/backtest/20260607_.../grp_cluster_other_30min/..._feature_table.csv
 669.9 MB  stock/etf/output/20260717_full_clean_atr5_adx10_live/daily_candidates.csv
```

**GitHub 单文件硬上限是 100MB。** 那个 2GB 的 `cycle_snapshots.csv` 无论仓库总大小多少都会被拒，
所以这不是"压一压就能过"的问题。

另外还有 **66 万张已跟踪的 PNG**（`stock/report/opportunity_date` 55.9 万张、
`cta/analysis` 4.9 万张、`stock/analysis` 3.7 万张）。体积上不如 CSV 突出，
但会让 clone 和 checkout 极慢，建议一并清掉。

## 二、为什么改 `.gitignore`没用

`.gitignore` **只对尚未被跟踪的文件生效**。已经 `git add` 过的文件会一直被跟踪，
之后再怎么写 ignore 规则都不会让它们离开索引和历史。

现有 `.gitignore` 里其实已经有 `stock/report` 和 `*.csv`，但：

```
575,824 个文件  stock/report      ← 仍在跟踪
 48,960 个文件  cta/analysis
 36,535 个文件  stock/analysis
  1,444 个文件  cta/backtest
    596 个文件  cta/strategy/brooks/report
    564 个文件  stock/etf/output
      6 个文件  cta/_xfer
```

合计约 66.4 万个文件仍被跟踪。规则写对了，但没执行过"停止跟踪"和"清理历史"两步。

## 三、修复

### 第 0 步：立刻回收 2.26 GB 垃圾（安全，不动历史）

```bash
cd /Users/wuyuliang/code/vnpy
git gc --prune=now
```

这只清掉 23 个失败 push 留下的 `tmp_pack_*`。不改任何提交。

### 第 1 步：备份

历史重写不可逆，先留一份。

```bash
cd /Users/wuyuliang/code
cp -a vnpy vnpy_backup_$(date +%Y%m%d)
```

### 第 2 步：停止跟踪（改索引，不改历史）

```bash
cd /Users/wuyuliang/code/vnpy
git rm -r --cached --quiet \
  cta/backtest cta/analysis cta/_xfer \
  cta/strategy/brooks/report cta/strategy/report \
  stock/report stock/analysis stock/etf/output
git commit -m "stop tracking backtest outputs and chart images"
```

确认 `.gitignore` 覆盖这些路径（缺的补上）：

```
cta/backtest/
cta/analysis/
cta/_xfer/
cta/strategy/report/
cta/strategy/brooks/report/
stock/report/
stock/analysis/
stock/etf/output/
```

**做完这一步 push 仍然会失败**——历史里的 15GB 还在。必须继续第 3 步。

### 第 3 步：重写历史（唯一能让 push 成功的办法）

推荐用 `git-filter-repo`，比 `filter-branch` 快几个数量级：

```bash
pip3 install git-filter-repo

cd /Users/wuyuliang/code/vnpy
# 3a. 干掉历史里所有超过 50MB 的对象（不管在哪个路径，代码一律不受影响）
git filter-repo --strip-blobs-bigger-than 50M --force

# 3b. 干掉历史里所有 PNG
git filter-repo --invert-paths --path-glob '*.png' --force
```

用 `--strip-blobs-bigger-than` 而不是列路径，是因为它**按体积**下手，
不会误删 `cta/backtest/` 里真正的回测脚本代码。

`filter-repo` 会移除 remote，重新加回来：

```bash
git remote add origin git@github.com:wuyuliang/vnpy.git
git gc --prune=now --aggressive
git count-objects -vH        # 确认 size-pack 已经降下来
```

### 第 4 步：强推

```bash
git push --force origin feature
```

⚠️ 历史重写会改掉**所有** commit hash。其他机器上的 clone 必须重新 clone，
不能 pull（会产生一堆冲突和重复提交）。

## 四、如果历史无所谓，还有更省事的一条路

只要代码、不要历史：

```bash
cd /Users/wuyuliang/code/vnpy
rm -rf .git
git init && git branch -M feature
git add -A                    # .gitignore 此时生效，输出目录不会进来
git commit -m "clean history: code only"
git remote add origin git@github.com:wuyuliang/vnpy.git
git push --force origin feature
```

十分钟搞定，代价是丢掉全部提交历史。

## 五、以后怎么防

1. **回测产出永远不进 git。** 输出目录一律在 `.gitignore` 里，
   新建输出目录时先确认它被覆盖。
2. **加一个 pre-commit 钩子挡住大文件**：

```bash
cat > .git/hooks/pre-commit <<'HOOK'
#!/bin/sh
limit=$((50*1024*1024))
big=$(git diff --cached --name-only --diff-filter=A | while read -r f; do
  [ -f "$f" ] || continue
  sz=$(wc -c <"$f")
  [ "$sz" -gt "$limit" ] && echo "$f ($((sz/1024/1024))MB)"
done)
if [ -n "$big" ]; then
  echo "拒绝提交：以下文件超过 50MB"; echo "$big"
  echo "回测产出不要进 git；确实需要就用 git lfs。"
  exit 1
fi
HOOK
chmod +x .git/hooks/pre-commit
```

3. 出图默认已改成只保留 `TRADED`（`--chart-outcomes`），
   新的回测一次只产出约 100 张图而不是 1.5 万张。
