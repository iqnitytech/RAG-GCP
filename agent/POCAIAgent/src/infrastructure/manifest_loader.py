from __future__ import annotations

import os
import re

import yaml

from application.manifest import AgentManifest

_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(value: str) -> str:
    return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)


def load_manifests(path: str) -> list[AgentManifest]:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return [
        AgentManifest(
            name=a["name"],
            description=a["description"],
            transport=_expand(a["transport"]),
            endpoint=_expand(a.get("endpoint", "")),
            required_roles=tuple(a.get("required_roles", [])),
            keywords=tuple(a.get("keywords", [])),
            order=int(a.get("order", 0)),
            consumes=tuple(a.get("consumes", [])),
            produces=tuple(a.get("produces", [])),
            version=str(a.get("version", "1.0.0")),
            owner=a.get("owner", ""),
            tags=tuple(a.get("tags", [])),
        )
        for a in raw.get("agents", [])
    ]
