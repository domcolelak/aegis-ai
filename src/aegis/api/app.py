"""Composition root.

All wiring lives here and only here: engine, repositories, provider stack
(retry over semaphore over the configured provider), tool registry, agents,
memory, executor, bus, service. Everything downstream receives constructed
dependencies -- no globals, no service locators, no DI framework.

For tests, ``create_app(analysis_service=..., bus=...)`` skips the
infrastructure entirely and serves the routes against the injected seams.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response

from aegis.api.bus import InProcessEventBus
from aegis.api.routes import incident_progress_ws, router
from aegis.core.config import Settings
from aegis.db import (
    EventRepository,
    IncidentRepository,
    InvestigationRepository,
    MemoryRepository,
    SourceRepository,
    create_db_engine,
    create_session_factory,
)
from aegis.investigation.agents import (
    DatabaseInvestigator,
    DevilsAdvocate,
    IncidentCommander,
    LogAnalyst,
)
from aegis.investigation.orchestrator import InvestigationOrchestrator
from aegis.investigation.providers import RateLimitedProvider, RetryingProvider, ScriptedProvider
from aegis.investigation.providers.demo import demo_scripts
from aegis.investigation.tools import ToolRegistry, default_tools
from aegis.memory import HashingEmbedder, IncidentMemory, VoyageEmbedder
from aegis.observability import PrometheusMetrics, configure_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from aegis.api.service import AnalysisService
    from aegis.investigation.progress import ProgressPublisher
    from aegis.investigation.providers.base import LLMProvider
    from aegis.memory.embeddings import EmbeddingProvider

type RequestHandler = Callable[[Request], Awaitable[Response]]


def _build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        # Imported lazily so the offline demo never touches the SDK.
        from aegis.investigation.providers.anthropic import AnthropicProvider

        inner: LLMProvider = AnthropicProvider(model=settings.anthropic_model)
    else:
        inner = ScriptedProvider(demo_scripts())
    return RetryingProvider(RateLimitedProvider(inner, max_concurrent=settings.llm_max_concurrent))


def _build_embedder(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "voyage":
        if settings.voyage_api_key is None:
            raise ValueError("AEGIS_VOYAGE_API_KEY is required for the voyage embedder")
        return VoyageEmbedder(settings.voyage_api_key, model=settings.voyage_model)
    return HashingEmbedder()


def create_app(
    settings: Settings | None = None,
    *,
    analysis_service: AnalysisService | None = None,
    bus: InProcessEventBus | None = None,
) -> FastAPI:
    settings = settings or Settings()
    bus = bus or InProcessEventBus()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)
    metrics = PrometheusMetrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if analysis_service is not None:
            # Test mode: routes talk to the injected seams, no infrastructure.
            app.state.analysis_service = analysis_service
            yield
            return

        from aegis.api.service import IncidentAnalysisService

        engine = create_db_engine(settings.database_url)
        sessions = create_session_factory(engine)
        executor = ProcessPoolExecutor(max_workers=settings.parser_workers)
        provider = _build_provider(settings)
        registry = ToolRegistry(default_tools())

        def orchestrator_factory(publisher: ProgressPublisher) -> InvestigationOrchestrator:
            return InvestigationOrchestrator(
                specialists=[
                    LogAnalyst(provider, registry),
                    DatabaseInvestigator(provider, registry),
                ],
                advocate=DevilsAdvocate(provider, registry),
                commander=IncidentCommander(provider, registry),
                publisher=publisher,
            )

        app.state.incidents = IncidentRepository(sessions)
        app.state.investigations = InvestigationRepository(sessions)
        app.state.sources = SourceRepository(sessions)
        service = IncidentAnalysisService(
            events=EventRepository(engine),
            incidents=app.state.incidents,
            investigations=app.state.investigations,
            memory=IncidentMemory(MemoryRepository(sessions), _build_embedder(settings)),
            orchestrator_factory=orchestrator_factory,
            bus=bus,
            executor=executor,
            metrics=metrics,
        )
        app.state.analysis_service = service
        try:
            yield
        finally:
            await service.shutdown()
            executor.shutdown(wait=False, cancel_futures=True)
            await engine.dispose()

    app = FastAPI(
        title="Aegis AI",
        description="Autonomous incident investigation engine.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.bus = bus
    app.state.metrics = metrics
    app.include_router(router)
    app.add_api_websocket_route("/ws/incidents/{incident_id}", incident_progress_ws)

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["x-request-id"] = request_id
        route = request.scope.get("route")
        metrics.inc(
            "http_requests_total",
            method=request.method,
            route=getattr(route, "path", request.url.path),
            status=str(response.status_code),
        )
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return Response(content=metrics.render(), media_type="text/plain; version=0.0.4")

    return app
