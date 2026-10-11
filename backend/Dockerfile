FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/srv/.venv/bin:$PATH"

WORKDIR /srv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
RUN uv sync --frozen --no-dev

CMD ["fastapi", "run", "app/api/main.py", "--port", "8000"]


FROM app AS matrix

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN uv sync --frozen --no-dev --group matrix

COPY vendor/ /opt/r5/
COPY etc/r5py.yml /etc/r5py.yml
RUN mkdir -p /srv/tmp/r5py

CMD ["python", "-m", "app.jobs.runner", "bake-matrices"]
