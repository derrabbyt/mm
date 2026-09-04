# One image, many roles. The API, the RQ worker and every scheduled job run the
# same code with a different command - see docker-compose.yml and
# app/jobs/runner.py.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv

# Dependencies resolve from the lockfile alone, so editing application code
# does not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN uv sync --frozen --no-dev

# The travel-time dataset is a mounted volume, not a layer: it is 1.4 GB, only
# the API reads it, and it changes on its own schedule (see the matrix job).
CMD ["fastapi", "run", "app/api/main.py", "--port", "8000"]
