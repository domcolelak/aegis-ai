from uuid import UUID, uuid4

from aegis.api.bus import InProcessEventBus, TopicPublisher
from aegis.investigation.progress import ProgressEvent, ProgressKind


def make_event(investigation_id: UUID, message: str, progress: float = 0.5) -> ProgressEvent:
    return ProgressEvent(
        investigation_id=investigation_id,
        kind=ProgressKind.AGENT_COMPLETED,
        message=message,
        progress=progress,
    )


async def test_publish_reaches_every_subscriber_of_the_topic() -> None:
    bus = InProcessEventBus()
    topic = uuid4()
    other_topic = uuid4()

    async with (
        bus.subscribe(topic) as first,
        bus.subscribe(topic) as second,
        bus.subscribe(other_topic) as other,
    ):
        await bus.publish(topic, make_event(topic, "hello"))

        assert (await first.get()).message == "hello"
        assert (await second.get()).message == "hello"
        assert other.empty(), "unrelated topics must not receive the event"


async def test_slow_consumer_loses_oldest_events_not_newest() -> None:
    bus = InProcessEventBus(queue_size=2)
    topic = uuid4()

    async with bus.subscribe(topic) as queue:
        for i in range(4):
            await bus.publish(topic, make_event(topic, f"event-{i}"))

        received = [queue.get_nowait().message, queue.get_nowait().message]

    assert received == ["event-2", "event-3"]


async def test_unsubscribe_cleans_up_empty_topics() -> None:
    bus = InProcessEventBus()
    topic = uuid4()

    async with bus.subscribe(topic):
        pass

    # Publishing to a topic with no subscribers is a no-op, not an error.
    await bus.publish(topic, make_event(topic, "into the void"))


async def test_topic_publisher_satisfies_progress_publisher() -> None:
    bus = InProcessEventBus()
    topic = uuid4()
    publisher = TopicPublisher(bus, topic)

    async with bus.subscribe(topic) as queue:
        await publisher.publish(make_event(topic, "adapted"))

        assert (await queue.get()).message == "adapted"
