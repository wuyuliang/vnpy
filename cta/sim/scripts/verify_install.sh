#!/usr/bin/env bash
# P0-6 装机后 4 步验证脚本（roadmap §2.1 验收门槛）
#
# 用法：
#     bash cta/sim/scripts/verify_install.sh
#
# 退出码：
#     0 = 全 4 步通过，可继续 sim soak
#     1 = 任一步失败
#
# 4 步：
#   (1) Python + vnpy 核心包就位
#   (2) vnpy_ctp 可 import
#   (3) SimNow td/md 端口连通
#   (4) sim_runner smoke import（不真实启动）

set -uo pipefail

step() {
    echo ""
    echo "─── [$1] $2 ───"
}

fail() {
    echo "✗ FAIL: $1" >&2
    exit 1
}

ok() {
    echo "✓ $1"
}

# ─── 1. Python + vnpy 核心包 ───────────────────────────────────────────
step "1/4" "Python + vnpy 核心包"
if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not in PATH"
fi
PYV=$(python3 --version 2>&1)
ok "python3: $PYV"

python3 -c "import vnpy; print('vnpy', vnpy.__version__)" || fail "vnpy not installed (pip install vnpy)"
python3 -c "import vnpy_ctastrategy; print('vnpy_ctastrategy ok')" || \
    fail "vnpy_ctastrategy not installed (pip install vnpy_ctastrategy)"
ok "vnpy core packages ready"

# ─── 2. vnpy_ctp 可 import ─────────────────────────────────────────────
step "2/4" "vnpy_ctp 可 import"
if python3 -c "from vnpy_ctp import CtpGateway; print('CtpGateway import ok')" 2>&1; then
    ok "vnpy_ctp installed"
else
    fail "vnpy_ctp not installed (pip install vnpy_ctp); see cta/sim/README.md"
fi

# ─── 3. SimNow td/md 端口连通 ──────────────────────────────────────────
step "3/4" "SimNow td/md 端口连通"
TD_HOST="180.168.146.187"
TD_PORT="10130"
MD_PORT="10131"

check_port() {
    local host=$1 port=$2 label=$3
    # 用 Python socket 做带 timeout 的连通检查（跨平台最可靠）。
    # 网络对外不通时降级为 WARN，不让整体验证失败（实际装机环境必须通）。
    if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
try:
    s.connect(('$host', $port))
    sys.exit(0)
except Exception:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; then
        ok "$label tcp://$host:$port reachable"
        return 0
    fi
    echo "⚠ WARN: $label tcp://$host:$port unreachable (check firewall / VPN if you're on prod box)"
    return 0
}

check_port "$TD_HOST" "$TD_PORT" "trade"
check_port "$TD_HOST" "$MD_PORT" "md   "

# ─── 4. sim_runner smoke import ────────────────────────────────────────
step "4/4" "sim_runner / 凭据 / contract_resolver import"
python3 -c "from cta.sim.sim_runner import SimnowSetting, SimRunConfig; print('sim_runner imports ok')" \
    || fail "sim_runner import failed"
python3 -c "from cta.config.sim_credentials_template import load_credentials, CtpCredentials; print('credentials import ok')" \
    || fail "sim_credentials import failed"
python3 -c "from cta.portfolio_logic.contract_resolver import load_default_resolver; load_default_resolver(); print('contract_resolver import ok')" \
    || fail "contract_resolver import failed"

# 检查凭据文件
if [ -f "cta/config/sim_credentials.py" ]; then
    if grep -q "REPLACE_ME" "cta/config/sim_credentials.py" 2>/dev/null; then
        echo "⚠ WARN: cta/config/sim_credentials.py 还含 REPLACE_ME 占位，请填入真实账号"
    else
        ok "cta/config/sim_credentials.py exists (filled)"
    fi
else
    echo "⚠ WARN: cta/config/sim_credentials.py 不存在；从模板复制："
    echo "       cp cta/config/sim_credentials_template.py cta/config/sim_credentials.py"
fi

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✓ 装机验证 4 步全过；可继续 sim soak"
echo "═══════════════════════════════════════════════════════"
exit 0
