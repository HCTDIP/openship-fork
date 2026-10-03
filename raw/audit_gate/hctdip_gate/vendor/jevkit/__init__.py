"""jevkit package — Jev decision model client."""
from .client import Client, Answer, gate

__all__ = ['Client', 'Answer', 'gate']
__version__ = '0.1.0'

# 让 README 里写的用法真的能 import（此前 normalize_score 只能从 jevkit.client 取，用户会踩）
from .client import Answer, Client, gate, normalize_score  # noqa: E402,F401
from .cache import DecisionCache, cache_key  # noqa: E402,F401

__all__ = ["Client", "Answer", "gate", "normalize_score", "DecisionCache", "cache_key"]
