"""jevkit — Python client for the Jev decision model (OpenRouter Decisions API)."""
import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass

from .cache import DecisionCache, cache_from_env, cache_key

API = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"


@dataclass
class Answer:
    """单个问题的回答（校准结果）。"""
    name: str
    type: str                    # noul / choice / score
    noul: float = None           # noul 概率
    choice: str = None           # choice 选中项
    confidence: float = None     # choice/score 置信
    probabilities: dict = None   # choice 分布


class Client:
    """Jev Decisions API client。

    不是聊天模型：state + typed questions → 校准概率，零文本生成。
    """

    def __init__(self, api_key: str = None, model: str = DEFAULT_MODEL,
                 cache_dir: str = None, cache_ttl: int = 0):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("JEV_API_KEY")
        self.model = model
        # 缓存默认关闭；显式传 cache_dir 或设 JEVKIT_CACHE_DIR 才启用
        self.cache = DecisionCache(cache_dir, ttl=cache_ttl) if cache_dir else cache_from_env()

    def cache_stats(self) -> dict:
        """缓存命中统计（未启用时返回 {'enabled': False}）。"""
        return {"enabled": True, **self.cache.stats()} if self.cache else {"enabled": False}

    def decide(self, questions: dict, state: str = "", timeout: int = 30, no_cache: bool = False) -> dict:
        """单次 decisions 调用。questions 是 record: {name: {type, instructions, ...}}。

        启用缓存时：同 model + state + questions → 直接回旧结论（响应多一个 `_cache` 字段：
        "hit" / "miss"），命中不产生任何外呼与费用。
        """
        key = k = None
        if self.cache and not no_cache:
            k = cache_key(self.model, state, questions)
            hit = self.cache.get(k)
            if hit is not None:
                self.cache.hits += 1
                hit = dict(hit)
                hit["_cache"] = "hit"
                return hit
            self.cache.misses += 1
        else:
            key = None
        resp = self._post({"model": self.model, "state": state, "questions": questions}, timeout=timeout)
        if self.cache and not no_cache and k:
            self.cache.put(k, resp)
            out = dict(resp)
            out["_cache"] = "miss"
            return out
        return resp

    def _post(self, payload: dict, timeout: int = 30) -> dict:
        """真正的 HTTP 层（单独抽出：便于测试替换与缓存包裹）。"""
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set.")
        req = urllib.request.Request(
            API,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            # RuntimeError — except Exception 能接住（fallback 友好）
            raise RuntimeError(f"Jev HTTP {e.code}: {err[:300]}")

    def noul(self, name: str, instructions: str, criteria: dict,
             state: str = "", timeout: int = 30) -> float:
        """是/否校准概率。criteria: {"true": str, "false": str}（描述，不是 options）。"""
        r = self.decide(
            {name: {"type": "noul", "instructions": instructions, "criteria": criteria}},
            state=state, timeout=timeout,
        )
        a = r.get("answers", {}).get(name, {})
        if "noul" in a:
            return float(a["noul"])
        raise RuntimeError(f"Jev 响应无 noul: {json.dumps(r)[:200]}")

    def choice(self, name: str, instructions: str, options: list = None,
               state: str = "", timeout: int = 30, criteria: dict = None) -> Answer:
        """挑一。

        ⚠️ API 要求 `criteria`（record）。旧版本方法发的是 `options`，会 **HTTP 400**
        （实测：expected "record"）。现在两种写法都收：传 options 时自动转成
        `{option: option}`；推荐直接传 `criteria={"submit": "能立刻交付", "skip": "不值"}`
        —— 描述越具体，校准越有意义。
        """
        if criteria is None:
            if not options:
                raise ValueError("choice() 需要 criteria（推荐）或 options")
            criteria = {o: o for o in options}
        r = self.decide(
            {name: {"type": "choice", "instructions": instructions, "criteria": criteria}},
            state=state, timeout=timeout,
        )
        a = r.get("answers", {}).get(name, {})
        return Answer(name=name, type="choice", choice=a.get("choice"),
                      confidence=a.get("confidence"), probabilities=a.get("probabilities"))


    def score(self, name: str, instructions: str, anchors: list,
              state: str = "", timeout: int = 30) -> float:
        """量表打分。返回**锚点刻度上的期望值**（不是 0-1！用 normalize_score 归一）。"""
        r = self.decide(
            {name: {"type": "score", "instructions": instructions, "criteria": anchors}},
            state=state, timeout=timeout,
        )
        a = r.get("answers", {}).get(name, {})
        if "score" in a:
            return float(a["score"])
        raise RuntimeError(f"Jev 响应无 score: {json.dumps(r)[:200]}")


def normalize_score(value: float, anchors: list) -> float:
    """锚点刻度 → 0-1（N 个锚点 → /(N-1)）。"""
    n = len(anchors)
    if n < 2:
        raise ValueError("anchors 至少 2 个")
    return max(0.0, min(1.0, float(value) / (n - 1)))


def gate(p: float, act: float = 0.7, confirm: float = 0.5) -> str:
    """confidence-gated act/confirm/escalate。

    act(>=0.7) → 直接执行；confirm(0.5-0.7) → 待审（borderline 会翻转，不自动执行）；
    escalate(<0.5) → 升级/跳过。
    """
    if p >= act:
        return "act"
    if p >= confirm:
        return "confirm"
    return "escalate"
