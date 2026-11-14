from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from application.manifest import CatalogEntry

SCHEMA_FILE = "skills.schema.json"


def load_skill_catalog(directory: str | Path, schema_path: str | Path | None = None) -> dict[str, CatalogEntry]:
    """Load + validate every *.json in `directory`. Raises ValueError listing all problems (use in CI)."""
    directory = Path(directory)
    schema_file = Path(schema_path) if schema_path else directory.parent / SCHEMA_FILE
    validator = Draft202012Validator(json.loads(schema_file.read_text(encoding="utf-8")))
    entries: dict[str, CatalogEntry] = {}
    problems: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{path.name}: invalid JSON ({exc})")
            continue
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        problems += [f"{path.name}: {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
        if errors:
            continue
        for s in data["skills"]:
            if not s["id"].startswith(data["agent"] + "."):
                problems.append(f"{path.name}: skill id {s['id']!r} must start with '{data['agent']}.'")
            elif s["id"] in entries:
                problems.append(f"{path.name}: duplicate skill id {s['id']!r}")
            else:
                entries[s["id"]] = CatalogEntry(
                    id=s["id"], agent=data["agent"], name=s["name"], tags=tuple(s.get("tags", ())),
                    examples=tuple(s.get("examples", ())), required_roles=tuple(s.get("required_roles", ())),
                    version=s.get("version", "1.0.0"), status=s.get("status", "pending"), owner=s.get("owner", ""),
                )
    if problems:
        raise ValueError("Invalid skill catalog:\n  " + "\n  ".join(problems))
    return entries
