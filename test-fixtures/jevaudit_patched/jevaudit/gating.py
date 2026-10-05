"""gating.py — 三段式置信度门控（用户令门控带）。

    Score >= 0.70  -> KEEP    （自动采用决策，直接出单/调开执行层）
    0.30 <= Score < 0.70 -> CONFIRM（存 fallback log，后置复核）
    Score < 0.30   -> DROP    （直接抛弃，节省算力开销）

注意：与 jevkit 现成 gate()（act/confirm/escalate）不同，本模块是
做市 PoC 专用门控带，不改旧件、不互相污染。
"""
import enum


class GateResult(enum.Enum):
    KEEP = "KEEP"
    CONFIRM = "CONFIRM"
    DROP = "DROP"


ACT = 0.70
DROP_BELOW = 0.30


def gate2(p: float, act: float = ACT, drop_below: float = DROP_BELOW) -> GateResult:
    """置信度门控。p 必须在 [0,1]。"""
    p = float(p)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p out of range: {p}")
    if p >= act:
        return GateResult.KEEP
    if p >= drop_below:
        return GateResult.CONFIRM
    return GateResult.DROP
