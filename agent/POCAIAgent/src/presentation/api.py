from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from container import Container, build_container
from domain.errors import AuthenticationError, AuthorizationError, DomainError, NotFoundError, UpstreamError
from domain.ports import UserContext
from settings import Settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
bearer = HTTPBearer(auto_error=False)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class SkillInvokeRequest(BaseModel):
    args: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    agents_used: list[str]
    errors: dict[str, str]
    skills_selected: list[str] = []


def get_container(request: Request) -> Container:
    return request.app.state.container


async def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    container: Container = Depends(get_container),
) -> UserContext:
    if cred is None:
        raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        return await container.validator.validate(cred.credentials)
    except AuthenticationError:
        raise HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or build_container(Settings.from_env())
        yield

    app = FastAPI(title="AI Chat Platform", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=[], allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"]
    )

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        return response

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/agents")
    async def agents(user: UserContext = Depends(current_user), c: Container = Depends(get_container)):
        return [
            {
                "name": a.name, "description": a.description, "version": getattr(a, "version", ""),
                "owner": getattr(a, "owner", ""), "tags": list(getattr(a, "tags", ())),
                "consumes": list(getattr(a, "consumes", ())), "produces": list(getattr(a, "produces", ())),
            }
            for a in c.registry.allowed_for(user)
        ]

    @app.get("/skills")
    async def skills(user: UserContext = Depends(current_user), c: Container = Depends(get_container)):
        return [
            {
                "id": s.id, "name": s.name, "agent": s.agent, "description": s.description,
                "tags": list(s.tags), "examples": list(s.examples), "input_schema": s.input_schema,
            }
            for s in await c.skills.list_for(user)
        ]

    @app.post("/skills/{skill_id}/invoke")
    async def invoke_skill(
        skill_id: str, body: SkillInvokeRequest, user: UserContext = Depends(current_user),
        c: Container = Depends(get_container),
    ):
        try:
            return {"result": await c.skills.invoke(skill_id, body.args, user)}
        except NotFoundError as exc:
            raise HTTPException(404, str(exc))
        except AuthorizationError as exc:
            raise HTTPException(403, str(exc))
        except UpstreamError as exc:
            raise HTTPException(502, str(exc))
        except DomainError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest, user: UserContext = Depends(current_user), c: Container = Depends(get_container)
    ):
        try:
            result = await c.chat.execute(body.message, user)
        except DomainError as exc:
            raise HTTPException(400, str(exc))
        return ChatResponse(answer=result.answer, agents_used=result.agents_used, errors=result.errors,
                            skills_selected=result.skills_selected)

    return app
