from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis_fastapi import FastAPIRedis

from .core.config import settings
from .routers.meetup import router as meetup_router
from .routers.test import router as test_router

app = FastAPI(
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# redis chaching
FastAPIRedis(app).lifespan().caching()

app.include_router(test_router)
app.include_router(meetup_router)
