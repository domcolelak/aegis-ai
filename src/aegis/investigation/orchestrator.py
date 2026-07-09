"""Runs the investigation: specialists in parallel, then challenge, then verdict.

Structure mirrors how a good incident review works: independent specialists
first (concurrently, in a TaskGroup -- one crashing cancels the rest), the
Devil's Advocate attacks their combined findings, and only then does the
Commander synthesize a validated RootCauseAssessment. Progress events are
published at every phase boundary for the WebSocket stream.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel

from aegis.investigation.briefs import AdvocateBrief, CommanderBrief, PatchBrief
from aegis.investigation.patching import validate_patch
from aegis.investigation.progress import NullPublisher, ProgressEvent, ProgressKind
from aegis.investigation.providers.base import TokenUsage
from aegis.investigation.tools.base import InvestigationAudit, InvestigationContext

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from aegis.inspection import RepositoryInspector
    from aegis.investigation.agents.base import Agent
    from aegis.investigation.assessment import (
        AdvocateChallenge,
        PatchProposal,
        RootCauseAssessment,
        SpecialistFinding,
    )
    from aegis.investigation.data import InvestigationDataStore
    from aegis.investigation.evidence import EvidenceBundle
    from aegis.investigation.progress import ProgressPublisher
    from aegis.investigation.tools.base import ToolExecution


@dataclass(slots=True, frozen=True)
class InvestigationResult:
    investigation_id: UUID
    assessment: RootCauseAssessment
    findings: Mapping[str, SpecialistFinding]
    challenge: AdvocateChallenge
    tool_executions: tuple[ToolExecution, ...]
    started_at: datetime
    completed_at: datetime
    usage: TokenUsage = field(default_factory=TokenUsage)
    patch: PatchProposal | None = None


class InvestigationOrchestrator:
    def __init__(
        self,
        *,
        specialists: Sequence[Agent[EvidenceBundle, SpecialistFinding]],
        advocate: Agent[AdvocateBrief, AdvocateChallenge],
        commander: Agent[CommanderBrief, RootCauseAssessment],
        publisher: ProgressPublisher | None = None,
        tool_timeout_s: float = 10.0,
        patch_engineer: Agent[PatchBrief, PatchProposal] | None = None,
        repository: RepositoryInspector | None = None,
    ) -> None:
        if not specialists:
            raise ValueError("at least one specialist agent is required")
        if patch_engineer is not None and repository is None:
            raise ValueError("a patch engineer requires a configured repository")
        self._specialists = list(specialists)
        self._advocate = advocate
        self._commander = commander
        self._publisher = publisher or NullPublisher()
        self._tool_timeout_s = tool_timeout_s
        self._patch_engineer = patch_engineer
        self._repository = repository

    async def investigate(
        self,
        data: InvestigationDataStore,
        evidence: EvidenceBundle,
        *,
        investigation_id: UUID | None = None,
    ) -> InvestigationResult:
        investigation_id = investigation_id or uuid4()
        started_at = datetime.now(tz=UTC)
        ctx = InvestigationContext(
            investigation_id=investigation_id,
            data=data,
            audit=InvestigationAudit(),
            tool_timeout_s=self._tool_timeout_s,
            repository=self._repository,
        )
        await self._publish(
            investigation_id, ProgressKind.INVESTIGATION_STARTED, "investigation started", 0.0
        )
        try:
            findings = await self._run_specialists(ctx, evidence, investigation_id)
            challenge = await self._run_agent(
                self._advocate,
                ctx,
                AdvocateBrief(evidence=evidence, findings=findings),
                investigation_id,
                progress_after=0.8,
            )
            assessment = await self._run_agent(
                self._commander,
                ctx,
                CommanderBrief(evidence=evidence, findings=findings, challenge=challenge),
                investigation_id,
                progress_after=0.92,
            )
            patch: PatchProposal | None = None
            if self._patch_engineer is not None and self._repository is not None:
                patch = await self._run_agent(
                    self._patch_engineer,
                    ctx,
                    PatchBrief(evidence=evidence, assessment=assessment, findings=findings),
                    investigation_id,
                    progress_after=0.98,
                )
                # The model proposes; deterministic validation decides.
                validate_patch(patch, self._repository)
        except BaseException:
            await self._publish(
                investigation_id,
                ProgressKind.INVESTIGATION_FAILED,
                "investigation failed",
                1.0,
            )
            raise

        await self._publish(
            investigation_id,
            ProgressKind.INVESTIGATION_COMPLETED,
            f"root cause: {assessment.root_cause} ({assessment.confidence:.0%})",
            1.0,
        )
        return InvestigationResult(
            investigation_id=investigation_id,
            assessment=assessment,
            findings=findings,
            challenge=challenge,
            tool_executions=ctx.audit.entries,
            started_at=started_at,
            completed_at=datetime.now(tz=UTC),
            usage=ctx.usage.total(),
            patch=patch,
        )

    async def _run_specialists(
        self,
        ctx: InvestigationContext,
        evidence: EvidenceBundle,
        investigation_id: UUID,
    ) -> dict[str, SpecialistFinding]:
        completed = 0
        total = len(self._specialists)
        # Overall progress reached so far; AGENT_STARTED reports the current
        # value (not a constant) so concurrent agents keep the stream monotonic.
        reached = 0.05

        async def run(
            agent: Agent[EvidenceBundle, SpecialistFinding],
        ) -> tuple[str, SpecialistFinding]:
            nonlocal completed, reached
            await self._publish(
                investigation_id,
                ProgressKind.AGENT_STARTED,
                f"{agent.name} investigating",
                reached,
                agent=agent.name,
            )
            finding = await agent.investigate(ctx, evidence)
            completed += 1
            reached = max(reached, 0.05 + 0.55 * (completed / total))
            await self._publish(
                investigation_id,
                ProgressKind.AGENT_COMPLETED,
                finding.summary[:200],
                reached,
                agent=agent.name,
            )
            return agent.name, finding

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(run(agent)) for agent in self._specialists]
        return dict(task.result() for task in tasks)

    async def _run_agent[TInput, TFinding: BaseModel](
        self,
        agent: Agent[TInput, TFinding],
        ctx: InvestigationContext,
        data: TInput,
        investigation_id: UUID,
        *,
        progress_after: float,
    ) -> TFinding:
        await self._publish(
            investigation_id,
            ProgressKind.AGENT_STARTED,
            f"{agent.name} working",
            # Small offset keeps the stream monotonic across sequential agents
            # (each start must not fall below the previous completion).
            progress_after - 0.04,
            agent=agent.name,
        )
        result = await agent.investigate(ctx, data)
        await self._publish(
            investigation_id,
            ProgressKind.AGENT_COMPLETED,
            f"{agent.name} done",
            progress_after,
            agent=agent.name,
        )
        return result

    async def _publish(
        self,
        investigation_id: UUID,
        kind: ProgressKind,
        message: str,
        progress: float,
        *,
        agent: str | None = None,
    ) -> None:
        await self._publisher.publish(
            ProgressEvent(
                investigation_id=investigation_id,
                kind=kind,
                message=message,
                progress=round(progress, 3),
                agent=agent,
            )
        )
