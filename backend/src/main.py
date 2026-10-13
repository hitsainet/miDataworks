"""The FastAPI app with Socket.IO mounted (ADR-002, ADR-008, ADR-013; Foundation task 3.x).

``app`` is the ASGI entry point: Socket.IO wraps FastAPI, answering on ``/api/ws/socket.io`` and
passing everything else through. ``fastapi_app`` is the FastAPI instance (tests read its routes).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI

from .api.v1.router import ROUTERS
from .core.agent_origin import AgentOriginMiddleware, mark_gated_routes
from .core.config import get_settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging, register_secret
from .core.websocket import SOCKETIO_PATH, sio


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Process start: build the operator registry and install it as 002's port (FTID 003 3.4)."""
    from .operators.registry import install_process_registry

    install_process_registry()
    from .services import labeling_ports

    labeling_ports.install()  # feature 005 into 003's endpoint port and 002's bindings
    from .services.generation import install as generation_install

    generation_install.install()  # feature 007 into 002's bindings and 006's metric registry
    yield


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
        lifespan=lifespan,
    )
    install_error_handlers(application)
    # Every request: a malformed agent header is refused, agent activity is recorded (010 3.1).
    application.add_middleware(AgentOriginMiddleware)
    for router in ROUTERS:
        mark_gated_routes(router)  # before include: include_router copies openapi_extra
        application.include_router(router)
    return application


fastapi_app = create_app()
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path=SOCKETIO_PATH.lstrip("/"))
