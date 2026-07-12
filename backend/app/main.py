import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .routers import (
    activity,
    ai,
    ai_settings,
    articles,
    auth,
    catalog,
    devices,
    feeds,
    integrations,
    interests,
    projects,
    shares,
    usage,
    users,
)

logging.basicConfig(level=logging.INFO)

API_VERSION = "0.1.0"
# Bumped only when the API changes incompatibly; mobile clients compare it
# against the newest version they understand and prompt for an app update.
MIN_CLIENT_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="NewsRead API", version=API_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Next-Cursor"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(feeds.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(articles.router, prefix="/api")
app.include_router(shares.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(ai_settings.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(integrations.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(interests.router, prefix="/api")


@app.get("/api/health")
async def health():
    """Unauthenticated probe; mobile onboarding uses `app` to confirm the URL
    points at a NewsRead server before asking for credentials."""
    return {
        "status": "ok",
        "app": "newsread",
        "version": API_VERSION,
        "min_client_version": MIN_CLIENT_VERSION,
    }
