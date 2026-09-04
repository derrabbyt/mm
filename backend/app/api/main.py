"""The HTTP shell: which modules are exposed over the API."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis_fastapi import FastAPIRedis

from ..core.config import settings
from ..core.exception_handlers import register_exception_handlers
from ..core.logging import setup_logging
from ..modules.accounts.router import router as accounts_router
from ..modules.demo.router import router as demo_router
from ..modules.events.router import router as events_router
from ..modules.meetups.router import router as meetups_router
from ..modules.rendezvous.router import router as rendezvous_router

# Rendezvous and events are sub-resources of a meetup, but live in their own
# modules - so the nesting is assembled here rather than by those modules.
MEETUP = "/api/meetups/{meetup_id}"


def _meetups_tree() -> APIRouter:
    """Order matters: `meetups` owns /{meetup_id}, so a route added there with a
    second path segment would shadow the ones registered below."""
    tree = APIRouter()
    tree.include_router(meetups_router)
    tree.include_router(rendezvous_router, prefix=MEETUP, tags=["meetups"])
    tree.include_router(events_router, prefix=MEETUP, tags=["meetups"])
    return tree


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url="/api/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    FastAPIRedis(app).lifespan().caching()

    app.include_router(demo_router)
    app.include_router(_meetups_tree())
    app.include_router(accounts_router)

    register_exception_handlers(app)

    return app


app = create_app()
