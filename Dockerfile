# Runs the server over Streamable HTTP at http://localhost:8000/mcp
FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home app

USER app
EXPOSE 8000
CMD ["helpdesk-mcp", "--http", "--host", "0.0.0.0", "--port", "8000", "--db", "/home/app/helpdesk.db"]
