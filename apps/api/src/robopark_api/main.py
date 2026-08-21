from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from robopark_api.config import get_settings
from robopark_api.routers import health


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Robopark API", version="0.1.0")
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
