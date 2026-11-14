"""Routing evaluation: runs labelled questions through the real chat pipeline and scores routing accuracy.

Used by tests/test_routing_eval.py (CI, mock LLM) and scripts/eval_routing.py (Vertex when LLM_MODE=vertex).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from container import Providers, build_container
from domain.ports import UserContext
from settings import Settings


@dataclass
class CaseResult:
    question: str
    passed: bool
    expected: list[str]
    used: list[str]
    skills: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class EvalReport:
    results: list[CaseResult]
    skipped: int

    @property
    def accuracy(self) -> float:
        return 100.0 * sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]


def _check(case: dict, used: list[str], skills: list[str]) -> str:
    want, forbid = set(case.get("agents", [])), set(case.get("forbid", []))
    if not want <= set(used):
        return f"missing agents {sorted(want - set(used))}"
    if set(used) & forbid:
        return f"forbidden agents used {sorted(set(used) & forbid)}"
    if case.get("exact") and set(used) != want:
        return f"expected exactly {sorted(want)}"
    missing = set(case.get("skills", [])) - set(skills)
    if missing:
        return f"skills not selected {sorted(missing)}"
    return ""


async def run_eval(settings: Settings, cases_file: str | Path) -> EvalReport:
    data = json.loads(Path(cases_file).read_text(encoding="utf-8"))
    chat = build_container(settings, Providers(settings)).chat
    live_llm = settings.llm_mode != "mock"
    results: list[CaseResult] = []
    skipped = 0
    for case in data["cases"]:
        if case.get("tier") == "llm" and not live_llm:
            skipped += 1
            continue
        ctx = UserContext("eval", "eval", frozenset(case.get("roles", ["Platform.Admin"])))
        res = await chat.execute(case["q"], ctx)
        reason = _check(case, res.agents_used, res.skills_selected)
        results.append(CaseResult(case["q"], not reason, case.get("agents", []), res.agents_used,
                                  res.skills_selected, reason))
    return EvalReport(results, skipped)
