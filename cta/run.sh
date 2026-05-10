#!/usr/bin/env bash
# CTA 服务器端流水线：按 cta/feature/symbols_research_ranking.csv 取 top-N 品种，
# 串行跑 数据下载 → 校验 → 通用特征 → 候选样本 → 训练（per-symbol 与 POOL 池化）。
#
# 用法
# ----
#   bash cta/run.sh <step>
#
#   step ∈ {data, validate, feature, candidate, train, pool, all}
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
#   TOP_N            取 ranking 前 N 品种（默认 18）
#   RANKING_CSV      ranking 文件路径
#   INTERVALS_DOWN   下载频率，空格分隔（默认包含全频率）
#   INTERVALS_FEAT   特征生成频率（默认 all）
#   INTERVALS_MODEL  训练 / 候选样本频率（默认 day 60min 30min 15min 5min min）
#   START / END      数据范围（默认 2010-01-01 / 2025-12-31）
#   TRAIN_END        训练截止日（默认 2020-12-31）
#   VALID_END        验证截止日（默认 2023-12-31）
#   TRADE_SIDE       交易方向（默认 both）
#   WORKERS          下载与特征并发数（默认 4）
#   RATE_LIMIT       tushare 每分钟限速（默认 450）
#   RUN_TAG          实验 tag（默认 YYYYMMDD）
#   POOL_INTERVALS   池化训练频率（默认 day 60min；day 单品种样本不足）
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
TOP_N="${TOP_N:-18}"
RANKING_CSV="${RANKING_CSV:-cta/feature/symbols_research_ranking.csv}"
INTERVALS_DOWN="${INTERVALS_DOWN:-day minute60 minute30 minute15 minute5 minute}"
INTERVALS_FEAT="${INTERVALS_FEAT:-all}"
INTERVALS_MODEL="${INTERVALS_MODEL:-day 60min 30min 15min 5min min}"
START="${START:-2010-01-01}"
END="${END:-2025-12-31}"
TRAIN_END="${TRAIN_END:-2020-12-31}"
VALID_END="${VALID_END:-2023-12-31}"
TRADE_SIDE="${TRADE_SIDE:-both}"
WORKERS="${WORKERS:-4}"
RATE_LIMIT="${RATE_LIMIT:-450}"
RUN_TAG="${RUN_TAG:-$(date +%Y%m%d)}"
POOL_INTERVALS="${POOL_INTERVALS:-day 60min}"
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
}

run_step() {
    # 包装：把每步标准输出 + 错误流同时写到 log 文件 + 控制台
    local name="$1"; shift
    local logfile="${LOG_DIR}/${name}.log"
    log "=> step ${name}"
    log "   logfile: ${logfile}"
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
            --rate-limit "${RATE_LIMIT}"
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
            --generic-mode auto
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
            --pool
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
    step_summary
}

# ============================================================================
# 入口
# ============================================================================
ACTION="${1:-all}"
case "${ACTION}" in
    data)        step_data ;;
    validate)    step_validate ;;
    feature)     step_feature ;;
    candidate)   step_candidate ;;
    train)       step_train ;;
    pool)        step_pool ;;
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
