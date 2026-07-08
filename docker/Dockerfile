# Multi-stage build: uv resolves and installs into a self-contained venv,
# the runtime stage carries no build tooling.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependency layer first: source changes do not invalidate the resolve.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=builder /app /app
EXPOSE 8000
CMD ["uvicorn", "aegis.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
