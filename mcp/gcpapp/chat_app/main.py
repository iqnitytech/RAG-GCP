from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chat_app.agent.executor import ChatAgentExecutor
from chat_app.agent.router import McpIntentRouter
from chat_app.config import ChatAppSettings
from chat_app.sample_questions import SAMPLE_QUESTIONS, SampleQuestion

settings = ChatAppSettings()
app = FastAPI(
    title="Enterprise MCP Chat Agent",
    description="Intelligent Chat Agent that selects and executes MCP tools based on user questions.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = McpIntentRouter()
executor = ChatAgentExecutor()

STATIC_DIR = Path(__file__).parent / "static"


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, description="Natural language user question")
    mode: str = Field(default="MOCK", description="Data provider mode: MOCK or SDK")


@app.get("/api/health")
async def health_check() -> dict[str, Any]:
    return {"status": "healthy", "service": "mcp_chat_agent", "mode": settings.DEFAULT_MODE}


@app.get("/api/sample-questions")
async def list_sample_questions() -> list[dict[str, Any]]:
    return [
        {
            "category": q.category,
            "question": q.question,
            "target_tool": q.target_tool,
            "server_id": q.server_id,
            "description": q.description,
            "expected_args": q.expected_args,
        }
        for q in SAMPLE_QUESTIONS
    ]


@app.post("/api/chat")
async def process_chat(request: ChatMessageRequest) -> dict[str, Any]:
    try:
        # 1. Route user message to best matching MCP tool & parameters
        intent = router.route(request.message)

        # 2. Execute tool against local MCP server in requested mode
        result = await executor.execute(intent, mode=request.mode)
        return result
    except Exception as exc:
        return {
            "reply": f"⚠️ **Error processing request:** {exc}",
            "tool_used": "none",
            "server_id": "none",
            "arguments": {},
            "reasoning": "Exception occurred during tool routing or execution",
            "confidence": 0.0,
            "execution_time_ms": 0.0,
            "status": "error",
            "mode": request.mode,
            "result_payload": {"error": str(exc)},
        }


# Mount static assets
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Enterprise MCP Chat Agent API is running. Static UI not found."}


if __name__ == "__main__":
    uvicorn.run(
        "chat_app.main:app",
        host=settings.CHAT_HOST,
        port=settings.CHAT_PORT,
        reload=False,
    )
