# Build from repository root:
#   docker build -f mcp_servers/Dockerfile .

FROM python:3.12-slim

WORKDIR /app
COPY mcp_servers/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_servers/ ./mcp_servers/

ENV PYTHONPATH=/app
ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "mcp_servers.app", "sse"]
