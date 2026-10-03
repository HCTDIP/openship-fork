"""决策缓存 + choice/score 修复的离线测试（不出网）。"""
import json
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from jevkit.cache import DecisionCache, cache_key  # noqa: E402
from jevkit.client import Client, Answer, normalize_score  # noqa: E402


class CountingClient(Client):
    """把 HTTP 层换成计数器 —— 缓存命中与否看得见，且零外呼。"""

    def __init__(self, **kw):
        super().__init__(api_key="test-key", **kw)
        self.calls = []

    def _post(self, payload, timeout=30):
        self.calls.append(payload)
        return {"answers": {n: {"type": "noul", "noul": 0.42} for n in payload["questions"]},
                "usage": {"cost": 1.19e-05}}


Q = {"worth_it": {"type": "noul", "instructions": "worth it?",
                  "criteria": {"true": "yes", "false": "no"}}}


def test_cache_key_is_order_insensitive_and_model_scoped():
    a = cache_key("m1", "s", {"x": {"t": 1}, "y": {"t": 2}})
    b = cache_key("m1", "s", {"y": {"t": 2}, "x": {"t": 1}})   # 键序不同
    c = cache_key("m2", "s", {"x": {"t": 1}, "y": {"t": 2}})   # 模型不同
    assert a == b and a != c


def test_second_identical_call_hits_cache(tmp_path):
    c = CountingClient(cache_dir=str(tmp_path))
    r1 = c.decide(Q, state="same state")
    r2 = c.decide(Q, state="same state")
    assert r1["_cache"] == "miss" and r2["_cache"] == "hit"
    assert len(c.calls) == 1                      # 只外呼一次 = 省钱
    assert r2["answers"]["worth_it"]["noul"] == 0.42   # 同状态同结论（字节级一致）
    assert c.cache_stats()["hits"] == 1 and c.cache_stats()["hit_rate"] == 0.5


def test_different_state_or_questions_misses(tmp_path):
    c = CountingClient(cache_dir=str(tmp_path))
    c.decide(Q, state="state A")
    c.decide(Q, state="state B")                  # 状态不同 → 必须重算
    c.decide({"other": {"type": "noul", "instructions": "x", "criteria": {"true": "a", "false": "b"}}},
             state="state A")                     # 问题不同 → 必须重算
    assert len(c.calls) == 3


def test_no_cache_bypass(tmp_path):
    c = CountingClient(cache_dir=str(tmp_path))
    c.decide(Q, state="s")
    c.decide(Q, state="s", no_cache=True)
    assert len(c.calls) == 2


def test_cache_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("JEVKIT_CACHE_DIR", raising=False)
    c = CountingClient()
    c.decide(Q, state="s")
    c.decide(Q, state="s")
    assert len(c.calls) == 2 and c.cache_stats() == {"enabled": False}


def test_env_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("JEVKIT_CACHE_DIR", str(tmp_path / "envcache"))
    c = CountingClient()
    c.decide(Q, state="s")
    c.decide(Q, state="s")
    assert len(c.calls) == 1 and c.cache_stats()["enabled"] is True


def test_ttl_expiry(tmp_path):
    cache = DecisionCache(str(tmp_path), ttl=1)
    cache.put("k", {"v": 1})
    assert cache.get("k") == {"v": 1}
    import os, time
    old = time.time() - 10
    os.utime(cache.path_for("k"), (old, old))
    assert cache.get("k") is None                 # 过期即视为未命中


def test_choice_uses_criteria_not_options(tmp_path):
    """回归：旧版 choice() 发 `options` → HTTP 400（实测 expected "record"）。"""
    c = CountingClient(cache_dir=str(tmp_path))
    c.choice("next_step", "what next?", options=["submit", "skip"], state="s")
    sent = c.calls[0]["questions"]["next_step"]
    assert "criteria" in sent and "options" not in sent
    assert sent["criteria"] == {"submit": "submit", "skip": "skip"}
    c.choice("n2", "what next?", criteria={"go": "立刻能交付"}, state="s")
    assert c.calls[1]["questions"]["n2"]["criteria"] == {"go": "立刻能交付"}


def test_choice_requires_something(tmp_path):
    c = CountingClient(cache_dir=str(tmp_path))
    with pytest.raises(ValueError):
        c.choice("n", "i", state="s")


def test_normalize_score():
    assert normalize_score(4, ["1", "2", "3", "4", "5"]) == 1.0
    assert normalize_score(1.34, ["1", "3", "5"]) == pytest.approx(0.67, abs=0.01)
    assert normalize_score(99, ["1", "2"]) == 1.0     # 越界夹紧
    with pytest.raises(ValueError):
        normalize_score(1, ["only-one"])
