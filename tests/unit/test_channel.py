import asyncio

import pytest

from aegis.core.channel import Channel
from aegis.core.errors import ChannelClosedError


async def test_items_flow_in_order() -> None:
    channel: Channel[int] = Channel(maxsize=8)
    for i in range(5):
        await channel.send(i)
    await channel.close()

    assert [item async for item in channel] == [0, 1, 2, 3, 4]


async def test_full_channel_suspends_producer() -> None:
    channel: Channel[int] = Channel(maxsize=1)
    await channel.send(0)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(channel.send(1), timeout=0.05)

    # Draining one item frees a slot and the producer can proceed again.
    assert await channel.receive() == 0
    await asyncio.wait_for(channel.send(1), timeout=1.0)


async def test_send_after_close_raises() -> None:
    channel: Channel[int] = Channel(maxsize=1)
    await channel.close()

    with pytest.raises(ChannelClosedError):
        await channel.send(1)


async def test_sender_suspended_on_full_channel_fails_after_close() -> None:
    channel: Channel[int] = Channel(maxsize=1)
    await channel.send(0)
    send_task = asyncio.create_task(channel.send(1))
    await asyncio.sleep(0)  # let the send suspend on the full channel
    await channel.close()

    assert await channel.receive() == 0
    with pytest.raises(ChannelClosedError):
        await send_task


async def test_receive_after_drain_raises_repeatedly() -> None:
    channel: Channel[int] = Channel(maxsize=2)
    await channel.send(1)
    await channel.close()

    assert await channel.receive() == 1
    # The close marker is re-enqueued so every consumer observes the close.
    for _ in range(2):
        with pytest.raises(ChannelClosedError):
            await channel.receive()


async def test_context_manager_closes_even_when_full() -> None:
    async with Channel[int](maxsize=1) as channel:
        await channel.send(1)

    assert channel.closed
    assert await channel.receive() == 1


async def test_close_is_idempotent() -> None:
    channel: Channel[int] = Channel(maxsize=1)
    await channel.close()
    await channel.close()

    assert channel.closed


def test_rejects_non_positive_maxsize() -> None:
    with pytest.raises(ValueError, match="maxsize"):
        Channel[int](maxsize=0)
