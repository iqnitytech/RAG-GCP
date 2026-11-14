from conftest import auth


def test_skills_are_discovered_per_role(client):
    ids = {s["id"] for s in client.get("/skills", headers=auth("Machine.Read")).json()}
    assert ids == {"machine.get_machine", "machine.list_machines_by_customer"}
    admin = {s["id"] for s in client.get("/skills", headers=auth("Platform.Admin")).json()}
    assert {"customer.search_customers", "telemetry.anomalies"} <= admin


def test_invoke_skill_by_id(client):
    r = client.post("/skills/machine.get_machine/invoke", json={"args": {"machine_id": "M-1"}},
                    headers=auth("Machine.Read"))
    assert r.status_code == 200 and r.json()["result"]["name"] == "CNC-100"


def test_invoke_skill_errors(client):
    h = auth("Machine.Read")
    assert client.post("/skills/telemetry.anomalies/invoke", json={"args": {"machine_id": "M-1"}},
                       headers=h).status_code == 403
    assert client.post("/skills/machine.nope/invoke", json={"args": {}}, headers=h).status_code == 404
    assert client.post("/skills/ghost.x/invoke", json={"args": {}}, headers=h).status_code == 404
    assert client.post("/skills/machine.get_machine/invoke", json={"args": {}}, headers=h).status_code == 400
    assert client.post("/skills/machine.get_machine/invoke",
                       json={"args": {"machine_id": "M-1", "x": 1}}, headers=h).status_code == 400
    assert client.post("/skills/machine.get_machine/invoke", json={"args": {"machine_id": "M-1"}}).status_code == 401
