# Documentation

| Document | Contents |
|---|---|
| [01-solution-architecture.md](01-solution-architecture.md) | Context, clean-architecture layers, patterns, components, data model, security model, permissions, deployment, implemented vs designed, how to extend |
| [02-workflow-and-functional-flow.md](02-workflow-and-functional-flow.md) | End-to-end workflow, functional flow by step, the three capabilities, chained example, skill governance lifecycle, error map, configuration switches |
| [03-query-routing-and-agent-selection.md](03-query-routing-and-agent-selection.md) | How a question picks its skills/agents: every function in call order, the LLM and mock algorithms, fallback strategies, worked examples, tuning |
| [04-sequence-diagrams.md](04-sequence-diagrams.md) | Mermaid sequence diagrams: chat request, skill selection, chained agents, fallback, skill discovery and approval, security handshake, direct invoke, partial failure, start-up |

Related: [project README](../README.md) (run locally, settings, catalog, evaluation), [ARCHITECTURE.md](../ARCHITECTURE.md) (original design proposal; superseded by these documents where they differ), [config/agents.yaml](../config/agents.yaml), [config/skills/](../config/skills), [routing evaluation cases](../tests/eval/routing_cases.json).

Diagrams are Mermaid: they render on GitHub and in the VS Code Markdown preview.
