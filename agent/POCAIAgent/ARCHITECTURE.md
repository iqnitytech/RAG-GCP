# AI Chat Platform on GCP — MachineInfo · CustomerInfo · TelemetryInfo

Multi-agent chat application with Entra ID sign-in, GCP workload identity, a Supervisor agent orchestrating three MCP servers, and Salesforce backends. Designed for extension (new MCPs/APIs/agents with no change to existing code).

> **Note:** this is the original design proposal. The implemented system, its workflows, the query-routing logic and sequence diagrams are documented in [docs/](docs/README.md), which supersede this file where they differ (for example, routing is now LLM skill selection with keyword/skill/LLM fallback, and the registries are Firestore/YAML-backed).

---

## 1. Goals & Constraints

| # | Requirement | Design response |
|---|---|---|
| 1 | Microsoft Entra login for FastAPI | OIDC/JWT validation of Entra access tokens (JWKS) in a FastAPI dependency |
| 2 | GCP authentication with token | Cloud Run service account (ADC) + Entra→GCP Workload Identity Federation; Google ID tokens for service-to-service (Cloud Run IAM) |
| 3 | Agent orchestration with 3 MCPs | Supervisor agent → domain agents → MCP servers (Machine, Customer, Telemetry) |
| 4 | MCP calls Salesforce (mock now, SDK later) | `SalesforcePort` interface + Mock adapter; swap for the real SDK adapter by config |
| 5 | SOLID | Ports & adapters, DI container, small interfaces |
| 6 | Patterns | Hierarchy (Supervisor→Agents→Tools), Strategy (routing, LLM, auth), Registry, Factory, Adapter, Decorator (audit/cache), Chain of Responsibility (middleware) |
| 7 | Open for extension | Plugin registry + capability manifest; add an MCP by registering config only |

---

## 2. Solution Architecture (Logical)

```
 ┌────────────────┐   Entra ID (OIDC, PKCE)    ┌──────────────────────┐
 │ Chat UI (SPA)  │───────────────────────────▶│ Microsoft Entra ID   │
 │ React / MSAL   │◀──── access token (JWT) ───│ (App Reg: API scope) │
 └───────┬────────┘                            └──────────┬───────────┘
         │ HTTPS + Bearer JWT                             │ JWKS (public keys)
         ▼                                                │
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Cloud Armor + External HTTPS LB (WAF, rate limit)  ── IAP optional   │
 └───────┬──────────────────────────────────────────────────────────────┘
         ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Cloud Run: chat-api (FastAPI)           SA: sa-chat-api              │
 │  Presentation → Application (use cases) → Domain ← Infrastructure    │
 │                                                                      │
 │   ┌──────────────── Supervisor Agent (Strategy-based router) ──────┐ │
 │   │  RoutingStrategy: LLM-intent | Keyword | Hybrid                 │ │
 │   └───────┬───────────────────┬───────────────────┬────────────────┘ │
 │     MachineAgent        CustomerAgent       TelemetryAgent  (+N)     │
 └───────┬───────────────────┬───────────────────┬──────────────────────┘
         │ MCP (streamable HTTP) + Google ID token (Cloud Run IAM, run.invoker)
         ▼                   ▼                   ▼
 ┌──────────────┐   ┌────────────────┐   ┌─────────────────┐
 │ mcp-machine  │   │ mcp-customer   │   │ mcp-telemetry   │  Cloud Run (private, ingress internal)
 │ SA: sa-mcp-m │   │ SA: sa-mcp-c   │   │ SA: sa-mcp-t    │
 └──────┬───────┘   └───────┬────────┘   └────────┬────────┘
        │ SalesforcePort    │ SalesforcePort      │ TelemetryPort (SF + BigQuery)
        ▼                   ▼                     ▼
   Salesforce (mock → SDK, creds in Secret Manager via VPC egress / Cloud NAT static IP)

 Cross-cutting: Secret Manager · Vertex AI (Gemini) · Firestore/Memorystore (sessions)
                Cloud Logging/Trace/Monitoring · BigQuery (audit & telemetry) · KMS (CMEK)
```

### Shared Agents / MCPs
Shared components live in a central **Agent & MCP Registry** (Firestore collection or Artifact Registry + config repo). Each entry is a manifest (`name`, `endpoint`, `transport`, `capabilities`, `required_roles`, `owner`, `version`). Teams in other projects publish MCPs; chat-api discovers them at startup/refresh. Cross-project access is granted via `roles/run.invoker` on the target service to `sa-chat-api`.

---

## 3. Clean Architecture Layout

Dependency rule: **outer layers depend inward only**. Domain has zero imports of FastAPI, GCP, MCP, or Salesforce.

```
src/
├─ domain/                      # Entities, value objects, ports (interfaces)
│  ├─ entities.py               # Machine, Customer, TelemetryReading, ChatMessage
│  ├─ ports.py                  # SalesforcePort, AgentPort, RoutingStrategy, TokenValidator, ...
│  └─ errors.py
├─ application/                 # Use cases / orchestration (no framework code)
│  ├─ chat_service.py           # HandleChatUseCase
│  ├─ supervisor.py             # SupervisorAgent
│  ├─ routing/                  # Strategy implementations
│  └─ registry.py               # AgentRegistry (Open/Closed)
├─ infrastructure/              # Adapters
│  ├─ auth/entra_validator.py   # JWT/JWKS
│  ├─ auth/gcp_tokens.py        # Google ID token provider
│  ├─ llm/vertex_gemini.py
│  ├─ mcp/client.py             # MCP client adapter
│  ├─ salesforce/mock.py, sdk.py
│  └─ observability/
├─ presentation/                # FastAPI routers, schemas, middleware
│  └─ api.py
├─ mcp_servers/
│  ├─ machine/ customer/ telemetry/   # each its own deployable
└─ container.py                 # Composition root (DI)
```

---

## 4. Patterns Mapping

| Pattern | Where | Benefit |
|---|---|---|
| **Hierarchy** | Supervisor → Domain Agents → MCP tools | Clear responsibility, scoped permissions per level |
| **Supervisor** | `SupervisorAgent` plans, delegates, aggregates multi-agent answers | Handles "Which machines does customer X own and their latest telemetry?" |
| **Strategy** | `RoutingStrategy`, `LLMProvider`, `SalesforcePort` impls, `TokenValidator` | Swap behaviour by config |
| **Registry / Factory** | `AgentRegistry`, `McpClientFactory` | Add agents without editing orchestrator |
| **Adapter** | Salesforce SDK, MCP client, Vertex | Isolates vendor APIs |
| **Decorator** | Audit, caching, retry, circuit breaker around ports | Cross-cutting without modifying core |
| **Chain of Responsibility** | Middleware: correlation-id → auth → RBAC → rate limit → audit | Ordered, composable |
| **Dependency Injection** | `container.py` | Testability |

SOLID: **S** each agent/MCP one domain; **O** registry + manifests; **L** any `SalesforcePort` is substitutable (mock/SDK); **I** narrow ports (`MachineRepository`, `CustomerRepository`, `TelemetryRepository`); **D** use cases depend on ports.

---

## 5. Authentication & Identity Flow

1. SPA signs in with MSAL (Auth Code + PKCE) and requests scope `api://<chat-api-app-id>/Chat.Access`.
2. SPA calls FastAPI with `Authorization: Bearer <Entra JWT>`.
3. FastAPI validates: signature (JWKS from `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys`), `iss`, `aud`, `exp/nbf`, `tid`; extracts `oid`, `roles`, `groups`.
4. RBAC maps Entra **App Roles** → capabilities (below).
5. chat-api calls MCP servers using **Google ID tokens** (audience = MCP Cloud Run URL) obtained from the metadata server (ADC) — no keys.
6. User identity is propagated to MCPs as a signed header/claim (`X-User-Context`, short‑lived, signed by chat-api via KMS) for row-level authorisation and audit. MCPs never trust it without the Google ID token.
7. Optional: **Workload Identity Federation** (Entra as OIDC provider) if the user token must directly access GCP APIs (e.g. BigQuery row-level on behalf of user); otherwise service accounts are used.
8. Salesforce: OAuth2 JWT bearer / client-credentials stored in Secret Manager; per-user context passed via Salesforce "run-as" or filtering where required.

---

## 6. Permissions

### 6.1 Entra App Roles (API app registration)
| Role | Chat | Machine | Customer | Telemetry |
|---|---|---|---|---|
| `Chat.User` | ✔ | – | – | – |
| `Machine.Read` | | ✔ | | |
| `Customer.Read` | | | ✔ | |
| `Telemetry.Read` | | | | ✔ |
| `Platform.Admin` | ✔ | ✔ | ✔ | ✔ + registry mgmt |

Assign roles to Entra security groups (not users); enforce Conditional Access/MFA.

### 6.2 GCP IAM (least privilege, one SA per workload)
| Service account | Roles | Scope |
|---|---|---|
| `sa-chat-api` | `roles/run.invoker` (each MCP service), `roles/aiplatform.user`, `roles/secretmanager.secretAccessor` (entra config only), `roles/datastore.user`, `roles/logging.logWriter`, `roles/cloudtrace.agent`, `roles/cloudkms.signerVerifier` (user-context key) | Project/resource level |
| `sa-mcp-machine` | `secretmanager.secretAccessor` (SF secret), `logging.logWriter` | Secret level |
| `sa-mcp-customer` | same as above (own secret) | Secret level |
| `sa-mcp-telemetry` | above + `roles/bigquery.dataViewer` (dataset), `bigquery.jobUser` | Dataset level |
| `sa-cicd` | `run.admin`, `artifactregistry.writer`, `iam.serviceAccountUser` (only on above SAs) | Via WIF from GitHub/Azure DevOps |

Rules: no SA keys; MCP services `--no-allow-unauthenticated`, `ingress=internal-and-cloud-load-balancing` (or internal), VPC connector + Cloud NAT static IP for Salesforce IP allow-listing; Org Policies: disable SA key creation, restrict domains, require CMEK where mandated; VPC Service Controls perimeter around Secret Manager/BigQuery/Vertex.

### 6.3 Authorization decision points
- **Edge** (chat-api): token valid + role allows requested domain.
- **Supervisor**: only routes to agents the caller's roles permit (filter registry by `required_roles`).
- **MCP**: re-validates Google ID token and `X-User-Context` role before tool execution (defense in depth).

---

## 7. Core Code (reference implementation skeleton)

### 7.1 Domain ports
```python
# domain/ports.py
from typing import Protocol, Sequence
from dataclasses import dataclass

@dataclass(frozen=True)
class UserContext:
    user_id: str
    tenant_id: str
    roles: frozenset[str]

class SalesforcePort(Protocol):
    async def query(self, soql: str) -> list[dict]: ...

class MachineRepository(Protocol):
    async def get_machine(self, machine_id: str) -> dict: ...
class CustomerRepository(Protocol):
    async def get_customer(self, customer_id: str) -> dict: ...
class TelemetryRepository(Protocol):
    async def latest(self, machine_id: str, limit: int = 10) -> list[dict]: ...

class Agent(Protocol):
    name: str
    description: str
    required_roles: frozenset[str]
    async def handle(self, message: str, ctx: UserContext) -> str: ...

class RoutingStrategy(Protocol):
    async def select(self, message: str, agents: Sequence[Agent]) -> list[Agent]: ...

class TokenValidator(Protocol):
    async def validate(self, token: str) -> UserContext: ...
```

### 7.2 Mock Salesforce (swap for SDK adapter)
```python
# infrastructure/salesforce/mock.py
class MockSalesforce:
    _data = {
        "Machine": [{"Id": "M-1", "Name": "CNC-100", "Status": "Running", "CustomerId": "C-1"}],
        "Customer": [{"Id": "C-1", "Name": "Acme Corp", "Tier": "Gold"}],
        "Telemetry": [{"MachineId": "M-1", "Temp": 71.2, "Vibration": 0.3, "Ts": "2026-11-13T07:00:00Z"}],
    }
    async def query(self, soql: str) -> list[dict]:
        for obj, rows in self._data.items():
            if f"FROM {obj}" in soql:
                return rows
        return []

# infrastructure/salesforce/sdk.py  (real adapter; same interface)
class SalesforceSdkClient:
    def __init__(self, sf):          # e.g. simple_salesforce.Salesforce built from Secret Manager creds
        self._sf = sf
    async def query(self, soql: str) -> list[dict]:
        import anyio
        r = await anyio.to_thread.run_sync(self._sf.query_all, soql)
        return r["records"]
```

### 7.3 Entra validation (FastAPI)
```python
# infrastructure/auth/entra_validator.py
import jwt                      # PyJWT
from jwt import PyJWKClient
from domain.ports import UserContext

class EntraTokenValidator:
    def __init__(self, tenant_id: str, audience: str):
        self._tenant, self._aud = tenant_id, audience
        self._jwks = PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys")
    async def validate(self, token: str) -> UserContext:
        key = self._jwks.get_signing_key_from_jwt(token).key
        c = jwt.decode(token, key, algorithms=["RS256"], audience=self._aud,
                       issuer=f"https://login.microsoftonline.com/{self._tenant}/v2.0")
        return UserContext(c["oid"], c["tid"], frozenset(c.get("roles", [])))

# presentation/deps.py
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer
bearer = HTTPBearer()
async def current_user(cred=Depends(bearer), validator=Depends(get_validator)):
    try:
        return await validator.validate(cred.credentials)
    except Exception:
        raise HTTPException(401, "Invalid token")
```

### 7.4 Registry, Strategy, Supervisor
```python
# application/registry.py
class AgentRegistry:
    def __init__(self): self._agents: dict[str, Agent] = {}
    def register(self, agent: Agent): self._agents[agent.name] = agent
    def allowed_for(self, ctx: UserContext) -> list[Agent]:
        return [a for a in self._agents.values()
                if not a.required_roles or a.required_roles & ctx.roles or "Platform.Admin" in ctx.roles]

# application/routing/keyword.py
class KeywordRouting:
    KEYS = {"machine": ["machine", "equipment", "device"],
            "customer": ["customer", "account", "client"],
            "telemetry": ["telemetry", "temperature", "vibration", "sensor"]}
    async def select(self, message, agents):
        m = message.lower()
        hit = [a for a in agents if any(k in m for k in self.KEYS.get(a.name, []))]
        return hit or list(agents)

# application/routing/llm.py  -> LlmRouting(llm).select(): Gemini function-calling picks agents (JSON output)
# application/routing/hybrid.py -> keyword first, fall back to LLM

# application/supervisor.py
import asyncio
class SupervisorAgent:
    def __init__(self, registry, strategy, llm):
        self._r, self._s, self._llm = registry, strategy, llm
    async def handle(self, message: str, ctx: UserContext) -> str:
        agents = await self._s.select(message, self._r.allowed_for(ctx))
        if not agents:
            return "You are not authorised for any capability that can answer this."
        results = await asyncio.gather(*(a.handle(message, ctx) for a in agents), return_exceptions=True)
        parts = {a.name: r for a, r in zip(agents, results) if not isinstance(r, Exception)}
        return await self._llm.synthesize(message, parts)   # aggregate to one answer
```

### 7.5 Domain agent (generic, MCP-backed)
```python
# application/mcp_agent.py
class McpAgent:
    def __init__(self, name, description, required_roles, mcp_client, llm):
        self.name, self.description, self.required_roles = name, description, frozenset(required_roles)
        self._mcp, self._llm = mcp_client, llm
    async def handle(self, message, ctx):
        tools = await self._mcp.list_tools()
        call = await self._llm.pick_tool(message, tools)       # LLM selects tool + args
        return str(await self._mcp.call_tool(call.name, call.args, ctx))
```
One class serves all three domains → adding a 4th is configuration, not code (OCP).

### 7.6 MCP server (e.g. Machine) with FastMCP
```python
# mcp_servers/machine/server.py
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("machine-info")
sf = build_salesforce()          # mock or SDK via env SF_MODE=mock|sdk

@mcp.tool()
async def get_machine(machine_id: str) -> dict:
    rows = await sf.query(f"SELECT Id,Name,Status,CustomerId FROM Machine WHERE Id='{sanitize(machine_id)}'")
    return rows[0] if rows else {}

@mcp.tool()
async def list_machines_by_customer(customer_id: str) -> list[dict]:
    return await sf.query(f"SELECT Id,Name,Status FROM Machine WHERE CustomerId='{sanitize(customer_id)}'")

app = mcp.streamable_http_app()  # wrapped by middleware that verifies Google ID token + user context
```
Customer MCP: `get_customer`, `search_customers`. Telemetry MCP: `latest_readings`, `anomalies` (Salesforce IoT objects and/or BigQuery).

### 7.7 Service-to-service Google ID token
```python
# infrastructure/auth/gcp_tokens.py
import google.auth.transport.requests, google.oauth2.id_token
def id_token_for(audience: str) -> str:
    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)
# MCP client sets: Authorization: Bearer <id_token_for(mcp_url)>
```

### 7.8 API & composition root
```python
# presentation/api.py
@app.post("/chat")
async def chat(req: ChatRequest, user: UserContext = Depends(current_user), sup=Depends(get_supervisor)):
    return {"answer": await sup.handle(req.message, user)}

# container.py  — registers agents from config/registry
for m in load_manifests():     # machine, customer, telemetry, + future
    registry.register(McpAgent(m.name, m.description, m.roles, McpClient(m.endpoint, id_token_for), llm))
```

---

## 8. Extensibility: Adding a New MCP/API/Agent

1. Build MCP server (or REST adapter) implementing tools; deploy private Cloud Run with its own SA.
2. Grant `sa-chat-api` → `run.invoker` on it.
3. Add a manifest entry to the registry:
```yaml
- name: warranty
  description: Warranty and contract lookups
  endpoint: https://mcp-warranty-xxxx.run.app/mcp
  transport: streamable_http
  required_roles: [Warranty.Read]
```
4. Create the Entra App Role. No change to Supervisor, routing, or existing agents.
For non-MCP APIs implement `Agent` (or an `ApiToolAdapter`) and register. For third-party/shared agents use the A2A-style manifest the same way. Pin versions and schema-validate manifests in CI.

---

## 9. Non-Functional Design

| Area | Decision |
|---|---|
| Security | Cloud Armor, private MCPs, CMEK, VPC-SC, prompt-injection guard (tool allow-lists, output sanitisation, SOQL parameter sanitising/read-only), PII redaction (DLP API) before logging |
| Reliability | Per-MCP timeout, retry w/ jitter, circuit breaker; Supervisor degrades gracefully (partial answers); min instances ≥1 for chat-api |
| Scalability | Cloud Run autoscaling; stateless API; sessions in Firestore/Memorystore; streaming responses (SSE) |
| Observability | OpenTelemetry → Cloud Trace; correlation-id across agent/MCP hops; audit table in BigQuery (who, which tool, params hash); SLO alerts |
| Governance | Audit log of every tool call; Model Armor/Vertex safety settings; human-in-loop for any future write tools |
| Environments | dev / test / prod projects; Terraform; Cloud Build or GitHub Actions with WIF; Binary Authorization |
| Cost | Gemini Flash for routing, Pro for synthesis; response caching (Decorator) on read-mostly data |

---

## 10. Deployment (summary)

```bash
gcloud run deploy mcp-machine --source mcp_servers/machine --no-allow-unauthenticated \
  --service-account sa-mcp-machine@PROJECT.iam.gserviceaccount.com \
  --ingress internal --set-secrets SF_CREDS=sf-creds:latest --set-env-vars SF_MODE=mock

gcloud run services add-iam-policy-binding mcp-machine \
  --member serviceAccount:sa-chat-api@PROJECT.iam.gserviceaccount.com --role roles/run.invoker

gcloud run deploy chat-api --source . --service-account sa-chat-api@PROJECT.iam.gserviceaccount.com \
  --set-env-vars ENTRA_TENANT_ID=...,ENTRA_AUDIENCE=api://...,SF_MODE=mock
```

---

## 11. Roadmap
1. **MVP**: chat-api + 3 MCPs with mock Salesforce, Entra auth, keyword routing.
2. Replace mock with Salesforce SDK adapter; LLM/hybrid routing; synthesis.
3. Registry-driven discovery, shared agents across teams, audit to BigQuery.
4. Hardening: VPC-SC, CMEK, Model Armor, evaluation harness (golden prompts per agent), load tests.

## 12. Testing Strategy
- Unit: use cases with fake ports (mock Salesforce).
- Contract: MCP tool schemas (JSON schema snapshots).
- Integration: chat-api ↔ MCPs on local docker-compose with mock tokens.
- Security: JWT negative tests (wrong aud/iss/expired), RBAC matrix tests.
- LLM eval: routing accuracy on a labelled prompt set.
