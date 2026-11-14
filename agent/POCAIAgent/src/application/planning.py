"""Dependency-aware planning: decides which extra agents are needed and the order they run in."""
from __future__ import annotations

import re
from itertools import groupby
from typing import Sequence

from domain.ports import Agent

ID_PATTERNS = {"machine_id": re.compile(r"\bM-\d+\b", re.I), "customer_id": re.compile(r"\bC-\d+\b", re.I)}
MAX_FANOUT = 10


def find_ids(kind: str, text: str) -> list[str]:
    pattern = ID_PATTERNS.get(kind)
    return list(dict.fromkeys(m.upper() for m in pattern.findall(text))) if pattern else []


def _attr(agent: Agent, name: str, default=()):
    return getattr(agent, name, default)


def plan_agents(selected: Sequence[Agent], available: Sequence[Agent], message: str) -> list[list[Agent]]:
    """Add producer agents for ids the question does not contain, then group agents into ordered stages.

    Agents in the same stage are independent and run in parallel; stages run one after another.
    """
    plan = list(selected)
    i = 0
    while i < len(plan):
        agent = plan[i]
        i += 1
        consumes = _attr(agent, "consumes")
        if not consumes or any(find_ids(k, message) for k in consumes):
            continue
        for producer in available:
            if producer not in plan and set(_attr(producer, "produces")) & set(consumes):
                plan.append(producer)
    plan.sort(key=lambda a: _attr(a, "order", 0))
    return [list(g) for _, g in groupby(plan, key=lambda a: _attr(a, "order", 0))]


def fanout_ids(agent: Agent, message: str, discovered: dict[str, list[str]]) -> list[str]:
    """Ids found by earlier stages that this agent should be run against (empty = run once as-is)."""
    consumes = _attr(agent, "consumes")
    if not consumes or any(find_ids(k, message) for k in consumes):
        return []
    ids = [i for k in consumes for i in discovered.get(k, [])]
    return list(dict.fromkeys(ids))[:MAX_FANOUT]
