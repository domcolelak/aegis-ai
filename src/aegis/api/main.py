"""Uvicorn entrypoint: ``uvicorn aegis.api.main:app``."""

from aegis.api.app import create_app

app = create_app()
