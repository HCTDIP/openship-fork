"""physical_gate.py — 物理任务闸（HCTDIP 层）：与 gate2 置信度层分开。

两层分工，不可互相替代：
- 置信度层 gate2：决定"模型多确定才放行"（p 值分档）
- 物理层 physical_gate：决定"任务允不允许跑"（任务 ID + 代理身份 + 动作落账）

0.2.0 合并说明（源自 0.1.1，原行为不变）：
- log_dir 可配置：参数 > 环境变量 R0T_LOG_DIR > 原默认 /var/minis/shared/logs/r0t
- log_action 新增可选 evidence：传入 hctdip.gate() 的返回值，整条因果记录
  （decision / reason / all_reasons / gate_evidence）一起落盘
"""
import datetime
import json
import os
import sys


def require_task_env(task_var: str = "R0T_TASK_ID", agent_var: str = "R0T_AGENT_ID"):
    """任务无 ID / 代理无身份 → 拒跑（fail-closed）。返回 (task_id, agent_id)。"""
    t = os.environ.get(task_var)
    a = os.environ.get(agent_var)
    if not t or not a:
        print(json.dumps({
            "gate": "physical", "result": "FAIL",
            "missing": [v for v in (task_var, agent_var) if not os.environ.get(v)],
        }))
        sys.exit(2)
    return t, a


DEFAULT_LOG_DIR = "/var/minis/shared/logs/r0t"


def log_action(task_id: str, agent_id: str, action: str,
               log_dir: str = None, evidence: dict = None) -> bool:
    """动作落账：时间戳 + 一行 JSON。目录不可用则静默跳过，不阻塞任务（返回 False）。

    evidence: 可选，hctdip.gate() 的返回值；有则把完整因果记录一起写入。
    """
    log_dir = log_dir or os.environ.get("R0T_LOG_DIR") or DEFAULT_LOG_DIR
    try:
        os.makedirs(log_dir, exist_ok=True)
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "task_id": task_id, "agent_id": agent_id, "action": action,
        }
        if evidence is not None:
            entry["gate"] = {k: evidence.get(k) for k in
                             ("decision", "reason", "all_reasons", "confidence", "gate_evidence")}
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(os.path.join(log_dir, f"{task_id}_{agent_id}.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except OSError:
        return False
