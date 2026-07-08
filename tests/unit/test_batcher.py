import asyncio

import pytest

from aegis.core.channel import Channel
from aegis.parsing import batched


async def test_flushes_when_batch_size_reached() -> None:
    channel: Channel[int] = Channel(maxsize=16)
    for i in range(7):
        await channel.send(i)
    await channel.close()

    batches = [batch async for batch in batched(channel, max_size=3, max_wait=60.0)]

    assert batches == [[0, 1, 2], [3, 4, 5], [6]]


async def test_flushes_remainder_on_close() -> None:
    channel: Channel[int] = Channel(maxsize=16)
    await channel.send(1)
    await channel.send(2)
    await channel.close()

    batches = [batch async for batch in batched(channel, max_size=100, max_wait=60.0)]

    assert batches == [[1, 2]]


async def test_no_empty_batch_from_empty_channel() -> None:
    channel: Channel[int] = Channel(maxsize=4)
    await channel.close()

    batches = [batch async for batch in batched(channel, max_size=10, max_wait=0.01)]

    assert batches == []


async def test_flushes_partial_batch_when_max_wait_elapses() -> None:
    channel: Channel[int] = Channel(maxsize=16)

    async def trickle() -> None:
        await channel.send(1)
        # Longer than max_wait: the first batch must flush before this arrives.
        await asyncio.sleep(0.15)
        await channel.send(2)
        await channel.close()

    async def consume() -> list[list[int]]:
        return [batch async for batch in batched(channel, max_size=100, max_wait=0.05)]

    async with asyncio.TaskGroup() as tg:
        tg.create_task(trickle())
        consumer = tg.create_task(consume())

    assert consumer.result() == [[1], [2]]


async def test_rejects_invalid_parameters() -> None:
    channel: Channel[int] = Channel(maxsize=4)

    with pytest.raises(ValueError, match="max_size"):
        await anext(batched(channel, max_size=0, max_wait=1.0))
    with pytest.raises(ValueError, match="max_wait"):
        await anext(batched(channel, max_size=1, max_wait=0.0))
