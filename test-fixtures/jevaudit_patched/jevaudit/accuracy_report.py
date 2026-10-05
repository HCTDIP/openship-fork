"""accuracy_report.py — Brier Score + 校准曲线 -> calibration_report.md。

Brier = (1/N) * sum((p_i - o_i)^2)，仅统计 outcome in {0,1}（skipped 不计分）。
目标：验证置信度 p 与真实概率 o 的匹配程度，置信度漂移率 <= 0.01。
"""
import statistics

from .ledger import load_ledger

REPORT = "calibration_report.md"


def scorable(rows: list) -> list:
    """可计分条目：p 存在且 outcome in {0,1}。"""
    return [r for r in rows
            if isinstance(r.get("p"), (int, float))
            and r.get("outcome") in (0, 1)]


def brier_score(p_list: list, o_list: list) -> float:
    """Brier Score。p_list/o_list 等长；outcome=None 表示 skipped 不计分。"""
    pairs = [(p, o) for p, o in zip(p_list, o_list) if o in (0, 1)]
    if not pairs:
        raise ValueError("no scorable samples")
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def calibration_curve(p_list: list, o_list: list, buckets: int = 5) -> list:
    """校准曲线：按 p 值等宽分桶，每桶返回 {lo, hi, n, mean_p, mean_o}。"""
    pairs = sorted((p, o) for p, o in zip(p_list, o_list) if o in (0, 1))
    if not pairs:
        return []
    width = 1.0 / buckets
    curve = []
    for i in range(buckets):
        lo, hi = i * width, (i + 1) * width
        seg = [(p, o) for p, o in pairs if lo <= p < hi or (i == buckets - 1 and p == 1.0)]
        if not seg:
            continue
        curve.append({
            "lo": round(lo, 2), "hi": round(hi, 2), "n": len(seg),
            "mean_p": round(statistics.mean(p for p, _ in seg), 4),
            "mean_o": round(statistics.mean(o for _, o in seg), 4),
        })
    return curve


def report(rows: list, out: str = REPORT) -> str:
    """产出 Markdown 报告（难听话逻辑内置：样本不足不下结论）。"""
    scor = scorable(rows)
    lines = ["# Calibration Report (mm-audit-poc)", ""]
    if len(scor) < 3:
        lines.append(f"**样本不足（n={len(scor)} < 3），不下结论。**")
    else:
        b = brier_score([r["p"] for r in scor], [r["outcome"] for r in scor])
        # 对照基线：全猜 0.5 的 Brier（常数 0.25，与结果分布无关）
        base = sum((0.5 - o) ** 2 for o in (r["outcome"] for r in scor)) / len(scor)
        curve = calibration_curve([r["p"] for r in scor], [r["outcome"] for r in scor])
        drift = abs(statistics.mean(r["p"] for r in scor)
                    - statistics.mean(r["outcome"] for r in scor))
        lines += [
            f"- n={len(scor)}  Brier={b:.4f}",
            f"- 对照基线（全猜 0.5）Brier={base:.4f}  {'✅ Jev 优于瞎猜' if b < base else '❌ Jev 不优于瞎猜（≈随机）'}",
            f"- 漂移率 |mean_p - mean_o| = {drift:.4f}  {'✅ <=0.01 达标' if drift <= 0.01 else '❌ >0.01 未达标'}",
            "",
            "| p 桶 | n | mean_p | mean_o |",
            "|---|---|---|---|",
        ]
        for c in curve:
            lines.append(f"| [{c['lo']}, {c['hi']}) | {c['n']} | {c['mean_p']} | {c['mean_o']} |")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out
