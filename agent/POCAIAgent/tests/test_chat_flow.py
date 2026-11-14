from conftest import auth

ALL = "Machine.Read,Customer.Read,Telemetry.Read"


def chat(client, msg, roles=ALL):
    return client.post("/chat", json={"message": msg}, headers=auth(roles))


def test_requires_token(client):
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    assert client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer junk"}).status_code == 401


def test_machine_info(client):
    r = chat(client, "What is the status of machine M-1?")
    assert r.status_code == 200
    body = r.json()
    assert body["agents_used"] == ["machine"]
    assert "CNC-100" in body["answer"] and "Running" in body["answer"]


def test_machines_of_customer(client):
    body = chat(client, "List machines for customer C-1").json()
    assert "customer" in body["agents_used"] or "machine" in body["agents_used"]
    assert "M-1" in body["answer"] and "M-2" in body["answer"]


def test_customer_search_by_name(client):
    body = chat(client, "Find customer named Acme").json()
    assert "Acme Corp" in body["answer"]


def test_telemetry_anomalies(client):
    body = chat(client, "Show telemetry anomalies for M-2").json()
    assert body["agents_used"] == ["telemetry"]
    assert "91.5" in body["answer"]


def test_multi_agent_fanout(client):
    body = chat(client, "Show customer C-1 and machine M-1 details").json()
    assert set(body["agents_used"]) == {"machine", "customer"}


def test_chained_customer_name_to_machines_to_telemetry(client):
    body = chat(client, "Show telemetry readings for all machines of customer named Acme").json()
    assert body["agents_used"] == ["customer", "machine", "telemetry"]
    assert "M-1" in body["answer"] and "91.5" in body["answer"]  # M-2 telemetry reached via the chain


def test_chain_skips_unauthorised_producer(client):
    body = chat(client, "Show telemetry readings for all machines of customer named Acme",
                roles="Machine.Read,Telemetry.Read").json()
    assert "customer" not in body["agents_used"]


def test_rbac_blocks_unauthorised_domain(client):
    body = chat(client, "Show telemetry for M-1", roles="Machine.Read").json()
    assert body["agents_used"] == []
    assert "not authorised" in body["answer"]


def test_admin_sees_everything(client):
    r = client.get("/agents", headers=auth("Platform.Admin"))
    assert {a["name"] for a in r.json()} == {"machine", "customer", "telemetry"}
    machine = next(a for a in r.json() if a["name"] == "machine")
    assert machine["version"] == "1.0.0" and machine["owner"] and machine["produces"] == ["machine_id"]
    r = client.get("/agents", headers=auth("Customer.Read"))
    assert [a["name"] for a in r.json()] == ["customer"]


def test_validation(client):
    assert client.post("/chat", json={"message": ""}, headers=auth(ALL)).status_code == 422
    assert client.post("/chat", json={"message": "x" * 3000}, headers=auth(ALL)).status_code == 422
