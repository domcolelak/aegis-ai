# Aegis AI — Autonomous Incident Investigator

Aegis AI is an autonomous incident investigation engine written in Python. It ingests large
volumes of distributed system logs, normalizes and correlates events, constructs a weighted
causal evidence graph, and orchestrates specialized AI investigators that determine probable
root causes and propose remediation.

> **Status: under active development.** This README describes only what exists today;
> the full MVP design lives in the architecture notes and lands piece by piece.

## Implemented so far

- **Core primitives** (`aegis.core`)
  - `Channel[T]` — bounded, closable pipeline channel; the bound is what gives the ingestion
    pipeline backpressure end to end.
  - `retry_async` — exponential backoff with full jitter and a shared cross-call *retry budget*
    (a tool that diagnoses retry storms must not cause them).
  - Typed exception hierarchy rooted at `AegisError`.
- **Event domain model** (`aegis.events`) — frozen, slotted dataclasses for the hot path:
  `LogEvent`, `RawLogEvent`, `EventSignature` (masked log templates as the deduplication unit),
  `Severity`, `EventKind`, `TimeWindow`.
- **Ingestion** (`aegis.ingestion`) — `LogSource` protocol (async generators, deterministic
  `aclose` on every exit path); `FileLogSource` (chunked reads, never loads the file),
  `StructuredJsonLogSource`, `DockerReplaySource` (consumes Docker's on-disk `json-file`
  format, no daemon needed); a `TaskGroup` supervisor with fail-fast semantics.
- **Parsing & normalization** (`aegis.parsing`) — pure, picklable parse functions offloaded to
  a `ProcessPoolExecutor` (regex + template extraction is GIL-bound at scale); size/latency
  batcher; format dispatch for plain text, NDJSON, and Docker envelopes; drain-style message
  masking; transparent ordered rules for `EventKind` classification.
- **Anomaly detection** (`aegis.detection`) — four deterministic detectors (frequency spike
  via per-signature EWMA z-scores, new error signature with a learning phase, error-ratio
  deviation with volume-weighted baselines, retry storm), all windowed on event time and
  emitting `AnomalyCluster`s that carry the numbers that triggered them.
- **Correlation** (`aegis.correlation`) — five weighted strategies (temporal proximity,
  trace/request linkage, service dependency direction, semantic template similarity, known
  error-propagation cascades) behind a `CorrelationStrategy` protocol; candidate generation
  blocks on trace/service/template keys so the engine never scores O(n²) pairs. Every edge
  keeps its per-strategy score breakdown. This is evidence-weighted plausibility, not causal
  inference — and the docs say so.
- **Causal evidence graph** (`aegis.graph`) — NetworkX DiGraph with SCC condensation (retry
  storms are real cycles), root-candidate ranking (blast radius × earliness × impact),
  strongest-chain extraction (Dijkstra over −log edge scores), and a clock-skew-tolerant
  topological timeline.
- **AI investigation** (`aegis.investigation`) — multi-agent root-cause analysis on top of the
  deterministic evidence, never instead of it:
  - `LLMProvider` protocol with an Anthropic adapter, a deterministic `ScriptedProvider`
    (tests and offline demo never touch a paid API), and resilience decorators (semaphore
    concurrency cap, transient-only retries with backoff and a shared retry budget);
  - a typed tool registry — Pydantic argument models double as the JSON schema shown to the
    model; per-call timeouts, per-agent tool budgets, and a full audit trail of every
    execution; seven built-in tools (`inspect_event_window`, `search_events`,
    `find_similar_events`, `analyze_db_connections`, `calculate_error_rate`,
    `inspect_dependency_graph`, `get_anomaly_details`);
  - a generic `Agent[TInput, TFinding]` ABC owning the guarded tool loop (budget enforcement,
    max turns, JSON validation with one correction attempt);
  - four agents: Log Analyst and Database Investigator run concurrently in a `TaskGroup`,
    the Devil's Advocate attacks their findings, and the tool-less Incident Commander
    synthesizes a validated `RootCauseAssessment`;
  - typed progress events published at every phase for the upcoming WebSocket stream.
- **Synthetic incident** (`aegis.synthetic`) — a seeded, deterministic five-service incident
  (traffic spike → Stripe latency → session leak → pool exhaustion → retry storm → outage)
  in four real log formats, driving the end-to-end test: the pipeline ranks the trigger
  region as the top root candidate and routes the strongest causal chain through the pool
  exhaustion, with no mocks.

## Design rules

- Internal hot-path types are frozen `slots=True` dataclasses; **Pydantic is reserved for trust
  boundaries** (API schemas, LLM output, tool arguments, configuration).
- asyncio owns everything that waits; the process pool owns only what genuinely computes.
- LLM output is never trusted unvalidated; agents talk to providers only through protocols.

## Development

```bash
uv sync                 # install with dev dependencies
uv run pytest           # tests
uv run ruff check .     # lint
uv run mypy             # strict type checking
```

Requires Python 3.13 (uv will fetch it automatically).

## Roadmap (MVP vertical slice)

Ingestion sources → process-pool parsing/normalization → statistical anomaly detection →
correlation strategies → causal evidence graph → concurrent AI investigators with typed tool
calling → validated `RootCauseAssessment` → FastAPI + WebSocket progress stream → PostgreSQL
(+pgvector) persistence and incident memory.
