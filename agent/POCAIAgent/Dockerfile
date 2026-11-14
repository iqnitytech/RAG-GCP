FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt google-genai simple-salesforce
COPY src ./src
COPY config ./config
RUN useradd -m app
USER app
# chat-api (default):   docker run ...
# an MCP service:       docker run -e MCP_SERVICE=machine ... --entrypoint uvicorn ... mcp_servers.app:app
ENV AGENTS_CONFIG=/app/config/agents.yaml
CMD ["sh", "-c", "cd src && exec uvicorn ${APP_MODULE:-main:app} --host 0.0.0.0 --port ${PORT:-8080}"]
