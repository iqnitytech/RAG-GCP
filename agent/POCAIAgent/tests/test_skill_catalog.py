import json

import pytest

from conftest import ROOT, auth
from container import Providers, build_container
from domain.ports import UserContext
from fastapi.testclient import TestClient
from infrastructure.registry.skill_catalog import load_skill_catalog
from infrastructure.registry.yaml_store import YamlRegistryStore
from presentation.api import create_app
from settings import Settings

AGENTS = str(ROOT / "config" / "agents.yaml")
ADMIN = "Platform.Admin"


def test_shipped_catalog_is_valid():
    catalog = load_skill_catalog(ROOT / "config" / "skills")
    assert len(catalog) == 6 and catalog["telemetry.anomalies"].status == "approved"


def test_invalid_catalog_reports_every_problem(tmp_path):
    (tmp_path.parent / "skills.schema.json").write_text((ROOT / "config" / "skills.schema.json").read_text())
    (tmp_path / "a.json").write_text(json.dumps({"agent": "machine", "skills": [
        {"id": "customer.x", "name": "wrong agent prefix"},
        {"id": "machine.ok", "name": "fine"}, {"id": "machine.ok", "name": "duplicate"}]}))
    (tmp_path / "b.json").write_text(json.dumps({"agent": "m", "skills": [{"id": "BAD", "name": "x", "status": "live"}]}))
    (tmp_path / "c.json").write_text("{not json")
    with pytest.raises(ValueError) as exc:
        load_skill_catalog(tmp_path)
    text = str(exc.value)
    assert "must start with 'machine.'" in text and "duplicate skill id" in text
    assert "b.json" in text and "invalid JSON" in text


def _client(tmp_path, catalog: dict | None, auto: bool):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (tmp_path / "skills.schema.json").write_text((ROOT / "config" / "skills.schema.json").read_text())
    for agent, entries in (catalog or {}).items():
        (skills_dir / f"{agent}.json").write_text(json.dumps({"agent": agent, "skills": entries}))
    settings = Settings(agents_config=AGENTS, skills_catalog_dir=str(skills_dir), skills_auto_approve=auto)
    p = Providers(settings)
    return TestClient(create_app(build_container(settings, p))), p


def test_catalog_metadata_is_exposed_on_skills(client):
    skill = next(s for s in client.get("/skills", headers=auth(ADMIN)).json() if s["id"] == "machine.get_machine")
    assert skill["name"] == "Machine details" and "asset" in skill["tags"] and skill["examples"]


def test_only_catalog_approved_skills_go_live_when_not_auto(tmp_path):
    catalog = {"machine": [{"id": "machine.get_machine", "name": "Machine details", "status": "approved"}]}
    with _client(tmp_path, catalog, auto=False)[0] as c:
        ids = [s["id"] for s in c.get("/skills", headers=auth(ADMIN)).json()]
    assert ids == ["machine.get_machine"]          # every other discovered tool stays pending


def test_catalog_can_disable_a_skill_and_set_roles(tmp_path):
    catalog = {"machine": [
        {"id": "machine.get_machine", "name": "Machine details", "status": "disabled"},
        {"id": "machine.list_machines_by_customer", "name": "Fleet", "required_roles": ["Fleet.Read"]}]}
    with _client(tmp_path, catalog, auto=True)[0] as c:
        ids = [s["id"] for s in c.get("/skills", headers=auth(ADMIN)).json() if s["agent"] == "machine"]
        assert ids == ["machine.list_machines_by_customer"]
        r = c.post("/skills/machine.get_machine/invoke", json={"args": {"machine_id": "M-1"}}, headers=auth(ADMIN))
        assert r.status_code == 404
        # catalog-set role overrides the agent's role for this skill
        r = c.post("/skills/machine.list_machines_by_customer/invoke", json={"args": {"customer_id": "C-1"}},
                   headers=auth("Machine.Read"))
        assert r.status_code == 403


def test_examples_improve_routing(tmp_path):
    question = {"message": "any overheating on M-2"}
    without = {"telemetry": [{"id": "telemetry.anomalies", "name": "Machine anomalies"}]}
    (tmp_path / "a").mkdir()
    with _client(tmp_path / "a", without, auto=True)[0] as c:
        assert c.post("/chat", json=question, headers=auth(ADMIN)).json()["agents_used"] == []
    (tmp_path / "b").mkdir()
    with_examples = {"telemetry": [{"id": "telemetry.anomalies", "name": "Machine anomalies",
                                     "tags": ["overheating", "fault"], "examples": ["is M-2 overheating"]}]}
    with _client(tmp_path / "b", with_examples, auto=True)[0] as c:
        body = c.post("/chat", json=question, headers=auth(ADMIN)).json()
    assert "telemetry" in body["agents_used"] and "91.5" in body["answer"]
