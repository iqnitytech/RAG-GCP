"""Routing evaluation gate.

    python scripts/eval_routing.py                    # mock LLM (CI)
    LLM_MODE=vertex VERTEX_PROJECT=... python scripts/eval_routing.py   # live (ADC) ; or VERTEX_API_KEY from env

Exit code 1 when accuracy < EVAL_MIN_ACCURACY (default 90).
"""
import asyncio
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from routing_eval import run_eval  # noqa: E402
from settings import Settings  # noqa: E402


def main() -> int:
    os.environ.setdefault("AGENTS_CONFIG", os.path.join(ROOT, "config", "agents.yaml"))
    settings = Settings.from_env()
    report = asyncio.run(run_eval(settings, os.path.join(ROOT, "tests", "eval", "routing_cases.json")))
    for r in report.failures:
        print(f"FAIL  {r.question!r}\n      expected={r.expected} used={r.used} skills={r.skills} :: {r.reason}")
    threshold = float(os.environ.get("EVAL_MIN_ACCURACY", "90"))
    print(f"\nllm_mode={settings.llm_mode} cases={len(report.results)} skipped={report.skipped} "
          f"accuracy={report.accuracy:.1f}% threshold={threshold}%")
    return 0 if report.accuracy >= threshold else 1


if __name__ == "__main__":
    sys.exit(main())
