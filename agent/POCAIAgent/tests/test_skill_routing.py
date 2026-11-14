from conftest import auth


def chat(client, msg, roles="Platform.Admin"):
    return client.post("/chat", json={"message": msg}, headers=auth(roles)).json()


def test_partial_word_routes_through_skills(client):
    body = chat(client, "tele M-2")
    assert "telemetry" in body["agents_used"]
    assert "91.5" in body["answer"]


def test_tool_name_and_description_route_without_agent_keywords(client):
    # 'search_customers' / 'tier' are skill text, not in the agent keyword list
    body = chat(client, "search tier Acme")
    assert body["agents_used"] == ["customer"]


def test_unrelated_question_is_not_guessed(client):
    body = chat(client, "what is the weather in Paris")
    assert body["agents_used"] == [] and "could not tell" in body["answer"]


def test_skill_routing_respects_roles(client):
    body = chat(client, "tele M-2", roles="Machine.Read")
    assert body["agents_used"] == [] and "not authorised" in body["answer"]
