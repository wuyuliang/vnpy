"""Pytest conftest for cta/ — filter known-noise warnings (codex P2-F).

为什么需要这个文件
------------------
codex 2026-05-23 review §[P2] 指出全量测试 warnings 累计 ~1934 条，主要来源：
- sklearn 1.8+ ``FutureWarning: 'penalty' was deprecated``
- sklearn ``RuntimeWarning: invalid value encountered in matmul``
- sklearn ``ConvergenceWarning: lbfgs failed to converge``
- pandas4 ``Pandas4Warning: 'd' is deprecated``

这些是依赖库已知噪音，不影响业务正确性。本 conftest 把它们**降级为
``filterwarnings='ignore'``**，让真正的业务 warnings 浮出来。

后续治理路径（roadmap §2.4 P2-F 后续）：
- 给 final_decision_model 改成新版 ``l1_ratio`` API 而非 ``penalty='elasticnet'``
- 给 trade_filter_model 加 ``ConvergenceWarning`` 时 fallback 简单模型
- 数据预处理过滤 NaN/inf 避免 matmul 警告
"""
from __future__ import annotations

import warnings


def pytest_configure(config) -> None:  # noqa: ANN001
    """Pytest hook：在测试 session 开始前注册 warning filter。"""
    _silence_known_dependency_noise()


def _silence_known_dependency_noise() -> None:
    # sklearn FutureWarning: LogisticRegression penalty deprecated
    warnings.filterwarnings(
        "ignore",
        category=FutureWarning,
        message=r".*penalty.*deprecated.*",
    )
    # sklearn RuntimeWarning: matmul invalid value / overflow / divide by zero
    warnings.filterwarnings(
        "ignore",
        category=RuntimeWarning,
        message=r".*encountered in matmul.*",
    )
    warnings.filterwarnings(
        "ignore",
        category=RuntimeWarning,
        message=r".*encountered in scalar.*",
    )
    # sklearn ConvergenceWarning（不通过 import 触发；用 message 匹配）
    warnings.filterwarnings(
        "ignore",
        message=r".*ConvergenceWarning.*",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*lbfgs failed to converge.*",
    )
    # pandas4 Pandas4Warning（频率别名 'd' → 'D'）
    warnings.filterwarnings(
        "ignore",
        message=r".*'d' is deprecated.*",
    )
    # vnpy 中文 print（早期 import 触发的）
    warnings.filterwarnings(
        "ignore",
        category=DeprecationWarning,
        module=r"vnpy\..*",
    )
