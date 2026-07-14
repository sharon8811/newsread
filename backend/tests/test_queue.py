from app import queue


async def test_enqueue_creates_pool_and_enqueues(monkeypatch):
    monkeypatch.setattr(queue, "_pool", None)
    calls = {}

    class FakePool:
        async def enqueue_job(self, name, *args):
            calls["job"] = (name, args)

    async def fake_create_pool(settings):
        calls["created"] = True
        return FakePool()

    monkeypatch.setattr(queue, "create_pool", fake_create_pool)
    await queue.enqueue("enrich_feed", 7)
    assert calls["created"]
    assert calls["job"] == ("enrich_feed", (7,))


async def test_enqueue_reuses_existing_pool(monkeypatch):
    seen = []

    class FakePool:
        async def enqueue_job(self, name, *args):
            seen.append(name)

    monkeypatch.setattr(queue, "_pool", FakePool())

    async def fail_create(settings):
        raise AssertionError("should not create a new pool")

    monkeypatch.setattr(queue, "create_pool", fail_create)
    await queue.enqueue("job", 1)
    assert seen == ["job"]


async def test_enqueue_swallows_errors(monkeypatch):
    monkeypatch.setattr(queue, "_pool", None)

    async def boom(settings):
        raise ConnectionError("redis down")

    monkeypatch.setattr(queue, "create_pool", boom)
    # Must not raise — enqueue failures degrade gracefully.
    await queue.enqueue("job", 1)
