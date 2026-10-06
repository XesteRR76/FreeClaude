import logging
from contextlib import asynccontextmanager
from typing import Any, Dict
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from app.config import settings
from app.key_manager import key_manager
from app.model_fallback import fallback_coordinator
from app.translator import ProtocolTranslator, extract_text_from_content

logger = logging.getLogger("gemini-proxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logging
    print("\n" + "=" * 65)
    print("  🚀 Claude Code -> Google Gemini Emulation Proxy")
    print("=" * 65)
    print(f"  • Listening on: http://{settings.proxy_host}:{settings.proxy_port}")
    print(f"  • Configured Gemini Keys: {key_manager.total_keys}")
    print(f"  • Key Rotation Cooldown: {settings.cooldown_seconds}s")
    print(f"  • Fallback Models Priority:")
    for idx, m in enumerate(settings.gemini_models, 1):
        print(f"      {idx}. {m}")
    print("=" * 65)
    print("  Compatible Claude CLI environment:")
    print(f'    export ANTHROPIC_BASE_URL="http://{settings.proxy_host}:{settings.proxy_port}"')
    print('    export ANTHROPIC_API_KEY="dummy-key"')
    print("=" * 65 + "\n")
    yield


app = FastAPI(
    title="Claude Code to Gemini Proxy",
    description="Anthropic Messages API emulator routing to Google Gemini with key rotation and cascading fallback",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.api_route("/api/hello", methods=["GET", "HEAD"])
@app.api_route("/hello", methods=["GET", "HEAD"])
async def api_hello():
    return {"status": "ok", "message": "Claude-Gemini Proxy alive"}


@app.get("/")
@app.get("/health")
async def health_check():
    """Health check endpoint providing status of active models and keys."""
    return {
        "status": "ok",
        "service": "claude-gemini-proxy",
        "host": settings.proxy_host,
        "port": settings.proxy_port,
        "fallback_models": settings.gemini_models,
        "total_keys": key_manager.total_keys,
        "keys_status": key_manager.get_status(),
    }


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """List available models for Anthropic / OpenAI tooling compatibility."""
    models_data = [
        {
            "id": "claude-3-5-sonnet-20241022",
            "object": "model",
            "created": 1729600000,
            "owned_by": "anthropic",
        },
        {
            "id": "claude-3-7-sonnet-20250219",
            "object": "model",
            "created": 1740000000,
            "owned_by": "anthropic",
        },
        {
            "id": "antigravity",
            "object": "model",
            "created": 1729600000,
            "owned_by": "google",
        },
    ]
    for gm in settings.gemini_models:
        models_data.append(
            {
                "id": gm,
                "object": "model",
                "created": 1729600000,
                "owned_by": "google",
            }
        )
    return {"data": models_data, "object": "list"}


@app.post("/v1/messages/count_tokens")
@app.post("/messages/count_tokens")
async def count_tokens(request: Request):
    """Estimate token count for Claude Code prompts."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    messages = body.get("messages", [])
    system = body.get("system", "")

    total_chars = len(str(system))
    for m in messages:
        c = m.get("content", "")
        total_chars += len(extract_text_from_content(c))

    estimated_tokens = max(1, total_chars // 4)
    return {"input_tokens": estimated_tokens}


@app.post("/v1/messages")
@app.post("/messages")
async def create_message(request: Request):
    """
    Primary Anthropic Messages API emulation endpoint.
    Handles streaming SSE and non-streaming requests from Claude Code CLI.
    """
    try:
        anthropic_request: Dict[str, Any] = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": f"Malformed JSON request body: {e}",
                },
            },
        )

    requested_model = anthropic_request.get("model", "claude-3-5-sonnet-20241022")
    is_stream = bool(anthropic_request.get("stream", False))

    # Translate Anthropic request to Gemini payload
    try:
        gemini_payload, _ = ProtocolTranslator.anthropic_to_gemini(anthropic_request)
    except Exception as e:
        logger.error(f"Error during request translation: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": f"Failed to translate request to Gemini format: {e}",
                },
            },
        )

    if is_stream:
        # Return Server-Sent Events stream
        return StreamingResponse(
            fallback_coordinator.execute_stream(
                gemini_payload, requested_model, raw_anthropic_request=anthropic_request
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Content-Type": "text/event-stream; charset=utf-8",
            },
        )
    else:
        # Return single JSON response
        try:
            result = await fallback_coordinator.execute_non_stream(
                gemini_payload, requested_model, raw_anthropic_request=anthropic_request
            )
            return JSONResponse(content=result)
        except Exception as e:
            logger.error(f"Execution error: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": str(e),
                    },
                },
            )
