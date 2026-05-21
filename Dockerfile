FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTMCP_SERVER_HOST=0.0.0.0 \
    FASTMCP_SERVER_PORT=8000

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

ENTRYPOINT ["mcp-docs-tidb"]
CMD ["--transport", "streamable-http"]
