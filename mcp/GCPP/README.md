# Multi-MCP Platform Integration (`gcpapp`)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-1.0+-green.svg)](https://modelcontextprotocol.io/)
[![Architecture: Hexagonal](https://img.shields.io/badge/architecture-hexagonal%2Fclean-purple.svg)](#architecture-overview)
[![Tests: 99 Passed](https://img.shields.io/badge/tests-99%20passed-success.svg)](#2-pytest-test-suite)
[![Coverage: 100% Domain](https://img.shields.io/badge/coverage-100%25%20domain-success.svg)](#2-pytest-test-suite)

An enterprise-scale, production-grade Model Context Protocol (MCP) ecosystem consisting of three specialized, loosely-coupled servers:

1. **`CustomerMCP` (`customer-mcp-server`)**: 360-degree customer profiles, account representatives, active SLAs/entitlements, and fleet asset associations.
2. **`MachineMCP` (`machine-mcp-server`)**: Hardware specifications, fleet distribution, maintenance records, and state transitions.
3. **`TelemetryMCP` (`telemetry-mcp-server`)**: High-frequency operational sensor snapshots, time-series data aggregation, anomaly alarms, and root-cause diagnostic trees.

All servers seamlessly consume our corporate SDK (`rag_platform_sdk-*.whl`) and feature a hot-swappable dual-mode adapter pattern (`MOCK` vs `SDK`).

---

## Architecture Overview

Built following **Hexagonal / Clean Architecture** and **SOLID Principles**:

```mermaid
graph TB
    subgraph "Clients & Frontends"
        CHAT[Intelligent Chat Agent UI<br/>http://localhost:8050]
        WEB[Client Web Dashboard<br/>http://localhost:8000]
        TERM[Interactive Terminal CLI<br/>python -m client_app.cli_main]
        CD[Claude Desktop]
        CU[Cursor IDE]
    end

    subgraph "Intelligent Chat Agent (chat_app)"
        ROUTER[McpIntentRouter<br/>Natural Language Classification]
        EXEC[ChatAgentExecutor<br/>Tool Selection & Execution]
        CHAT --> ROUTER
        ROUTER --> EXEC
    end

    subgraph "Client Application (client_app)"
        GW[LocalMcpClientGateway]
        UC_CLI[InvokeTool & Scenario UseCases]
        WEB & TERM --> UC_CLI
        UC_CLI --> GW
    end

    EXEC --> GW

    subgraph "MCP Platform Layer (mcp/)"
        subgraph "CustomerMCP (:8001)"
            CT[Customer Tools] --> CUC[Customer Use Cases]
            CUC --> CP[Customer Ports]
        end
        subgraph "MachineMCP (:8002)"
            MT[Machine Tools] --> MUC[Machine Use Cases]
            MUC --> MP[Machine Ports]
        end
        subgraph "TelemetryMCP (:8003)"
            TT[Telemetry Tools] --> TUC[Telemetry Use Cases]
            TUC --> TP[Telemetry Ports]
        end
    end

    subgraph "Core & DI"
        GF[GatewayFactory]
        LOG[Structured JSON Logger]
        EX[Exception Hierarchy]
    end

    subgraph "Infrastructure & Data Layer"
        CP & MP & TP --> GF
        GF -->|DATA_PROVIDER_MODE=MOCK| MOCK[Mock Adapters<br/>Deterministic Relational Fixtures]
        GF -->|DATA_PROVIDER_MODE=SDK| SDK[SDK Adapters<br/>rag_platform_sdk]
        SDK --> EDH[Enterprise Data Hub<br/>(API Hub / Data Lake)]
    end

    GW --> CT & MT & TT
    CD & CU --> CT & MT & TT
```

### Layered Separation of Concerns
- **Domain (`domain/`)**: Pure business entities (`Customer`, `Machine`, `Alert`, `MetricPoint`), value objects (`CustomerId`, `MachineId`, `SiteId`), and domain exceptions. Zero framework imports.
- **Application (`application/`)**: High-level workflows, role-segregated ports (`CustomerReaderPort`, `MachineOperationsPort`), and validated use cases.
- **Infrastructure (`infrastructure/`)**:
  - `MockAdapter`: High-fidelity synthetic datasets sharing relational IDs.
  - `SdkAdapter`: Resilient calls to `rag_platform_sdk.connectors.edh.EdhConnector`.
- **Interface (`tools.py`, `server.py`)**: MCP server definitions (FastMCP / MCPServer), structured parameter validation, correlation ID tracking, and redacted error handling.

---

## Directory Structure

```
gcpapp/
├── requirements.txt                   # Frozen & bounded platform dependencies
├── pyproject.toml                     # Platform packaging configuration
├── README.md                          # Architecture & integration documentation
├── API_DOCUMENTATION.md               # 12-tool OpenAPI/JSON-RPC catalog
├── .env.example                       # Environment template
├── .env                               # Active configuration
├── claude_desktop_config.json         # Ready-to-copy Claude Desktop config
├── .cursor/
│   └── mcp.json                       # Cursor IDE tool configuration
├── .vscode/
│   ├── launch.json                    # VS Code 1-click debug configurations
│   └── settings.json                  # Workspace editor settings
├── chat_app/                          # Intelligent MCP Chat Agent Application
│   ├── main.py                        # FastAPI chat server (:8050)
│   ├── config.py                      # Chat app settings (host, port, mode)
│   ├── sample_questions.py            # Pre-configured enterprise prompts
│   ├── agent/
│   │   ├── router.py                  # Intent classifier & tool selector
│   │   └── executor.py                # Multi-MCP execution engine
│   └── static/                        # Chat frontend (HTML, CSS, Vanilla JS)
├── client_app/                        # Multi-MCP Client Dashboard & Terminal CLI
│   ├── main.py                        # FastAPI web client server (:8000)
│   ├── cli_main.py                    # Interactive terminal client
│   ├── application/                   # Gateway ports & use cases
│   ├── infrastructure/                # Local in-process MCP client gateway
│   └── presentation/                  # Web dashboard static assets & REST API
├── config/
│   └── settings.py                    # Pydantic BaseSettings (MOCK_MODE, SDK credentials)
├── core/
│   ├── exceptions.py                  # Domain, Infrastructure, and Tool exception hierarchy
│   ├── logging.py                     # Structured JSON logger with correlation context
│   └── factory.py                     # Dependency Injection GatewayFactory (Singleton)
├── common/
│   ├── domain/value_objects.py        # CustomerId, MachineId, SiteId, CorrelationId
│   └── infrastructure/resilience.py   # Tenacity backoff decorators & circuit breaker
├── mcp/
│   ├── __init__.py                    # Namespace package extension
│   ├── customer_mcp/
│   │   ├── domain/entities.py         # Customer, Address, AccountRep, Entitlements
│   │   ├── application/ports.py       # CustomerReaderPort, EntitlementPort, MachinePort
│   │   ├── application/use_cases.py   # Profile, Search, Entitlements, Fleet queries
│   │   ├── infrastructure/
│   │   │   ├── mock_adapter.py        # High-fidelity mock customer repository
│   │   │   └── sdk_adapter.py         # EdhConnector API Hub adapter
│   │   ├── tools.py                   # 4 FastMCP customer tools
│   │   └── server.py                  # FastMCP customer server entrypoint
│   ├── machine_mcp/
│   │   ├── domain/entities.py         # Machine, HardwareSpec, Location, MaintenanceRecord
│   │   ├── application/ports.py       # MachineReaderPort, MaintenancePort, OperationsPort
│   │   ├── application/use_cases.py   # Fleet, Specs, Maintenance, State updates
│   │   ├── infrastructure/
│   │   │   ├── mock_adapter.py        # High-fidelity machine repository
│   │   │   └── sdk_adapter.py         # EdhConnector state and dataset adapter
│   │   ├── tools.py                   # 4 FastMCP machine tools
│   │   └── server.py                  # FastMCP machine server entrypoint
│   └── telemetry_mcp/
│       ├── domain/entities.py         # MetricPoint, LatestMetrics, Alert, FaultCode
│       ├── application/ports.py       # TelemetryQueryPort, AlertPort, DiagnosticsPort
│       ├── application/use_cases.py   # Metrics, Range downsampling, Alerts, Fault trees
│       ├── infrastructure/
│       │   ├── mock_adapter.py        # Deterministic wave generator & 15 fault codes
│       │   └── sdk_adapter.py         # EdhConnector time-series adapter
│       ├── tools.py                   # 4 FastMCP telemetry tools
│       └── server.py                  # FastMCP telemetry server entrypoint
├── scripts/
│   ├── verify_mcps.py                 # Smoke & integration verification harness
│   └── test_tool.py                   # Single-tool interactive CLI runner
└── tests/
    ├── conftest.py                    # Pytest DI fixtures and mock settings
    ├── unit/                          # 95 unit tests (domain, use cases, chaos, chat, sec)
    └── integration/                   # 4 cross-MCP relational consistency tests
```

---

## Installation & Setup

### 1. Prerequisites
- Python 3.11 or higher
- The built `rag_platform_sdk` wheel (`rag_platform_sdk-0.2.0-py3-none-any.whl`) located in `C:\stash\SDK\rag-platform-sdk\dist\` (required for live SDK mode)

### 2. Virtual Environment Setup
```powershell
cd C:\stash\gcpapp

# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install the internal SDK wheel (when connecting to live Enterprise Data Hub)
pip install C:\stash\SDK\rag-platform-sdk\dist\rag_platform_sdk-0.2.0-py3-none-any.whl

# Install dependencies from requirements.txt
pip install -r requirements.txt
```

---

## Environment Configuration

Copy `.env.example` to `.env`:
```ini
# Toggle between synthetic offline mock and live enterprise SDK
DATA_PROVIDER_MODE=MOCK

# Structured Logging
LOG_LEVEL=INFO
LOG_FORMAT=JSON

# SDK Hub Connection (used when DATA_PROVIDER_MODE=SDK)
SDK_BASE_URL=https://edh-api.internal.corp
SDK_API_KEY=prod-sec-key-xxxx
SDK_TENANT_ID=tenant-enterprise-01
SDK_TIMEOUT_SECONDS=30
SDK_MAX_RETRIES=3

# Ports
CUSTOMER_MCP_PORT=8001
MACHINE_MCP_PORT=8002
TELEMETRY_MCP_PORT=8003
```

### Switching Between MOCK and SDK Modes
Zero code changes required. Set `DATA_PROVIDER_MODE`:
- `DATA_PROVIDER_MODE=MOCK`: Instant, offline operation with deterministic, relationally consistent datasets.
- `DATA_PROVIDER_MODE=SDK`: GatewayFactory routes all data access through `rag_platform_sdk.connectors.edh.EdhConnector`.

---

## Intelligent MCP Chat Agent (`chat_app`)

An AI-driven assistant that dynamically classifies user questions, selects the appropriate MCP tools across Customer, Machine, and Telemetry servers, and renders formatted responses:

### Launch the Chat Agent
```powershell
python -m chat_app.main
```
Open **`http://localhost:8050`** in your browser:
- **Natural Language Tool Routing**: Ask questions in plain English (e.g. *"Show profile for CUST-001001"*, *"What machines are in CRITICAL status?"*, *"Diagnose fault code ERR-VIB-001"*).
- **Interactive Prompt Suggestions**: Click any pre-configured question chip to test tool execution instantly.
- **Mode Toggle (MOCK / SDK)**: Switch execution mode live without restarting the server.
- **Execution Telemetry**: View the invoked tool, latency in milliseconds, status envelopes, and raw output.

---

## MCP Client Application (`client_app`)

The project includes an enterprise client application with both a **Web GUI Dashboard** and an **Interactive Terminal Menu**:

### 1. Launch the Interactive Web Dashboard
```powershell
python -m client_app.main
```
Open **`http://localhost:8000`** in your browser:
- **Visual Tool Caller**: Select any tool from `CustomerMCP`, `MachineMCP`, or `TelemetryMCP`. Form inputs are auto-generated with editable defaults.
- **MOCK / SDK Mode Switcher**: Toggle live between synthetic fixtures and SDK backend.
- **Pipeline Testing**: Run pre-configured end-to-end integration scenarios (e.g. *Customer 360*, *Machine Fleet Lifecycle*, *Telemetry Anomaly & Fault Diagnosis*).
- **REST API & Swagger Docs**: Browse `/docs` for standard OpenAPI documentation.

### 2. Launch the Terminal Interactive Client
```powershell
python -m client_app.cli_main
```
An interactive terminal menu enabling you to select servers, inspect tools, provide custom JSON arguments, and view execution latency and payload output.

---

## Server Execution (`stdio` Mode)

Each server runs as an independent process communicating over standard input/output (`stdio`) for CLI/IDE agents:

```powershell
# Run CustomerMCP
python -m mcp.customer_mcp.server

# Run MachineMCP
python -m mcp.machine_mcp.server

# Run TelemetryMCP
python -m mcp.telemetry_mcp.server
```

---

## Client Integration Guides

### 1. Claude Desktop
Add to `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "customer-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.customer_mcp.server"],
      "cwd": "C:\\stash\\gcpapp",
      "env": { "DATA_PROVIDER_MODE": "MOCK" }
    },
    "machine-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.machine_mcp.server"],
      "cwd": "C:\\stash\\gcpapp",
      "env": { "DATA_PROVIDER_MODE": "MOCK" }
    },
    "telemetry-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.telemetry_mcp.server"],
      "cwd": "C:\\stash\\gcpapp",
      "env": { "DATA_PROVIDER_MODE": "MOCK" }
    }
  }
}
```

### 2. Cursor IDE
Place in `C:\stash\gcpapp\.cursor\mcp.json` or global `%USERPROFILE%\.cursor\mcp.json`:
```json
{
  "mcpServers": {
    "customer-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.customer_mcp.server"],
      "cwd": "C:\\stash\\gcpapp"
    },
    "machine-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.machine_mcp.server"],
      "cwd": "C:\\stash\\gcpapp"
    },
    "telemetry-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.telemetry_mcp.server"],
      "cwd": "C:\\stash\\gcpapp"
    }
  }
}
```

### 3. Google Antigravity
Add to `C:\Users\<user>\.gemini\antigravity\mcp.json`:
```json
{
  "mcpServers": {
    "customer-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.customer_mcp.server"],
      "cwd": "C:\\stash\\gcpapp"
    },
    "machine-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.machine_mcp.server"],
      "cwd": "C:\\stash\\gcpapp"
    },
    "telemetry-mcp": {
      "command": "C:\\stash\\gcpapp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.telemetry_mcp.server"],
      "cwd": "C:\\stash\\gcpapp"
    }
  }
}
```

### 4. Visual Studio Code
A ready-to-run `.vscode/launch.json` is provided with pre-configured debugging profiles for:
- `Chat: Intelligent Agent UI (http://localhost:8050)`
- `Client: Web Dashboard (http://localhost:8000)`
- `Client: Interactive Terminal Menu`
- `MCP: Customer Server`, `MCP: Machine Server`, `MCP: Telemetry Server`
- `Script: Run Tool with Custom Args` (`scripts/test_tool.py`)
- `Script: Verify All MCPs (Smoke Test)` (`scripts/verify_mcps.py`)
- `Test: Pytest Test Suite`

---

## Verification & Testing

### 1. Standalone Verification Harness
Run the health matrix verification script:
```powershell
python scripts/verify_mcps.py
```
Output:
```
===============================================================================================
  ENTERPRISE MULTI-MCP PLATFORM: INTEGRATION & HEALTH VERIFICATION
  Mode: DATA_PROVIDER_MODE=MOCK | Standards: Fortune 10 Clean Architecture
===============================================================================================
  STATUS     | SERVER             | TOOL NAME                            | OUTPUT / EVIDENCE
-----------------------------------------------------------------------------------------------
[1/3] Verifying CustomerMCP (customer-mcp-server)...
  ✅ PASS     | CustomerMCP        | customer_get_profile                 | Company: Nexus Industrial Corp
  ✅ PASS     | CustomerMCP        | customer_search                      | Found 1 matching records
  ✅ PASS     | CustomerMCP        | customer_get_entitlements            | Active SLAs: 1
  ✅ PASS     | CustomerMCP        | customer_get_assigned_machines       | Machines: MCH-A1B2-C3D4, MCH-E5F6-G7H8

[2/3] Verifying MachineMCP (machine-mcp-server)...
  ✅ PASS     | MachineMCP         | machine_get_details                  | Model: INDUSTRIAL-X500, Spec: 32C
  ✅ PASS     | MachineMCP         | machine_list_fleet                   | Fleet count: 9 machines
  ✅ PASS     | MachineMCP         | machine_get_maintenance_history      | Records: 3, Cost: $1,950.00
  ✅ PASS     | MachineMCP         | machine_update_operational_state     | Audit ID: 7106c5e4... (New state: MAINTENANCE)

[3/3] Verifying TelemetryMCP (telemetry-mcp-server)...
  ✅ PASS     | TelemetryMCP       | telemetry_get_latest_metrics         | Sensors: {'vibration': 0.6, 'temperature': 55.0, 'rpm': 2016.0}
  ✅ PASS     | TelemetryMCP       | telemetry_query_range                | Series: 4 metrics, 24 points each
  ✅ PASS     | TelemetryMCP       | telemetry_get_active_alerts          | Critical alert: Vibration velocity exceeds ISO 10816 CRITICAL...
  ✅ PASS     | TelemetryMCP       | telemetry_diagnose_fault_code        | Fault: Motor Overheat (Repair: 4.0h)

===============================================================================================
  EXECUTION SUMMARY: 12/12 Tools Passed | 0 Failed
  RESULT: ALL MCP CAPABILITIES VERIFIED (100% HEALTH CHECK PASSED)
===============================================================================================
```

### 2. Single-Tool Interactive Runner
Test and debug individual MCP tools with custom input JSON payloads:
```powershell
# Example: Invoke customer profile tool
python scripts/test_tool.py customer_get_profile --args '{"customer_id": "CUST-001001"}' --mode MOCK

# Example: Diagnose a fault code
python scripts/test_tool.py telemetry_diagnose_fault_code --args '{"fault_code": "ERR-VIB-001"}' --mode MOCK
```

### 3. Pytest Test Suite
Run the 99 automated unit and integration tests:
```powershell
pytest tests/ -v
```
Test suite coverage breakdown (99 tests across 10 modules):
- **`test_mock_integration.py` (4 tests)**: Cross-MCP relational consistency and FastMCP tool response envelopes.
- **`test_critical_fixes.py` (26 tests)**: Security sanitization, credential leak protection, input bounds, singleton lifetimes, and exception preservation.
- **`test_chaos_failure_modes.py` (8 tests)**: Resilience under network failure, retry exhaustion, circuit breakers, and metrics export.
- **`test_chat_app.py` (10 tests)**: Intent classification, router tool selection, chat endpoints, and execution flows in MOCK/SDK modes.
- **`test_client_app.py` (7 tests)**: Client gateway execution, tool invocation, and scenario pipelines.
- **`test_customer_domain.py` & `test_customer_usecases.py` (16 tests)**: Value object parsing, customer profiles, SLA entitlements, and machine associations.
- **`test_machine_usecases.py` (8 tests)**: Fleet queries, status filters, hardware specifications, and state transitions.
- **`test_telemetry_usecases.py` (9 tests)**: Real-time sensor metrics, windowed range queries, active alerts, and diagnostic fault trees.
- **`test_sdk_adapters.py` (11 tests)**: `EdhConnector` SDK adapters, query parameter mapping, pagination, and error handling.
