# Sequence Diagrams

All diagrams use [Mermaid](https://mermaid.js.org/) (rendered by GitHub and VS Code's Markdown preview). Function names match the code. Participants:

| Alias | Component |
|---|---|
| UI | Chat client |
| API | [presentation/api.py](../src/presentation/api.py) |
| UC | `HandleChatUseCase` |
| SUP | `SupervisorAgent` |
| AREG | `AgentRegistry` |
| SEL | `LlmSkillSelector` |
| SREG | `SkillRegistry` |
| SH | `SkillShortlister` |
| LLM | `LLMProvider` (Vertex Gemini or Mock) |
| PLAN | `plan_agents` / `fanout_ids` |
| AG | `McpAgent` |
| MCP | MCP client -> MCP server (`mcp-machine`, `mcp-customer`, `mcp-telemetry`) |
| SF | Salesforce (mock or SDK) |

---

## 1. End-to-end chat request (single skill)

Question: *"status of machine M-1"*.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI
    participant API
    participant UC
    participant SUP
    participant AREG
    participant SEL
    participant LLM
    participant PLAN
    participant AG as McpAgent (machine)
    participant MCP as mcp-machine
    participant SF as Salesforce

    U->>UI: types question
    UI->>API: POST /chat {message} + Authorization: Bearer token
    API->>API: current_user() -> TokenValidator.validate(token)
    Note over API: 401 if the token is missing or invalid
    API->>UC: execute(message, ctx)
    UC->>UC: strip, reject empty or over 2000 chars
    UC->>SUP: handle(message, ctx)
    SUP->>AREG: allowed_for(ctx)
    AREG-->>SUP: agents permitted by roles
    SUP->>SEL: select(message, ctx)
    SEL->>LLM: select_skills(message, permitted skills, max_steps)
    LLM-->>SEL: ["machine.get_machine"]
    SEL-->>SUP: validated skill ids
    SUP->>PLAN: plan_agents(selected, allowed, message)
    PLAN-->>SUP: [[machine]]  (id M-1 present, nothing to add)
    SUP->>AG: handle(message, ctx, only_tools=["get_machine"])
    AG->>MCP: list_tools(ctx)
    MCP-->>AG: tool specs
    AG->>LLM: pick_tool(message, [get_machine])
    LLM-->>AG: ToolCall(get_machine, {machine_id: "M-1"})
    AG->>MCP: call_tool("get_machine", args, ctx)
    MCP->>MCP: AuthMiddleware + require_role("Machine.Read")
    MCP->>SF: query Machine where Id = M-1
    SF-->>MCP: record
    MCP-->>AG: JSON
    AG-->>SUP: result text
    SUP->>LLM: synthesize(message, {machine: result})
    LLM-->>SUP: final answer
    SUP-->>UC: ChatResult
    UC-->>API: ChatResult
    API-->>UI: 200 {answer, agents_used, skills_selected, errors}
```

---

## 2. Skill selection in detail

```mermaid
sequenceDiagram
    autonumber
    participant SUP as SupervisorAgent._choose
    participant SEL as LlmSkillSelector.select
    participant SREG as SkillRegistry
    participant AREG as AgentRegistry
    participant SH as SkillShortlister
    participant EMB as Embedder
    participant LLM as LLMProvider.select_skills

    SUP->>SEL: select(message, ctx)
    SEL->>SREG: list_for(ctx)
    SREG->>AREG: allowed_for(ctx)
    AREG-->>SREG: allowed agents
    loop each allowed agent
        SREG->>SREG: _records(agent) (cached 300 s, see diagram 5)
        SREG->>SREG: keep records where _permitted(record, agent, ctx)
    end
    SREG-->>SEL: approved + permitted skills
    alt more skills than SKILL_SHORTLIST_SIZE
        SEL->>SH: top(message, skills)
        SH->>EMB: embed(uncached skill texts)
        SH->>EMB: embed([message])
        EMB-->>SH: vectors
        SH-->>SEL: top-K by cosine similarity
        Note over SH: on embedder failure the full list is returned
    else small catalog
        Note over SEL: no embedding call
    end
    SEL->>LLM: select_skills(message, offered, max_steps)
    LLM-->>SEL: ids (untrusted)
    SEL->>SEL: keep only offered ids, de-duplicate, cap at max_steps
    SEL-->>SUP: validated skill ids
    SUP->>SUP: group by agent -> tools_by_agent, keep only allowed agents
```

---

## 3. Chained multi-agent question

Question: *"anomalies for all machines of customer named Acme"* (no ids supplied).

```mermaid
sequenceDiagram
    autonumber
    participant SUP as SupervisorAgent
    participant PLAN as planning
    participant C as customer agent
    participant M as machine agent
    participant T as telemetry agent
    participant LLM

    SUP->>PLAN: plan_agents(selected, allowed, message)
    Note over PLAN: no id in message, producers already selected,<br/>sort by order -> stages [[customer],[machine],[telemetry]]
    PLAN-->>SUP: stages

    rect rgb(235, 245, 255)
    Note over SUP,C: Stage 0
    SUP->>PLAN: fanout_ids(customer, message, {})
    PLAN-->>SUP: [] (run once)
    SUP->>C: handle(message, ctx, ["search_customers"])
    C-->>SUP: [{"id": "C-1", "name": "Acme Corp"}]
    SUP->>PLAN: find_ids("customer_id", result)
    PLAN-->>SUP: ["C-1"]
    end

    rect rgb(235, 255, 240)
    Note over SUP,M: Stage 1
    SUP->>PLAN: fanout_ids(machine, message, {customer_id: [C-1]})
    PLAN-->>SUP: ["C-1"]
    SUP->>M: handle(message + " C-1", ctx, ["list_machines_by_customer", "get_machine"])
    M-->>SUP: M-1, M-2
    SUP->>PLAN: find_ids("machine_id", result)
    PLAN-->>SUP: ["M-1", "M-2"]
    end

    rect rgb(255, 245, 230)
    Note over SUP,T: Stage 2 (parallel over ids)
    SUP->>PLAN: fanout_ids(telemetry, message, {machine_id: [M-1, M-2]})
    PLAN-->>SUP: ["M-1", "M-2"]
    par M-1
        SUP->>T: handle(message + " M-1", ctx, ["anomalies"])
    and M-2
        SUP->>T: handle(message + " M-2", ctx, ["anomalies"])
    end
    T-->>SUP: joined results (M-2 reading 91.5 C is an anomaly)
    end

    SUP->>LLM: synthesize(message, {customer, machine, telemetry})
    LLM-->>SUP: final answer
```

---

## 4. Fallback routing (selector returned nothing)

```mermaid
sequenceDiagram
    autonumber
    participant SUP as SupervisorAgent
    participant SEL as Skill selector
    participant H as HybridRouting
    participant K as KeywordRouting
    participant S as SkillRouting
    participant L as LlmRouting
    participant LLM

    SUP->>SEL: select(message, ctx)
    alt selector raised an error
        SEL--xSUP: Exception (caught and logged)
    else selector returned []
        SEL-->>SUP: []
    end
    SUP->>H: select(message, allowed, ctx)
    H->>K: select(...)
    alt keyword matched
        K-->>H: agents
        H-->>SUP: agents (tools_by_agent = {})
    else no keyword match
        K-->>H: []
        H->>S: select(...)
        alt skill words matched
            S-->>H: agents
            H-->>SUP: agents
        else no match
            S-->>H: []
            H->>L: select(...)
            L->>LLM: choose_agents(message, agents)
            LLM-->>L: names
            L-->>H: agents
            H-->>SUP: agents (possibly empty)
        end
    end
    opt still nothing
        SUP->>H: select(message, ALL agents, ctx)
        alt matches an agent the user may not use
            H-->>SUP: forbidden agents
            Note over SUP: answer "not authorised for the requested information"
        else no match anywhere
            H-->>SUP: []
            Note over SUP: answer "could not tell which capability you need"
        end
    end
```

---

## 5. Skill discovery, reconciliation and approval

Runs on first use of an agent and then every 300 seconds (`SkillRegistry._records`).

```mermaid
sequenceDiagram
    autonumber
    participant SREG as SkillRegistry
    participant MCP as MCP server
    participant ST as RegistryStore (Firestore or YAML)
    participant CAT as Skill catalog (config/skills/*.json)
    actor ADM as Registry admin

    SREG->>MCP: list_tools(ctx)
    MCP-->>SREG: tools (name, description, JSON schema)
    loop each tool
        SREG->>SREG: skill_id = agent.tool, digest = sha256(description, schema)
        SREG->>ST: get_skill(skill_id)
        alt no record
            Note over SREG: status = approved if auto_approve or catalog says approved, else pending
            SREG->>SREG: _overlay(record, catalog_entry)
            SREG->>ST: upsert_skill(record)
        else digest changed
            Note over SREG: version + 1, pending again (approved if auto_approve, disabled stays disabled)
            SREG->>SREG: _overlay(record, catalog_entry)
            SREG->>ST: upsert_skill(record)
        else unchanged
            SREG->>SREG: _overlay(existing, catalog_entry)
        end
    end
    SREG->>CAT: (loaded at startup) warn about catalog entries with no tool
    ADM->>ST: registry_admin.py pending / approve agent.tool / disable agent.tool
    Note over SREG,ST: new status is picked up after the cache expires or invalidate() is called
```

---

## 6. Service-to-service security handshake (chat-api to MCP)

```mermaid
sequenceDiagram
    autonumber
    participant AG as McpAgent
    participant AUD as AuditingMcpClient
    participant HC as HttpMcpClient
    participant GCP as Google metadata server (ADC)
    participant MW as MCP AuthMiddleware
    participant TOOL as FastMCP tool
    participant SF as Salesforce

    AG->>AUD: call_tool(name, args, ctx)
    AUD->>HC: call_tool(name, args, ctx)
    HC->>GCP: fetch ID token (audience = MCP URL)
    GCP-->>HC: Google ID token
    HC->>HC: sign(ctx) -> X-User-Context (HMAC-SHA256, 60 s TTL)
    HC->>MW: POST /mcp with Authorization: Bearer ID token and X-User-Context
    MW->>MW: verify_google_id_token(token, audience)
    MW->>MW: optional: caller email == MCP_ALLOWED_CALLER_SA
    MW->>MW: verify(X-User-Context) -> UserContext, then bind_user(ctx)
    Note over MW: 401 if token or context is missing, bad or expired. 403 if caller not allowed
    MW->>TOOL: tool invoked
    TOOL->>TOOL: require_role("Machine.Read") (Platform.Admin always passes)
    TOOL->>SF: query (validated identifiers, escaped values)
    SF-->>TOOL: rows
    TOOL-->>HC: result
    HC-->>AUD: normalised JSON
    AUD->>AUD: audit log {user, mcp, tool, args_sha256, outcome}
    AUD-->>AG: result
```

---

## 7. Direct skill invocation API

`POST /skills/{skill_id}/invoke` bypasses routing but not security.

```mermaid
sequenceDiagram
    autonumber
    participant UI
    participant API
    participant SREG as SkillRegistry.invoke
    participant AREG as AgentRegistry
    participant MCP

    UI->>API: POST /skills/machine.get_machine/invoke {"args": {"machine_id": "M-1"}}
    API->>API: current_user()  (401 if invalid)
    API->>SREG: invoke(skill_id, args, ctx)
    SREG->>AREG: get(agent) / allowed_for(ctx)
    alt unknown agent
        SREG-->>API: NotFoundError -> 404
    else agent not permitted
        SREG-->>API: AuthorizationError -> 403
    else
        SREG->>SREG: _records(agent), then record = records[tool]
        alt missing or not approved
            SREG-->>API: NotFoundError -> 404
        else skill roles not satisfied
            SREG-->>API: AuthorizationError -> 403
        else args missing or unknown keys
            SREG-->>API: DomainError -> 400
        else
            SREG->>MCP: call_tool(tool, args, ctx)
            MCP-->>SREG: result
            SREG-->>API: result
            API-->>UI: 200 {"result": ...}
        end
    end
    Note over API,SREG: discovery failure -> UpstreamError -> 502
```

---

## 8. Partial failure (one MCP is down)

```mermaid
sequenceDiagram
    autonumber
    participant SUP as SupervisorAgent
    participant M as machine agent
    participant T as telemetry agent
    participant LLM

    par stage runs in parallel
        SUP->>M: handle(...)
        M-->>SUP: result text
    and
        SUP->>T: handle(...)
        T--xSUP: UpstreamError (timeout or 5xx)
    end
    SUP->>SUP: errors[telemetry] = unavailable, parts[machine] = result
    SUP->>LLM: synthesize(message, {machine: result})
    LLM-->>SUP: answer from the data that is available
    Note over SUP: response keeps agents_used = [machine] and errors = {telemetry: unavailable}
    Note over SUP: if every agent fails: "could not retrieve that information right now"
```

---

## 9. Application start-up (composition root)

Providers are lazy singletons; nothing is built until first use.

```mermaid
sequenceDiagram
    autonumber
    participant APP as FastAPI lifespan
    participant CR as build_container / Providers
    participant ST as RegistryStore
    participant LLM as build_llm
    participant SF as build_salesforce
    participant MCPF as build_mcp_client
    participant CAT as load_skill_catalog

    APP->>CR: build_container(Settings.from_env())
    Note over CR: Settings.validate(): refuses unsafe prod combinations
    CR->>ST: build_registry_store() -> list_agents()
    CR->>LLM: build_llm() (one shared Vertex client when LLM_MODE=vertex)
    CR->>SF: build_salesforce() (mock or SDK)
    loop each agent manifest
        CR->>MCPF: build_mcp_client(manifest) wrapped by AuditingMcpClient
        CR->>CR: AgentRegistry.register(McpAgent(...))
    end
    CR->>CAT: load_skill_catalog(config/skills) (validated against the JSON schema)
    CR->>CR: SkillRegistry, SkillShortlister, LlmSkillSelector, routing strategies
    CR->>CR: SupervisorAgent(registry, strategy, llm, selector) -> HandleChatUseCase
    CR-->>APP: Container(settings, validator, registry, chat, skills)
```
