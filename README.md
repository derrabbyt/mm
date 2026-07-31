# mm

npm install
ng serve

uv run fastapi dev
docker compose up

RQ worker (not `rq worker` - that entry point skips the logging setup):

uv run python -m app.worker