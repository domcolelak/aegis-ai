"""HTTP routes and the WebSocket progress stream."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from aegis.api.schemas import (
    AnalyzeAccepted,
    AnalyzeRequest,
    EdgeOut,
    FindingOut,
    GraphOut,
    IncidentOut,
    InvestigationOut,
    PatchOut,
    ProgressEventOut,
    SourceIn,
    SourceOut,
)

# Runtime imports on purpose: FastAPI evaluates these annotations when routes
# are registered; TYPE_CHECKING-only names would silently degrade Depends
# parameters into query parameters.
from aegis.api.service import AnalysisService
from aegis.db import (
    IncidentRepository,
    InvestigationRepository,
    SourceRepository,
)
from aegis.investigation.progress import ProgressKind

if TYPE_CHECKING:
    from aegis.api.bus import InProcessEventBus

router = APIRouter(prefix="/api/v1")

_TERMINAL = {ProgressKind.INVESTIGATION_COMPLETED, ProgressKind.INVESTIGATION_FAILED}


def _state(request: Request, name: str) -> object:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} not configured")
    return value


def get_service(request: Request) -> AnalysisService:
    return cast("AnalysisService", _state(request, "analysis_service"))


def get_incidents(request: Request) -> IncidentRepository:
    return cast("IncidentRepository", _state(request, "incidents"))


def get_investigations(request: Request) -> InvestigationRepository:
    return cast("InvestigationRepository", _state(request, "investigations"))


def get_sources(request: Request) -> SourceRepository:
    return cast("SourceRepository", _state(request, "sources"))


@router.post(
    "/incidents/analyze", response_model=AnalyzeAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def analyze(
    payload: AnalyzeRequest, service: Annotated[AnalysisService, Depends(get_service)]
) -> AnalyzeAccepted:
    incident_id, investigation_id = await service.start_analysis(payload)
    return AnalyzeAccepted(
        incident_id=incident_id,
        investigation_id=investigation_id,
        websocket=f"/ws/incidents/{incident_id}",
    )


@router.get("/incidents", response_model=list[IncidentOut])
async def list_incidents(
    incidents: Annotated[IncidentRepository, Depends(get_incidents)],
) -> list[IncidentOut]:
    return [IncidentOut.model_validate(record) for record in await incidents.list_recent()]


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
async def get_incident(
    incident_id: UUID, incidents: Annotated[IncidentRepository, Depends(get_incidents)]
) -> IncidentOut:
    record = await incidents.get(incident_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    return IncidentOut.model_validate(record)


@router.get("/incidents/{incident_id}/graph", response_model=GraphOut)
async def get_graph(
    incident_id: UUID, incidents: Annotated[IncidentRepository, Depends(get_incidents)]
) -> GraphOut:
    if await incidents.get(incident_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "incident not found")
    edges = await incidents.edges_for(incident_id)
    return GraphOut(incident_id=incident_id, edges=[EdgeOut.model_validate(edge) for edge in edges])


@router.get("/incidents/{incident_id}/investigation", response_model=InvestigationOut)
async def get_investigation(
    incident_id: UUID,
    investigations: Annotated[InvestigationRepository, Depends(get_investigations)],
) -> InvestigationOut:
    record = await investigations.latest_for_incident(incident_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no investigation for this incident")
    findings = await investigations.findings_for(record.investigation_id)
    return InvestigationOut(
        investigation_id=record.investigation_id,
        incident_id=record.incident_id,
        status=record.status,
        started_at=record.started_at,
        completed_at=record.completed_at,
        assessment=record.assessment,
        challenge=record.challenge,
        findings=[FindingOut(agent=item.agent, finding=item.finding) for item in findings],
    )


@router.get("/incidents/{incident_id}/patch", response_model=PatchOut)
async def get_patch(
    incident_id: UUID,
    investigations: Annotated[InvestigationRepository, Depends(get_investigations)],
) -> PatchOut:
    record = await investigations.latest_for_incident(incident_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no investigation for this incident")
    patch = await investigations.patch_for(record.investigation_id)
    if patch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no patch proposed for this incident")
    return PatchOut.model_validate(patch)


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def register_source(
    payload: SourceIn, sources: Annotated[SourceRepository, Depends(get_sources)]
) -> SourceOut:
    await sources.register(payload.source_id, payload.log_format)
    for record in await sources.list_all():
        if record.source_id == payload.source_id:
            return SourceOut.model_validate(record)
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "source registration failed")


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    sources: Annotated[SourceRepository, Depends(get_sources)],
) -> list[SourceOut]:
    return [SourceOut.model_validate(record) for record in await sources.list_all()]


async def incident_progress_ws(websocket: WebSocket, incident_id: UUID) -> None:
    """Streams typed ProgressEventOut frames until the investigation ends."""
    bus = cast("InProcessEventBus", websocket.app.state.bus)
    await websocket.accept()
    try:
        async with bus.subscribe(incident_id) as queue:
            while True:
                event = await queue.get()
                await websocket.send_text(ProgressEventOut.from_event(event).model_dump_json())
                if event.kind in _TERMINAL:
                    break
        await websocket.close()
    except WebSocketDisconnect:
        return
