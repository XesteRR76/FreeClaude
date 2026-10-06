import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock
from app.main import app
from app.gemini_client import GeminiRateLimitError, GeminiModelNotFoundError


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "fallback_models" in data
        assert "total_keys" in data


@pytest.mark.asyncio
async def test_count_tokens_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        body = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [
                {"role": "user", "content": "What is the status of systemd service?"}
            ],
        }
        resp = await ac.post("/v1/messages/count_tokens", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "input_tokens" in data
        assert data["input_tokens"] > 0


@pytest.mark.asyncio
async def test_cascading_model_fallback_and_key_rotation():
    """
    Test scenario:
    Key 1 hits 429 on Model 1.
    Key 2 hits 429 on Model 1.
    All keys exhausted on Model 1 -> Cascades to Model 2.
    Model 2 succeeds on Key 1 (or next available).
    """
    from app.key_manager import key_manager
    from app.model_fallback import fallback_coordinator

    # Configure 2 test keys
    test_keys = ["test-key-1", "test-key-2"]
    key_manager.update_keys(test_keys)
    fallback_coordinator.reload_models(["model-primary", "model-fallback"])

    call_history = []

    async def mock_generate(model, api_key, payload):
        call_history.append((model, api_key))
        if model == "model-primary":
            # Rate limit on both keys for primary model
            raise GeminiRateLimitError(429, "Quota exceeded")
        elif model == "model-fallback":
            return {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "Hello from fallback model!"}],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 6},
            }

    with patch("app.model_fallback.gemini_client.generate_content", side_effect=mock_generate):
        # Reset cooldowns
        for k in test_keys:
            key_manager.reset_cooldown(k)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            req_body = {
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "Test prompt"}],
            }
            resp = await ac.post("/v1/messages", json=req_body)
            assert resp.status_code == 200
            data = resp.json()
            assert data["role"] == "assistant"
            assert data["content"][0]["text"] == "Hello from fallback model!"

            # Verify that fallback occurred:
            # First tried model-primary with both keys, then cascaded to model-fallback
            assert any(m == "model-primary" for m, k in call_history)
            assert any(m == "model-fallback" for m, k in call_history)
