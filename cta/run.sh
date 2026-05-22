#!/usr/bin/env bash
# CTA 服务器端流水线：按 cta/feature/symbols_research_ranking.csv 取 top-N 品种，
# 串行跑 数据下载 → 校验 → 通用特征 → 候选样本 → 训练（per-symbol 与 POOL 池化）。
#
# 用法
# ----
#   bash cta/run.sh <step>
#
#   step ∈ {data, data_index_bond, validate, feature, candidate, train, pool, group_pool, index_pool, bull_models, summary, all}
#
#   bull_models 是 2026-05-21 后引入的专项 step，覆盖 day / 60min / 30min 三个频率，
#   验证新增 3 个训练模型（bull_regime_strength / trend_persistence /
#   pyramid_eligibility）端到端 fit + OOT。详见 run.md §4.4.5。
#
# 也可以直接 `bash cta/run.sh all` 一把跑完。各步骤通过环境变量调参，缺省覆盖
# run.md 中的推荐值，保持可复现。
#
# 必需环境变量
# ------------
#   TUSHARE_TOKEN  分钟数据下载 token（data 步骤需要）
#
# 可选环境变量（带默认值）
# ----------------------
#   TOP_N            取 ranking 前 N 品种（默认 77，覆盖商品+金融期货）
#   RANKING_CSV      ranking 文件路径
#   INTERVALS_DOWN   下载频率，空格分隔（默认包含全频率）
#   INDEX_BOND_SYMBOLS  指数/国债专项下载品种（默认 IF0 IH0 IC0 IM0 T0 TF0 TS0）
#   INTERVALS_FEAT   特征生成频率（默认 all）
#   INTERVALS_MODEL  训练 / 候选样本频率（默认 day 60min 30min 15min 5min min）
#   START / END      数据范围（默认 2010-01-01 / 当天 YYYY-MM-DD）
#   TRAIN_END        训练截止日（默认 2020-12-31）
#   VALID_END        验证截止日（默认 2023-12-31）
#   TRADE_SIDE       交易方向（默认 both）
#   WORKERS          下载与特征并发数（默认 4）
#   RATE_LIMIT       tushare 每分钟限速（默认 450）
#   RUN_TAG          实验 tag（默认 YYYYMMDD）
#   POOL_INTERVALS   池化训练频率（默认 day 60min；day 单品种样本不足）
#   GROUP_BY         分组池化分组键（默认 tier）
#   GROUP_MIN_SIZE   分组池化最小组样本数（默认 2）
#   GROUP_INTERVALS  分组池化训练频率（默认 day 60min 30min；3 个新模型
#                       bull_regime_strength / trend_persistence /
#                       pyramid_eligibility 会在每个 interval 上自动 fit，
#                       30min 是为了让 hold_extend / pyramid 列在分钟级也有覆盖）
#   BULL_MODELS_INTERVALS  3 个新训练模型专项联动频率（默认 day 60min 30min）
#   INDEX_GROUP_INTERVALS  股指期货专项训练频率（默认 day 60min 30min 15min）
#   USE_PORTFOLIO_LOGIC_RUNTIME  OOT 评估走 portfolio_logic 真实逻辑（默认 0；
#                       设 1 时启用 HTF gate + ranker + trailing + pyramid +
#                       score_calibration + risk_throttle，与 sim/live 一致）
#   INCLUDE_DISABLED 是否包含 symbol_disable_manifest 禁用品种（1=包含，默认 0）
#   LOG_DIR          日志目录（默认 cta/report/run_log/${RUN_TAG}）
#   PYTHON           Python 可执行文件（默认 python3）
#
# 退出码
# ------
#   0 全部成功；非 0 表示中间步骤失败（参考最近一条日志定位）

set -euo pipefail

# ============================================================================
# 配置
# ============================================================================
TOP_N="${TOP_N:-77}"
RANKING_CSV="${RANKING_CSV:-cta/feature/symbols_research_ranking.csv}"
INTERVALS_DOWN="${INTERVALS_DOWN:-day minute60 minute30 minute15 minute5 minute}"
INDEX_BOND_SYMBOLS="${INDEX_BOND_SYMBOLS:-IF0 IH0 IC0 IM0 T0 TF0 TS0}"
INTERVALS_FEAT="${INTERVALS_FEAT:-all}"
INTERVALS_MODEL="${INTERVALS_MODEL:-day 60min 30min 15min 5min min}"
START="${START:-2010-01-01}"
END="${END:-$(date +%Y-%m-%d)}"
TRAIN_END="${TRAIN_END:-2020-12-31}"
VALID_END="${VALID_END:-2023-12-31}"
TRADE_SIDE="${TRADE_SIDE:-both}"
WORKERS="${WORKERS:-4}"
RATE_LIMIT="${RATE_LIMIT:-450}"
RUN_TAG="${RUN_TAG:-$(date +%Y%m%d)}"
GLOBAL_SEED="${GLOBAL_SEED:-${RUN_TAG}}"
POOL_INTERVALS="${POOL_INTERVALS:-day 60min}"
GROUP_BY="${GROUP_BY:-cluster}"
GROUP_MIN_SIZE="${GROUP_MIN_SIZE:-2}"
GROUP_INTERVALS="${GROUP_INTERVALS:-day 60min 30min}"
# 股指期货专项分组训练频率（IF/IH/IC/IM，日内流动性极佳，可加密到 15min）
INDEX_GROUP_INTERVALS="${INDEX_GROUP_INTERVALS:-day 60min 30min 15min}"
# 3 个新训练模型联动专项频率：day 验证 trend filter 主战场（INDEX 2024 bias），
# 60min 验证 mfe_mae / trend_persistence 主战场，30min 验证 pyramid_eligibility 主战场。
BULL_MODELS_INTERVALS="${BULL_MODELS_INTERVALS:-day 60min 30min}"
USE_PORTFOLIO_LOGIC_RUNTIME="${USE_PORTFOLIO_LOGIC_RUNTIME:-0}"
INCLUDE_DISABLED="${INCLUDE_DISABLED:-0}"
LOG_DIR="${LOG_DIR:-cta/report/run_log/${RUN_TAG}}"
PYTHON="${PYTHON:-python3}"

DATA_REPORT_DIR="cta/report/data"

# ============================================================================
# 工具函数
# ============================================================================
log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

require_token() {
    if [[ -z "${TUSHARE_TOKEN:-}" ]]; then
        die "TUSHARE_TOKEN 未设置 (export TUSHARE_TOKEN=...)"
    fi
}

require_ranking() {
    if [[ ! -f "${RANKING_CSV}" ]]; then
        die "ranking 文件不存在: ${RANKING_CSV}"
    fi
}

ensure_dirs() {
    mkdir -p "${LOG_DIR}" "${DATA_REPORT_DIR}"
    export CTA_GLOBAL_SEED="${GLOBAL_SEED}"
}

run_step() {
    # 包装：把每步标准输出 + 错误流同时写到 log 文件 + 控制台
    local name="$1"; shift
    local logfile="${LOG_DIR}/${name}.log"
    log "=> step ${name}"
    log "   logfile: ${logfile}"
    log "   seed: ${CTA_GLOBAL_SEED:-unset}"
    log "   cmd: $*"
    # 用 tee 同时输出，pipefail 保证 python 失败时整体非 0
    "$@" 2>&1 | tee -a "${logfile}"
    local rc=${PIPESTATUS[0]}
    if [[ ${rc} -ne 0 ]]; then
        die "step ${name} failed (rc=${rc})"
    fi
    log "<= step ${name} done"
}

# ============================================================================
# 步骤
# ============================================================================
step_data() {
    require_token
    require_ranking
    ensure_dirs
    log "data: downloading top-${TOP_N} from ${RANKING_CSV}, intervals=[${INTERVALS_DOWN}]"
    # shellcheck disable=SC2086
    run_step "01_data_download" \
        ${PYTHON} -m cta.data_code.download_all \
            --intervals ${INTERVALS_DOWN} \
            --max-rank "${TOP_N}" \
            --workers "${WORKERS}" \
            --rate-limit "${RATE_LIMIT}" \
            --include-financial \
            --include-index \
            --build-macro
}

step_data_index_bond() {
    require_token
    ensure_dirs
    log "data_index_bond: downloading index futures + bond futures + reference index, symbols=[${INDEX_BOND_SYMBOLS}] intervals=[${INTERVALS_DOWN}]"
    # shellcheck disable=SC2086
    run_step "01_data_index_bond_download" \
        ${PYTHON} -m cta.data_code.download_all \
            --intervals ${INTERVALS_DOWN} \
            --only-symbols ${INDEX_BOND_SYMBOLS} \
            --start "${START}" \
            --end "${END}" \
            --workers "${WORKERS}" \
            --rate-limit "${RATE_LIMIT}" \
            --include-financial \
            --include-index \
            --build-macro
}

step_validate() {
    require_ranking
    ensure_dirs
    log "validate: running data integrity check for top-${TOP_N}"
    # 日线
    # shellcheck disable=SC2086
    run_step "02_validate_day" \
        ${PYTHON} -m cta.cli validate \
            --interval day --max-rank "${TOP_N}" \
            --out "${DATA_REPORT_DIR}/${RUN_TAG}_day_validate.csv"
    # 分钟级（按下载频率列表的非 day 项）
    for itv in ${INTERVALS_DOWN}; do
        if [[ "${itv}" == "day" ]]; then continue; fi
        # shellcheck disable=SC2086
        run_step "02_validate_${itv}" \
            ${PYTHON} -m cta.cli validate \
                --interval "${itv}" --max-rank "${TOP_N}" \
                --out "${DATA_REPORT_DIR}/${RUN_TAG}_${itv}_validate.csv"
    done
}

step_feature() {
    require_ranking
    ensure_dirs
    log "feature: building generic features for top-${TOP_N}, intervals=[${INTERVALS_FEAT}]"
    # shellcheck disable=SC2086
    run_step "03_feature" \
        ${PYTHON} -m cta.feature.run_all_features \
            --interval ${INTERVALS_FEAT} \
            --max-rank "${TOP_N}" \
            --workers "${WORKERS}"
}

step_candidate() {
    require_ranking
    ensure_dirs
    log "candidate: building training samples for top-${TOP_N}, intervals=[${INTERVALS_MODEL}]"
    # shellcheck disable=SC2086
    run_step "04_candidate" \
        ${PYTHON} -m cta.model.feature.candidate_training_dataset \
            --top-n-symbols "${TOP_N}" \
            --symbols-ranking-path "${RANKING_CSV}" \
            --interval ${INTERVALS_MODEL} \
            --start "${START}" --end "${END}" \
            --trade-side-mode "${TRADE_SIDE}" \
            --run-tag "${RUN_TAG}"
}

step_train() {
    require_ranking
    ensure_dirs
    log "train: per-symbol model_pipeline for top-${TOP_N}, intervals=[${INTERVALS_MODEL}]"
    # shellcheck disable=SC2086
    run_step "05_train_per_symbol" \
        ${PYTHON} -m cta.model.model_pipeline \
            --top-n-symbols "${TOP_N}" \
            --symbols-ranking-path "${RANKING_CSV}" \
            --interval ${INTERVALS_MODEL} \
            --start "${START}" --end "${END}" \
            --train-end "${TRAIN_END}" --valid-end "${VALID_END}" \
            --window-mode expanding \
            --max-walk-forward-windows 3 \
            --by-signal-type \
            --generic-mode auto \
            --seed "${GLOBAL_SEED}"
}

step_pool() {
    require_ranking
    ensure_dirs
    log "pool: pooled multi-symbol model_pipeline for top-${TOP_N}, intervals=[${POOL_INTERVALS}]"
    # shellcheck disable=SC2086
    run_step "06_train_pool" \
        ${PYTHON} -m cta.model.model_pipeline \
            --top-n-symbols "${TOP_N}" \
            --symbols-ranking-path "${RANKING_CSV}" \
            --interval ${POOL_INTERVALS} \
            --start "${START}" --end "${END}" \
            --train-end "${TRAIN_END}" --valid-end "${VALID_END}" \
            --window-mode expanding \
            --max-walk-forward-windows 3 \
            --by-signal-type \
            --generic-mode auto \
            --pool \
            --seed "${GLOBAL_SEED}"
}

step_group_pool() {
    require_ranking
    ensure_dirs
    log "group_pool: grouped pooled model_pipeline by ${GROUP_BY}, intervals=[${GROUP_INTERVALS}] use_pl_runtime=${USE_PORTFOLIO_LOGIC_RUNTIME}"
    local include_flag=()
    if [[ "${INCLUDE_DISABLED}" == "1" ]]; then
        include_flag+=(--include-disabled-symbols)
    fi
    local pl_runtime_flag=()
    if [[ "${USE_PORTFOLIO_LOGIC_RUNTIME}" == "1" ]]; then
        pl_runtime_flag+=(--use-portfolio-logic-runtime)
    fi
    # shellcheck disable=SC2086
    run_step "06_train_group_pool" \
        ${PYTHON} -m cta.model.model_pipeline \
            --group-pool \
            --group-by "${GROUP_BY}" \
            --group-min-size "${GROUP_MIN_SIZE}" \
            --symbols-ranking-path "${RANKING_CSV}" \
            --top-n-symbols "${TOP_N}" \
            --interval ${GROUP_INTERVALS} \
            --start "${START}" --end "${END}" \
            --train-end "${TRAIN_END}" --valid-end "${VALID_END}" \
            --window-mode expanding \
            --max-walk-forward-windows 3 \
            --by-signal-type \
            --generic-mode auto \
            --min-used-symbols 2 \
            "${include_flag[@]}" \
            "${pl_runtime_flag[@]}" \
            --seed "${GLOBAL_SEED}"
}

step_index_pool() {
    # 仅训练股指期货 group（IF0/IH0/IC0/IM0 → cluster_index）
    # 适合快速验证 cluster_index 端到端：训练 → 注册 → OOT → sim/live
    require_ranking
    ensure_dirs
    log "index_pool: training cluster_index ONLY (IF0/IH0/IC0/IM0), intervals=[${INDEX_GROUP_INTERVALS}] use_pl_runtime=${USE_PORTFOLIO_LOGIC_RUNTIME}"
    local include_flag=()
    if [[ "${INCLUDE_DISABLED}" == "1" ]]; then
        include_flag+=(--include-disabled-symbols)
    fi
    local pl_runtime_flag=()
    if [[ "${USE_PORTFOLIO_LOGIC_RUNTIME}" == "1" ]]; then
        pl_runtime_flag+=(--use-portfolio-logic-runtime)
    fi
    # shellcheck disable=SC2086
    run_step "06_train_index_pool" \
        ${PYTHON} -m cta.model.model_pipeline \
            --group-pool \
            --group-by cluster \
            --group-min-size 2 \
            --only-clusters index \
            --symbols-ranking-path "${RANKING_CSV}" \
            --top-n-symbols "${TOP_N}" \
            --interval ${INDEX_GROUP_INTERVALS} \
            --start "${START}" --end "${END}" \
            --train-end "${TRAIN_END}" --valid-end "${VALID_END}" \
            --window-mode expanding \
            --max-walk-forward-windows 3 \
            --by-signal-type \
            --generic-mode auto \
            --min-used-symbols 2 \
            "${include_flag[@]}" \
            "${pl_runtime_flag[@]}" \
            --seed "${GLOBAL_SEED}"
}

step_bull_models() {
    # 专项验证 2026-05-21 引入的 3 个新训练模型在 day / 60min / 30min 三个频率上端到端：
    #   - BullRegimeStrengthModel   → bull_strength_score / bull_mode
    #   - TrendPersistenceModel     → hold_extend_score / recommended_horizon_extension_bars
    #   - PyramidEligibilityModel   → pyramid_add_score / pyramid_size_mult
    # 每个 interval 跑一次 group_pool + portfolio_logic_runtime（强制开启，
    # 因为 hold_extend / pyramid_size_mult 列只在 portfolio_logic 路径里被消费），
    # 然后跑 4.4.4 的快速核对脚本确认 3 个模型的输出列都进入 predictions.csv。
    require_ranking
    ensure_dirs
    log "bull_models: training+OOT with 3 new models on intervals=[${BULL_MODELS_INTERVALS}]"
    local include_flag=()
    if [[ "${INCLUDE_DISABLED}" == "1" ]]; then
        include_flag+=(--include-disabled-symbols)
    fi
    for itv in ${BULL_MODELS_INTERVALS}; do
        log "bull_models[${itv}]: starting"
        # shellcheck disable=SC2086
        run_step "06_train_bull_models_${itv}" \
            ${PYTHON} -m cta.model.model_pipeline \
                --group-pool \
                --group-by cluster \
                --group-min-size 2 \
                --symbols-ranking-path "${RANKING_CSV}" \
                --top-n-symbols "${TOP_N}" \
                --interval "${itv}" \
                --start "${START}" --end "${END}" \
                --train-end "${TRAIN_END}" --valid-end "${VALID_END}" \
                --window-mode expanding \
                --max-walk-forward-windows 3 \
                --by-signal-type \
                --generic-mode auto \
                --use-portfolio-logic-runtime \
                --min-used-symbols 2 \
                "${include_flag[@]}" \
                --seed "${GLOBAL_SEED}"
        log "bull_models[${itv}]: verifying new-model columns in predictions.csv"
        ${PYTHON} - <<'PY' "${itv}"
import sys
from pathlib import Path
import pandas as pd

itv = sys.argv[1]
runs = sorted(Path("cta/report/backtest").glob(f"*_GRP_*_{itv}_*model_pipeline"))
if not runs:
    print(f"[bull_models verify][{itv}] no run dir matched")
    sys.exit(0)
run_dir = runs[-1]
pred_files = list(run_dir.glob("*_predictions.csv"))
if not pred_files:
    print(f"[bull_models verify][{itv}] no predictions.csv in {run_dir.name}")
    sys.exit(0)
pred = pd.read_csv(pred_files[0])
must = [
    "bull_strength_score", "bull_mode",
    "hold_extend_score", "recommended_horizon_extension_bars",
    "pyramid_add_score", "pyramid_size_mult",
]
present = [c for c in must if c in pred.columns]
missing = [c for c in must if c not in pred.columns]
print(f"[bull_models verify][{itv}] run={run_dir.name}")
print(f"  present: {present}")
if missing:
    print(f"  MISSING: {missing}  -> 3 new models pipeline broken at this interval")
    sys.exit(1)
print(f"  ok ({len(present)}/{len(must)} columns)")
PY
        log "bull_models[${itv}]: done"
    done
}

step_summary() {
    log "summary: locating latest model_pipeline outputs"
    if compgen -G "cta/report/backtest/${RUN_TAG}_*model_pipeline" > /dev/null; then
        ls -d cta/report/backtest/${RUN_TAG}_*model_pipeline 2>/dev/null | sort | tee "${LOG_DIR}/07_summary.txt"
    else
        log "no model_pipeline directory matched ${RUN_TAG}_*"
    fi
    log "logs:    ${LOG_DIR}/"
    log "reports: cta/report/backtest/"
}

run_all() {
    step_data
    step_validate
    step_feature
    step_candidate
    step_train
    step_pool
    step_group_pool
    step_summary
}

# ============================================================================
# 入口
# ============================================================================
ACTION="${1:-all}"
case "${ACTION}" in
    data)        step_data ;;
    data_index_bond) step_data_index_bond ;;
    validate)    step_validate ;;
    feature)     step_feature ;;
    candidate)   step_candidate ;;
    train)       step_train ;;
    pool)        step_pool ;;
    group_pool)  step_group_pool ;;
    index_pool)  step_index_pool ;;
    bull_models) step_bull_models ;;
    summary)     step_summary ;;
    all)         run_all ;;
    -h|--help|help)
        sed -n '2,40p' "$0"
        exit 0
        ;;
    *)
        die "unknown action: ${ACTION} (try -h)"
        ;;
esac

log "all done"
