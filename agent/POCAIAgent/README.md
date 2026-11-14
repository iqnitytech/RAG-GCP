# AI Chat Platform (MachineInfo · CustomerInfo · TelemetryInfo)

Design: [ARCHITECTURE.md](ARCHITECTURE.md) (original proposal). Current documentation with workflows, routing logic and
sequence diagrams: [docs/](docs/README.md). Python 3.11+.

```
src/
  domain/          entities, ports (interfaces), errors
  application/     supervisor, McpAgent, routing strategies, registry, use case
  infrastructure/  Entra/dev validators, Google ID tokens, signed user context,
                   Salesforce (mock + SDK adapter + repositories), MCP clients, LLM (mock/Vertex)
  presentation/    FastAPI app (/chat, /agents, /health)
  mcp_servers/     machine, customer, telemetry MCP servers + shared auth/RBAC
  container.py     composition root (only place with concrete classes)
config/agents.yaml agent/MCP registry
```

## Run locally (everything in-process, mock Salesforce + mock LLM)
```
pip install -r requirements.txt
python -m pytest
cd src ; $env:AGENTS_CONFIG="..\config\agents.yaml" ; uvicorn main:app --port 8100
curl -X POST localhost:8100/chat -H "Authorization: Bearer dev:alice:Machine.Read,Telemetry.Read" `
     -H "Content-Type: application/json" -d '{\"message\":\"anomalies for M-2\"}'
```
`dev:<user>:<roles>` tokens work only with `AUTH_MODE=dev` (refused when `APP_ENV=prod`).

## Run as separate services (real MCP over HTTP)
```
# one per service, from ./src
$env:MCP_SERVICE="machine";   uvicorn mcp_servers.app:app --port 8101
$env:MCP_SERVICE="customer";  uvicorn mcp_servers.app:app --port 8102
$env:MCP_SERVICE="telemetry"; uvicorn mcp_servers.app:app --port 8103
$env:MCP_TRANSPORT="streamable_http"   # chat-api uses URLs from .env.example
```

## Production switches
`AUTH_MODE=entra` (+ tenant/audience), `MCP_AUTH=google` + `MCP_ALLOWED_CALLER_SA`, `USER_CONTEXT_SECRET` from Secret Manager,
`LLM_MODE=vertex`, `SF_MODE=sdk`, `ROUTING=hybrid`. `Settings` refuses unsafe prod combinations.
Deploy the same image to Cloud Run (`APP_MODULE=mcp_servers.app:app` + `MCP_SERVICE=...` for MCPs); see ARCHITECTURE.md §6/§10.

## Registry store (prod)
`REGISTRY_STORE=firestore` keeps agents (`<prefix>_agents`) and governed skills (`<prefix>_skills`) in Firestore.
1. `python scripts/registry_admin.py seed config/agents.yaml` (agents; run by admins/CI)
2. Start chat-api: discovered MCP tools are stored as `pending` (changed tools go back to `pending`).
3. `python scripts/registry_admin.py pending` then `approve <agent.tool>`; per-skill `required_roles` can be set on the doc.
Only `approved` skills are listed/invokable. Agents are read at startup (redeploy/restart to pick up changes); skills refresh every 5 min.

## LLM (Vertex AI)
`LLM_MODE=vertex` uses one shared Gemini client with a model per role: routing and tool selection default to
`gemini-2.5-flash` (cheap, fast), the final answer to `gemini-2.5-pro`. Override with `VERTEX_MODEL_ROUTING`,
`VERTEX_MODEL_TOOLS`, `VERTEX_MODEL_SYNTHESIS` (or `VERTEX_MODEL` for all). Auth is ADC by default;
`VERTEX_API_KEY` is optional and must come from Secret Manager, never source control. Check the model names are
available in your region/project.

## Dependency injection
[container.py](src/container.py) defines `Providers`: lazy, thread-safe singletons ([di.py](src/infrastructure/di.py))
for settings, validator, LLM, Salesforce, registry store, agent registry, routing, supervisor, chat and skills.
Tests can override a provider (`p.llm = Singleton(lambda: fake)`) before first use.

## Skill catalog (JSON metadata)
[config/skills/](config/skills) holds one A2A-style JSON file per agent (name, tags, example questions, required roles,
version, owner, status), validated by [skills.schema.json](config/skills.schema.json). The MCP tool stays the source of
truth for the executable contract; the catalog adds reviewed metadata that is applied at startup:
- `status: approved` lets a *new* tool go live without `SKILLS_AUTO_APPROVE`; a changed tool contract still returns to `pending`.
- `status: disabled` switches a skill off; `required_roles` overrides the agent's roles for that skill.
- `tags` and `examples` feed skill routing (so "any overheating on M-2" finds the telemetry agent).
- Tools with no catalog entry stay `pending` in prod. Catalog entries with no matching tool are logged and ignored.
Validate in CI: `python scripts/registry_admin.py validate-catalog config/skills`. Override the folder with `SKILLS_CATALOG_DIR`.

## LLM skill selection and routing evaluation
Each question is handled in this order ([supervisor.py](src/application/supervisor.py)):
1. **Skill selection (`SKILL_SELECTION=llm`):** the LLM sees only the caller's approved, role-permitted skills
   (name, description, tags, examples) and picks up to `SKILL_MAX_STEPS`. Its answer is validated (unknown, duplicate
   or over-cap ids are dropped). The chosen tool is pinned for that agent. With many skills an embedding shortlist
   (`EMBEDDER`, `SKILL_SHORTLIST_SIZE`) keeps the prompt small; it is inactive for small catalogs.
2. **Fallback:** if the selector returns nothing or fails, the `ROUTING` strategy runs (keyword -> skill -> LLM).
3. **Planning:** dependent steps (customer name -> machines -> telemetry) are added and ordered by the agents'
   `consumes`/`produces` rules, whatever order the LLM returned.
4. If nothing matches: "could not tell"; if the question matches only a forbidden agent: "not authorised".

Routing evaluation: [tests/eval/routing_cases.json](tests/eval/routing_cases.json) (labelled questions: required agents,
forbidden agents, optional exact match and skills; `tier: llm` cases only run with a real LLM).
```
python scripts/eval_routing.py                                  # mock LLM, also enforced in pytest (>= 90%)
$env:LLM_MODE="vertex"; $env:VERTEX_PROJECT="<project>"; python scripts/eval_routing.py   # live; ADC or VERTEX_API_KEY from env
```
The mock-tier set was used to tune the mock, so its score is not evidence about Vertex: run the live eval
(including the `llm` tier) and add real user questions to the file before trusting routing in production.

## Add a new MCP
1. Create `mcp_servers/<name>/server.py` with `build_server(repo)`; call `require_role("<Name>.Read")` in each tool.
2. Register it in `mcp_servers/catalog.py` (one line).
3. Add an entry to `config/agents.yaml` (+ Entra app role). No change to supervisor/routing.
