"""Log ingestion: sources produce RawLogEvents into a bounded channel.

Sources own record framing and provenance only; interpretation of payloads
belongs to the parsing stage. New transports (syslog, Kafka, HTTP streaming,
a live Docker daemon client) plug in by implementing the LogSource protocol.
"""

from aegis.ingestion.source import LogSource
from aegis.ingestion.sources import DockerReplaySource, FileLogSource, StructuredJsonLogSource
from aegis.ingestion.supervisor import IngestionSupervisor

__all__ = [
    "DockerReplaySource",
    "FileLogSource",
    "IngestionSupervisor",
    "LogSource",
    "StructuredJsonLogSource",
]
