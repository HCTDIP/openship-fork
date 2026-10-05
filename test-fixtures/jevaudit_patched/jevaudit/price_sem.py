"""price_sem.py — price 列语义修正（动手派根因报告落地）。

核心规则：price 列是截断/舍入后的展示价（残差恒 < 0.0125），
usdcSize 是精确执行金额。**price 列严禁精确校验**：
- 真执行价 = usdcSize / size（implied price）
- 校验用 tick 级容差（0.01/0.001）或残差结构检查
"""
BAND = 0.0125  # 实测截断带（残差峰值钉在 +0.0125）


def implied_price(size: float, usdc_size: float) -> float:
    """真执行价 = usdcSize / size。别再用展示价当真值。"""
    return usdc_size / size


def residual_check(price: float, implied: float, band: float = BAND) -> str:
    """残差结构三态：ok / reverse（SELL 侧，标注不判死）/ out_of_band。"""
    d = implied - price
    if d < 0:
        return "reverse"
    if d < band:
        return "ok"
    return "out_of_band"


def match_tick(price: float, implied: float, tick: float = 0.01) -> bool:
    """tick 级容差校验选项。"""
    return abs(implied - price) <= tick
