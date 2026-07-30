# mm

npm install
ng serve

uv run fastapi dev

RQ worker (not `rq worker` - that entry point skips the logging setup):

uv run python -m app.worker

Log verbosity is LOG_LEVEL in backend/.env (DEBUG locally, INFO deployed).