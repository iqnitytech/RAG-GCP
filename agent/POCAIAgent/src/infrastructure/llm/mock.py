"""Deterministic offline LLM stand-in (heuristics). Lets the whole stack run without Vertex AI."""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from domain.ports import Agent, SkillInfo, ToolCall, ToolSpec

_SELECT_STOP = {"the", "and", "for", "with", "all", "show", "get", "list", "give", "tell", "about", "what",
                "which", "how", "are", "any", "its", "their", "please", "info", "information", "details",
                "data", "named", "called", "have", "has", "does", "own", "from"}


def _select_words(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", text.lower()) if len(t) >= 3 and t not in _SELECT_STOP]


def _select_tokens(text: str) -> set[str]:
    return set(_select_words(text))


def _tok_match(token: str, word: str) -> bool:
    return word.startswith(token) or (len(word) >= 4 and token.startswith(word))

_ID_PATTERNS = {"machine_id": r"\bM-\d+\b", "customer_id": r"\bC-\d+\b"}
_NAME_PATTERNS = (
    r"[\"']([^\"']+)[\"']",
    r"\b(?:named|called|name|matching)\s+([A-Za-z0-9][A-Za-z0-9 ]*?)(?=\s+(?:and|with|have|has)\b|[?.,]|$)",
)
_STOP = {"the", "a", "an", "of", "for", "is", "are", "what", "which", "show", "get", "list", "me", "and",
         "their", "its", "to", "in", "on", "by", "with", "all", "details", "info", "information"}


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def _extract(param: str, message: str) -> Any | None:
    if param in _ID_PATTERNS:
        m = re.search(_ID_PATTERNS[param], message, re.I)
        return m.group(0).upper() if m else None
    if param == "name":
        for pat in _NAME_PATTERNS:
            m = re.search(pat, message, re.I)
            if m:
                return m.group(1).strip()
        m = re.search(r"(?<!^)\b([A-Z][a-z]+(?: [A-Z][a-z]+)*)", message)
        return m.group(1) if m else None
    return None


class MockLLM:
    async def pick_tool(self, message: str, tools: Sequence[ToolSpec]) -> ToolCall | None:
        msg_words = _words(message)
        best: tuple[int, ToolCall] | None = None
        for tool in tools:
            props = tool.input_schema.get("properties", {})
            required = tool.input_schema.get("required", list(props))
            args = {p: _extract(p, message) for p in required}
            if any(v is None for v in args.values()):
                continue
            score = len(msg_words & _words(tool.name.replace("_", " ") + " " + tool.description))
            if best is None or score > best[0]:
                best = (score, ToolCall(tool.name, args))
        return best[1] if best else None

    async def choose_agents(self, message: str, agents: Sequence[Agent]) -> list[str]:
        msg_words = _words(message)
        return [a.name for a in agents if msg_words & _words(a.name + " " + a.description)]

    async def select_skills(self, message: str, skills: Sequence[SkillInfo], max_steps: int) -> list[str]:
        """Stand-in for LLM skill selection: rarity (idf) x frequency (tf) weighted word matching.

        1. primary skills = those scoring close to the best match;
        2. every question word that no primary skill answers well pulls in its own best skill, so a question
           that spans two domains gets both. Returns [] when nothing matches so the caller falls back."""
        query = _select_tokens(message)
        docs = {s.id: _select_words(s.text) for s in skills}
        contrib: dict[str, dict[str, float]] = {}     # token -> skill id -> weight
        for token in query:
            tf = {sid: sum(_tok_match(token, w) for w in words) for sid, words in docs.items()}
            hits = {sid: n for sid, n in tf.items() if n}
            if hits:
                idf = math.log(1 + len(skills) / len(hits))
                by_agent = {s.id: s.agent for s in skills}
                # a skill owned by the agent the word names ('customer' -> customer.*) beats one that mentions it
                contrib[token] = {sid: idf * (1 + math.log(n)) * (1.5 if _tok_match(token, by_agent[sid]) else 1.0)
                                  for sid, n in hits.items()}
        if not contrib:
            return []
        scores = {s.id: sum(c.get(s.id, 0.0) for c in contrib.values()) for s in skills}
        best = max(scores.values())
        chosen = [sid for sid in sorted(scores, key=scores.get, reverse=True) if scores[sid] >= 0.7 * best]
        for token, per_skill in contrib.items():
            top = max(per_skill.values())
            if not any(per_skill.get(sid, 0.0) >= 0.8 * top for sid in chosen):
                chosen.append(max(per_skill, key=per_skill.get))
        return list(dict.fromkeys(chosen))[:max_steps]

    async def synthesize(self, message: str, parts: Mapping[str, str]) -> str:
        if len(parts) == 1:
            return next(iter(parts.values()))
        return "\n\n".join(f"[{name}] {text}" for name, text in parts.items())
