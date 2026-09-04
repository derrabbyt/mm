# mm

## Frontend

```bash
cd frontend
npm install
npm run api:generate   # needs the backend running; output is gitignored
ng serve
```

## Backend

```bash
cd backend
docker compose up                            # postgres, redis, photon
uv run fastapi dev app/api/main.py           # the API
uv run python -m app.modules.demo.worker             # rq worker, for request-triggered work
```

Scheduled jobs run one at a time and exit:

```bash
uv run python -m app.jobs.runner --list
uv run python -m app.jobs.runner scrape-events
uv run python -m app.jobs.runner scrape-events --every 3600   # development only
```

The same image serves all of it; compose carries the containers behind
profiles, so a bare `docker compose up` still brings up infrastructure only:

```bash
docker compose --profile app up    # + api and rq worker
docker compose --profile jobs up   # + the scheduled jobs
```

See `backend/docs/architecture.md` for the module layout and
`backend/docs/auth-and-db-patterns.md` for the conventions inside a module.

![Architecture Diagram](<frontend/public/architecture v1.png>)

![ERD](<frontend/public/erd v1.png>)
