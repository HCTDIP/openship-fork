"""ledger.py — 追加写 JSONL 对账账本（原子落盘 + 坏行容忍 + 三指纹）。"""
import hashlib
import json
import os
import sys


def canonical(obj) -> str:
    """规范 JSON：ensure_ascii=False + sort_keys + 紧凑分隔符。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def input_hash(state: str, questions: dict) -> str:
    """指纹 1：sha256(规范化的 state+questions)[:32]。输入给偏了首查它。"""
    return _sha(canonical({"state": state, "questions": questions}))[:32]


def output_hash(response) -> str:
    """指纹 2：sha256(模型原始响应)[:32]。同输入重跑结果变了没（模型漂移）。"""
    return _sha(canonical(response))[:32]


def code_hash(script_path: str = None) -> str:
    """指纹 3：sha256(判定脚本字节)[:12]，src- 前缀。是不是我们改了逻辑。

    采用 0.1.1 语义（2026-09-29 合并）：显式 path 优先；默认取调用方脚本 sys.argv[0]；
    定位不到如实返回 src-unavailable，不假装。（0.1.1 注释写 [:16]，实际代码一直是 [:12]，已更正注释。）
    hctdip.audited_call() 在拿到 src-unavailable 时改哈希实际执行判定的 hctdip.py，并在记录里写明来源。
    """
    p = script_path
    if not p:
        a0 = sys.argv[0] if sys.argv else ""
        if a0.endswith(".py") and os.path.exists(a0):
            p = a0
    if not p:
        return "src-unavailable"
    try:
        with open(p, "rb") as f:
            return "src-" + hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return "src-unavailable"


def add_record(path: str, record: dict) -> None:
    """原子追加一行 JSONL。幂等无害：只追加不重写。"""
    line = canonical(record) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def load_ledger(path: str) -> list:
    """读回账本，坏行容忍（跳过不可解析行，不抛异常）。"""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 坏行容忍
    return rows
