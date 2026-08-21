from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.routers import auth, health, parks
from robopark_api.seed import ensure_seed_user


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with SessionLocal() as db:
            ensure_seed_user(db, settings)
        yield

    app = FastAPI(title="Robopark API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(parks.router)
    return app


app = create_app()
