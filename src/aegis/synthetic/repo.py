"""The synthetic incident's companion source repository.

Contains the exact defect the incident's logs point to: ``create_booking``
opens a database session and never closes it when the external payment call
raises. The Code Investigator is expected to find it and the Patch Engineer
to propose the context-manager fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

BOOKING_SERVICE = '''"""Booking service for the synthetic demo application."""

from app.db import SessionLocal
from app.payments import stripe_client


async def create_booking(request):
    session = SessionLocal()
    booking = await _insert_booking(session, request)
    # BUG: when this call raises (e.g. TimeoutError under Stripe latency),
    # the function unwinds without ever closing the session -- each timeout
    # permanently consumes one connection from the pool.
    await stripe_client.create_payment(booking.total, booking.reference)
    await session.commit()
    await session.close()
    return booking


async def _insert_booking(session, request):
    booking = request.to_model()
    session.add(booking)
    await session.flush()
    return booking
'''

DB_MODULE = '''"""Database session factory for the synthetic demo application."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

engine = create_async_engine("postgresql+asyncpg://app:app@postgres/app", pool_size=100)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
'''

PAYMENTS_MODULE = '''"""Stripe client wrapper for the synthetic demo application."""

import httpx


class StripeClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)

    async def create_payment(self, amount, reference):
        response = await self._http.post(
            "https://api.stripe.com/v1/charges",
            data={"amount": amount, "reference": reference},
        )
        response.raise_for_status()
        return response.json()


stripe_client = StripeClient()
'''


def materialize_repo(directory: Path) -> Path:
    """Write the buggy demo service into ``directory`` and return its root."""
    root = directory / "booking-service"
    services = root / "app" / "services"
    services.mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (services / "__init__.py").write_text("", encoding="utf-8")
    (services / "booking_service.py").write_text(BOOKING_SERVICE, encoding="utf-8")
    (root / "app" / "db.py").write_text(DB_MODULE, encoding="utf-8")
    (root / "app" / "payments.py").write_text(PAYMENTS_MODULE, encoding="utf-8")
    return root
