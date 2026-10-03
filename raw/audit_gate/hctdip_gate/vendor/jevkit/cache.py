"""决策缓存：同 state + 同 questions + 同模型 → 同一个结论。

两个价值：
1. **省钱**：重复尽调/重复路由 0 成本（真调用 $0.0000119/次）
2. **确定性可感知**：这是 Jev 的核心卖点 —— 同一状态永远给同一结论，
   缓存把这一点变成"看得见、可断言"的行为（相同 key 命中 = 结论字节级一致）

默认**关闭**（不偷偷写磁盘）。开启方式二选一：
    Client(cache_dir="~/.cache/jevkit")          # 显式
    export JEVKIT_CACHE_DIR=~/.cache/jevkit      # 环境变量
"""
import hashlib
import json
import os
import pathlib
import time

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".cache", "jevkit")


def cache_key(model: str, state: str, questions: dict) -> str:
    """稳定 key：与字典顺序无关（sort_keys），与模型绑定（换模型不串味）。"""
    payload = {"model": model, "state": state, "questions": questions}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DecisionCache:
    """极简文件缓存：一个 key → 一个 JSON 文件。无依赖、可人工检查、可 gitignore。"""

    def __init__(self, directory: str = None, ttl: int = 0):
        self.directory = pathlib.Path(os.path.expanduser(directory or DEFAULT_DIR))
        self.ttl = int(ttl or 0)          # 0 = 永不过期
        self.hits = 0
        self.misses = 0

    def path_for(self, key: str) -> pathlib.Path:
        return self.directory / f"{key}.json"

    def get(self, key: str):
        p = self.path_for(key)
        if not p.exists():
            return None
        if self.ttl and time.time() - p.stat().st_mtime > self.ttl:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, value: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.path_for(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path_for(key))   # 原子落盘，避免半截文件

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0,
                "directory": str(self.directory), "ttl": self.ttl}


def cache_from_env():
    """环境变量开启：JEVKIT_CACHE_DIR（可选 JEVKIT_CACHE_TTL 秒）。未设置 → None（关闭）。"""
    d = os.environ.get("JEVKIT_CACHE_DIR")
    if not d:
        return None
    return DecisionCache(d, ttl=int(os.environ.get("JEVKIT_CACHE_TTL", "0") or 0))
