# Five containers from one target, and a sixth from its own.
#
# The API, the RQ worker and three of the four scheduled jobs run the same code
# with a different command - see docker-compose.yml and app/jobs/runner.py. The
# bake does not. It is a three-hour, memory-hungry job hosting a JVM, which is a
# different runtime from a web API, so it gets a target carrying the Java
# runtime, the routing engine's Python package and its jar. The other five pay
# for none of that, and the API has never invoked any of it.
#
# A bare `docker build .` would build the last stage, so name the one you want:
#
#   docker build --target app .
#   docker build --target matrix .
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS app

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


# --- the bake ---------------------------------------------------------------
FROM app AS matrix

# A JVM, because the routing engine is a Java library behind a Python wrapper.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# The engine's own packages. Only this target installs them.
RUN uv sync --frozen --no-dev --group matrix

# The jar, in the image rather than fetched at run time. The engine resolves its
# class path while being imported and downloads a 64 MB jar when it cannot find
# one, and containers on the development host cannot resolve hostnames at all -
# so a run-time download is not an option, and neither is downloading it here.
# `scripts/fetch-r5-jar.sh` puts it in place, and is the one file that pins its
# version and digest - `etc/r5py.yml` only has to name the path.
COPY vendor/ /opt/r5/
# Which names that jar, pins the JVM heap, and keeps both out of the engine's
# own cache - which deletes what it holds after a fortnight, including the jar a
# weekly job would then be unable to replace.
COPY etc/r5py.yml /etc/r5py.yml
RUN mkdir -p /srv/tmp/r5py

CMD ["python", "-m", "app.jobs.runner", "bake-matrices"]
