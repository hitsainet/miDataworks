"""The FastAPI app with Socket.IO mounted (ADR-002, ADR-008, ADR-013; Foundation task 3.x).

``app`` is the ASGI entry point: Socket.IO wraps FastAPI, answering on ``/api/ws/socket.io`` and
passing everything else through. ``fastapi_app`` is the FastAPI instance (tests read its routes).
"""

from __future__ import annotations

import socketio
from fastapi import FastAPI

from .api.v1.router import ROUTERS
from .core.config import get_settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging, register_secret
from .core.websocket import SOCKETIO_PATH, sio


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    register_secret(settings.settings_encryption_key.get_secret_value())
    register_secret(settings.internal_api_secret.get_secret_value())
    application = FastAPI(
        title="miDataworks",
        version="0.1.0",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    install_error_handlers(application)
    for router in ROUTERS:
        application.include_router(router)
    return application


fastapi_app = create_app()
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path=SOCKETIO_PATH.lstrip("/"))
