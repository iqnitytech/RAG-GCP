"""Registry admin CLI (run with ADC from CI or a privileged admin account; NOT from chat-api).

    python scripts/registry_admin.py validate-catalog [config/skills]   # no GCP needed; use in CI
    python scripts/registry_admin.py seed config/agents.yaml            # agents -> Firestore
    python scripts/registry_admin.py pending
    python scripts/registry_admin.py approve machine.get_machine
    python scripts/registry_admin.py disable machine.get_machine

Skill metadata (name, tags, examples, roles, approval) lives in config/skills/*.json (reviewed via PR) and is
applied by chat-api at startup; `approve`/`disable` edit the Firestore record's status for ad-hoc decisions.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from infrastructure.registry.skill_catalog import load_skill_catalog  # noqa: E402


def _store():
    from google.cloud import firestore

    from infrastructure.registry.firestore_store import FirestoreRegistryStore

    return FirestoreRegistryStore(
        firestore.Client(project=os.environ.get("FIRESTORE_PROJECT") or None),
        os.environ.get("REGISTRY_PREFIX", "registry"),
    )


def main(argv: list[str]) -> int:
    cmd, *rest = argv or ["help"]
    if cmd == "validate-catalog":
        try:
            catalog = load_skill_catalog(rest[0] if rest else "config/skills")
        except ValueError as exc:
            print(exc)
            return 1
        print(f"OK: {len(catalog)} skills")
    elif cmd == "seed" and rest:
        from infrastructure.manifest_loader import load_manifests

        store = _store()
        for m in load_manifests(rest[0]):
            store.put_agent(m)
            print("agent:", m.name)
    elif cmd == "pending":
        for r in _store().list_by_status("pending"):
            print(f"{r.id}  v{r.version}  {r.description}")
    elif cmd in ("approve", "disable") and rest:
        _store().set_status(rest[0], "approved" if cmd == "approve" else "disabled")
        print(cmd, rest[0])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
