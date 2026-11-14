"""Runtime configuration (12-factor, env based). Secrets come from Secret Manager mounted as env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    env: str = "local"                      # local | dev | prod
    auth_mode: str = "dev"                  # dev | entra
    entra_tenant_id: str = ""
    entra_audience: str = ""
    llm_mode: str = "mock"                  # mock | vertex
    vertex_project: str = ""
    vertex_location: str = "us-central1"
    vertex_model: str = ""                  # legacy: if set, used for every role not set explicitly below
    vertex_model_routing: str = "gemini-2.5-flash"    # agent selection (cheap/fast)
    vertex_model_tools: str = "gemini-2.5-flash"      # tool + argument selection (cheap/fast)
    vertex_model_synthesis: str = "gemini-2.5-pro"    # final answer (quality)
    vertex_api_key: str = ""                # optional (Vertex express mode); prefer ADC. Inject from Secret Manager
    llm_timeout_s: float = 30.0
    routing: str = "hybrid"                 # keyword | skill | llm | hybrid (keyword -> skill -> llm)
    sf_mode: str = "mock"                   # mock | sdk
    sf_username: str = ""
    sf_password: str = ""
    sf_security_token: str = ""
    sf_domain: str = "login"
    agents_config: str = "config/agents.yaml"
    user_context_secret: str = "local-dev-secret"
    mcp_auth: str = "none"                  # none | google (verify Google ID token)
    mcp_allowed_caller_sa: str = ""         # e.g. sa-chat-api@project.iam.gserviceaccount.com
    mcp_timeout_s: float = 15.0
    registry_store: str = "yaml"            # yaml | firestore
    registry_prefix: str = "registry"
    firestore_project: str = ""
    skills_auto_approve: bool = True        # local only: new/changed skills go live without review
    skills_catalog_dir: str = ""            # default: <dir of agents_config>/skills
    skill_selection: str = "llm"            # llm (LLM picks skills, falls back to ROUTING) | off
    skill_max_steps: int = 4                # hard cap on skills chosen for one question
    skill_shortlist_size: int = 12          # only shortlist (embeddings) when the catalog is larger than this
    embedder: str = "hash"                  # hash (local, offline) | vertex | none
    vertex_model_embedding: str = "text-embedding-005"
    @classmethod
    def from_env(cls) -> "Settings":
        def g(key: str, default: str) -> str:
            return os.environ.get(key, default)

        s = cls(
            env=g("APP_ENV", "local"),
            auth_mode=g("AUTH_MODE", "dev"),
            entra_tenant_id=g("ENTRA_TENANT_ID", ""),
            entra_audience=g("ENTRA_AUDIENCE", ""),
            llm_mode=g("LLM_MODE", "mock"),
            vertex_project=g("VERTEX_PROJECT", ""),
            vertex_location=g("VERTEX_LOCATION", "us-central1"),
            vertex_model=g("VERTEX_MODEL", ""),
            vertex_model_routing=g("VERTEX_MODEL_ROUTING", g("VERTEX_MODEL", "gemini-2.5-flash")),
            vertex_model_tools=g("VERTEX_MODEL_TOOLS", g("VERTEX_MODEL", "gemini-2.5-flash")),
            vertex_model_synthesis=g("VERTEX_MODEL_SYNTHESIS", g("VERTEX_MODEL", "gemini-2.5-pro")),
            vertex_api_key=g("VERTEX_API_KEY", ""),
            llm_timeout_s=float(g("LLM_TIMEOUT_S", "30")),
            routing=g("ROUTING", "hybrid"),
            sf_mode=g("SF_MODE", "mock"),
            sf_username=g("SF_USERNAME", ""),
            sf_password=g("SF_PASSWORD", ""),
            sf_security_token=g("SF_SECURITY_TOKEN", ""),
            sf_domain=g("SF_DOMAIN", "login"),
            agents_config=g("AGENTS_CONFIG", "config/agents.yaml"),
            user_context_secret=g("USER_CONTEXT_SECRET", "local-dev-secret"),
            mcp_auth=g("MCP_AUTH", "none"),
            mcp_allowed_caller_sa=g("MCP_ALLOWED_CALLER_SA", ""),
            mcp_timeout_s=float(g("MCP_TIMEOUT_S", "15")),
            registry_store=g("REGISTRY_STORE", "yaml"),
            registry_prefix=g("REGISTRY_PREFIX", "registry"),
            firestore_project=g("FIRESTORE_PROJECT", ""),
            skills_auto_approve=g("SKILLS_AUTO_APPROVE", "true").lower() == "true",
            skills_catalog_dir=g("SKILLS_CATALOG_DIR", ""),
            skill_selection=g("SKILL_SELECTION", "llm"),
            skill_max_steps=int(g("SKILL_MAX_STEPS", "4")),
            skill_shortlist_size=int(g("SKILL_SHORTLIST_SIZE", "12")),
            embedder=g("EMBEDDER", "hash"),
            vertex_model_embedding=g("VERTEX_MODEL_EMBEDDING", "text-embedding-005"),
        )
        s.validate()
        return s

    def validate(self) -> None:
        if self.env == "prod":
            if self.auth_mode != "entra":
                raise RuntimeError("AUTH_MODE must be 'entra' in prod")
            if self.user_context_secret == "local-dev-secret":
                raise RuntimeError("USER_CONTEXT_SECRET must be set in prod")
            if self.mcp_auth != "google":
                raise RuntimeError("MCP_AUTH must be 'google' in prod")
            if self.registry_store != "firestore":
                raise RuntimeError("REGISTRY_STORE must be 'firestore' in prod")
            if self.skills_auto_approve:
                raise RuntimeError("SKILLS_AUTO_APPROVE must be 'false' in prod")
        if self.llm_mode == "vertex" and not (self.vertex_project or self.vertex_api_key):
            raise RuntimeError("VERTEX_PROJECT (ADC) or VERTEX_API_KEY is required for LLM_MODE=vertex")
        if self.embedder == "vertex" and not (self.vertex_project or self.vertex_api_key):
            raise RuntimeError("VERTEX_PROJECT or VERTEX_API_KEY is required for EMBEDDER=vertex")
        if self.skill_selection not in ("llm", "off") or self.embedder not in ("hash", "vertex", "none"):
            raise RuntimeError("Invalid SKILL_SELECTION or EMBEDDER")
        if not 1 <= self.skill_max_steps <= 10:
            raise RuntimeError("SKILL_MAX_STEPS must be between 1 and 10")
        if self.auth_mode == "entra" and not (self.entra_tenant_id and self.entra_audience):
            raise RuntimeError("ENTRA_TENANT_ID and ENTRA_AUDIENCE are required for AUTH_MODE=entra")
