import logging


async def test_report_client_error_logs_and_returns_204(client, caplog):
    payload = {
        "message": "TypeError: x is not a function",
        "stack": "TypeError: x is not a function\n  at page.tsx:1:1",
        "url": "https://newsread.example/article/9",
        "digest": "abc123",
        "context": "error-boundary",
    }
    with caplog.at_level(logging.ERROR, logger="app.routers.client_errors"):
        resp = await client.post("/api/client-errors", json=payload)
    assert resp.status_code == 204
    record = caplog.records[-1]
    message = record.getMessage()
    assert "TypeError: x is not a function" in message
    assert "error-boundary" in message
    assert "digest=abc123" in message
    assert "https://newsread.example/article/9" in message


async def test_report_client_error_minimal_body(client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.routers.client_errors"):
        resp = await client.post("/api/client-errors", json={"message": "boom"})
    assert resp.status_code == 204
    message = caplog.records[-1].getMessage()
    assert "boom" in message
    assert "(no stack)" in message
    assert "[unknown]" in message


async def test_report_client_error_requires_no_auth(client):
    resp = await client.post("/api/client-errors", json={"message": "pre-login crash"})
    assert resp.status_code == 204


async def test_report_client_error_rejects_oversized_or_empty(client):
    resp = await client.post("/api/client-errors", json={"message": ""})
    assert resp.status_code == 422
    resp = await client.post("/api/client-errors", json={"message": "x" * 2001})
    assert resp.status_code == 422
