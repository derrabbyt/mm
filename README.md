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

Tests run against a database of their own, `<POSTGRES_DB>_test`, which the suite
creates and migrates on first run — so `uv run pytest` is the only command, and
it never touches the data the app is serving. It refuses to start if pointed at
the application's database. `TEST_DATABASE_URL` overrides where it connects.

```bash
uv run pytest
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
