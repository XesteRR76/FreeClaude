import asyncio
import json
import logging
import sys
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple
import httpx
from app.config import settings
from app.key_manager import key_manager, mask_key
from app.gemini_client import (
    gemini_client,
    GeminiRateLimitError,
    GeminiModelNotFoundError,
    GeminiAuthError,
    GeminiServerError,
    GeminiAPIError,
)
from app.translator import ProtocolTranslator, extract_text_from_content
from app.antigravity_engine import antigravity_engine, is_project_assembly_intent

# Setup formatted terminal logger
logger = logging.getLogger("gemini-proxy")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "\033[36m[%(asctime)s]\033[0m %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class TerminalColors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RED = "\033[31m"


class ModelFallbackCoordinator:
    """
    Coordinates intelligent API key rotation, cooldown tracking,
    and cascading model fallback across the priority list,
    activating the Antigravity Agent Engine for project build tasks.
    """

    def __init__(self):
        self.models: List[str] = list(settings.gemini_models)
        self._model_cooldowns: Dict[str, float] = {}  # model -> expire_at_timestamp

    def reload_models(self, models: Optional[List[str]] = None):
        if models:
            self.models = list(models)
        else:
            self.models = list(settings.gemini_models)

    def is_model_cooling_down(self, model: str) -> bool:
        expire_at = self._model_cooldowns.get(model, 0.0)
        return time.time() < expire_at

    def mark_model_cooldown(self, model: str, duration: Optional[float] = None):
        dur = duration if duration is not None else settings.model_overload_cooldown_seconds
        self._model_cooldowns[model] = time.time() + dur

    async def execute_non_stream(
        self,
        gemini_payload: Dict[str, Any],
        requested_model: str,
        raw_anthropic_request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes a non-streaming request with automatic key rotation, model fallback,
        and Antigravity prioritization for project build tasks.
        """
        if not key_manager.total_keys:
            raise GeminiAPIError(
                500,
                "No Gemini API keys configured. Please set GEMINI_API_KEYS in your .env file.",
            )

        is_build = raw_anthropic_request and is_project_assembly_intent(raw_anthropic_request)
        if is_build:
            logger.info(
                f"{TerminalColors.CYAN}{TerminalColors.BOLD}[PROJECT BUILD DETECTED]{TerminalColors.RESET} "
                f"Task involves project assembly/building ('собрать проект')! "
                f"Prioritizing Antigravity Agent Engine (60 RPM / 100 RPD quota)..."
            )
            models_to_try = ["antigravity"] + [m for m in self.models if m != "antigravity"]
        else:
            models_to_try = list(self.models)

        # Safeguard: if all models are cooling down, clear cooldowns so requests never fail with 429
        if all(self.is_model_cooling_down(m) for m in models_to_try):
            self._model_cooldowns.clear()

        last_error: Optional[Exception] = None

        for model_idx, model in enumerate(models_to_try):
            # Check if this model is temporarily overloaded
            if self.is_model_cooling_down(model):
                rem = round(self._model_cooldowns.get(model, 0.0) - time.time(), 1)
                logger.info(
                    f"{TerminalColors.YELLOW}[MODEL COOLDOWN]{TerminalColors.RESET} "
                    f"Model {model} is on high-demand cooldown ({rem}s left). Skipping to next model..."
                )
                continue

            tried_keys: Set[str] = set()

            while True:
                key = await key_manager.get_available_key(model=model, excluded_keys=tried_keys)
                if not key:
                    logger.warning(
                        f"{TerminalColors.MAGENTA}[FALLBACK]{TerminalColors.RESET} "
                        f"All keys exhausted/in cooldown for model {TerminalColors.BOLD}{model}{TerminalColors.RESET}. "
                        f"Cascading to next fallback model in priority list..."
                    )
                    break

                tried_keys.add(key)
                m_key = mask_key(key)

                # Special handler for Antigravity Agent Engine
                if model == "antigravity":
                    logger.info(
                        f"{TerminalColors.CYAN}[ANTIGRAVITY DISPATCH]{TerminalColors.RESET} "
                        f"Key: {TerminalColors.BOLD}{m_key}{TerminalColors.RESET} | "
                        f"Engine: Antigravity Agent (Agents quota)"
                    )
                    try:
                        messages = raw_anthropic_request.get("messages", []) if raw_anthropic_request else []
                        prompt = extract_text_from_content(messages[-1].get("content", "")) if messages else "Собери проект"
                        sys_txt = str(raw_anthropic_request.get("system", "")) if raw_anthropic_request else ""

                        text_resp = await antigravity_engine.run_chat_non_stream(
                            prompt=prompt, system_text=sys_txt, api_key=key
                        )
                        logger.info(
                            f"{TerminalColors.GREEN}[ANTIGRAVITY 200 OK]{TerminalColors.RESET} "
                            f"Key: {m_key} | Antigravity Agent successfully generated project response!"
                        )
                        return {
                            "id": f"msg_{uuid.uuid4().hex[:24]}",
                            "type": "message",
                            "role": "assistant",
                            "model": requested_model,
                            "content": [{"type": "text", "text": text_resp}],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "usage": {
                                "input_tokens": len(prompt) // 4,
                                "output_tokens": len(text_resp) // 4,
                            },
                        }
                    except Exception as e:
                        logger.warning(
                            f"{TerminalColors.YELLOW}[ANTIGRAVITY FAILOVER]{TerminalColors.RESET} "
                            f"Antigravity run failed ({e}). Cascading to Flash cascade..."
                        )
                        last_error = e
                        break

                logger.info(
                    f"{TerminalColors.CYAN}[DISPATCH]{TerminalColors.RESET} "
                    f"Key: {TerminalColors.BOLD}{m_key}{TerminalColors.RESET} | "
                    f"Model: {TerminalColors.BOLD}{model}{TerminalColors.RESET} | Mode: Non-streaming"
                )

                try:
                    resp_json = await gemini_client.generate_content(
                        model=model, api_key=key, payload=gemini_payload
                    )

                    usage = resp_json.get("usageMetadata", {})
                    in_tok = usage.get("promptTokenCount", 0)
                    out_tok = usage.get("candidatesTokenCount", 0)
                    logger.info(
                        f"{TerminalColors.GREEN}[SUCCESS 200]{TerminalColors.RESET} "
                        f"Key: {m_key} | Model: {model} | Tokens: (in={in_tok}, out={out_tok})"
                    )

                    # Translate to Anthropic format
                    return ProtocolTranslator.gemini_to_anthropic_non_stream(
                        resp_json, requested_model
                    )

                except GeminiRateLimitError as e:
                    key_manager.mark_cooldown(key, model=model, duration=settings.cooldown_seconds)
                    logger.warning(
                        f"{TerminalColors.YELLOW}[KEY 429 RATE LIMIT]{TerminalColors.RESET} "
                        f"Key {m_key} hit rate/quota limit on model {model}. Marked cooldown {int(settings.cooldown_seconds)}s. "
                        f"Seamlessly rotating to next key..."
                    )
                    last_error = e
                    continue

                except GeminiModelNotFoundError as e:
                    logger.warning(
                        f"{TerminalColors.YELLOW}[MODEL 404 NOT FOUND]{TerminalColors.RESET} "
                        f"Model {model} returned 404 Not Found. Skipping model in cascade..."
                    )
                    last_error = e
                    break

                except GeminiAuthError as e:
                    logger.error(
                        f"{TerminalColors.RED}[AUTH ERROR]{TerminalColors.RESET} "
                        f"Key {m_key} is invalid: {e.message}. Marking global cooldown 300s."
                    )
                    key_manager.mark_cooldown(key, model="", duration=300.0)
                    last_error = e
                    continue

                except GeminiServerError as e:
                    logger.warning(
                        f"{TerminalColors.YELLOW}[MODEL 503 OVERLOAD]{TerminalColors.RESET} "
                        f"Model {model} returned 503 ({e.message}). "
                        f"Marking model cooldown {int(settings.model_overload_cooldown_seconds)}s and cascading to next model..."
                    )
                    self.mark_model_cooldown(model)
                    last_error = e
                    break

                except Exception as e:
                    logger.error(
                        f"{TerminalColors.RED}[ERROR]{TerminalColors.RESET} "
                        f"Key {m_key} | Model {model} -> {e}. Trying next key..."
                    )
                    last_error = e
                    continue

        # If all models and all keys exhausted
        error_msg = (
            f"All Gemini models and keys exhausted. Last error: {last_error}"
            if last_error
            else "No available Gemini API key or model found."
        )
        raise GeminiRateLimitError(429, error_msg)

    async def execute_stream(
        self,
        gemini_payload: Dict[str, Any],
        requested_model: str,
        raw_anthropic_request: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Executes a streaming request with key rotation, model fallback,
        and Antigravity prioritization for project build tasks.
        """
        if not key_manager.total_keys:
            error_data = {
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "No Gemini API keys configured. Set GEMINI_API_KEYS in .env.",
                },
            }
            yield f"event: error\ndata: {error_data}\n\n"
            return

        is_build = raw_anthropic_request and is_project_assembly_intent(raw_anthropic_request)
        if is_build:
            logger.info(
                f"{TerminalColors.CYAN}{TerminalColors.BOLD}[PROJECT BUILD DETECTED]{TerminalColors.RESET} "
                f"Task involves project assembly/building ('собрать проект')! "
                f"Prioritizing Antigravity Agent Engine (60 RPM / 100 RPD quota)..."
            )
            models_to_try = ["antigravity"] + [m for m in self.models if m != "antigravity"]
        else:
            models_to_try = list(self.models)

        # Safeguard: if all models are cooling down, clear cooldowns so requests never fail with 429
        if all(self.is_model_cooling_down(m) for m in models_to_try):
            self._model_cooldowns.clear()

        last_error: Optional[Exception] = None

        for model in models_to_try:
            if self.is_model_cooling_down(model):
                rem = round(self._model_cooldowns.get(model, 0.0) - time.time(), 1)
                logger.info(
                    f"{TerminalColors.YELLOW}[MODEL COOLDOWN]{TerminalColors.RESET} "
                    f"Model {model} is on high-demand cooldown ({rem}s left). Skipping to next model..."
                )
                continue

            tried_keys: Set[str] = set()

            while True:
                key = await key_manager.get_available_key(model=model, excluded_keys=tried_keys)
                if not key:
                    logger.warning(
                        f"{TerminalColors.MAGENTA}[FALLBACK]{TerminalColors.RESET} "
                        f"All keys exhausted/in cooldown for model {TerminalColors.BOLD}{model}{TerminalColors.RESET}. "
                        f"Cascading to next fallback model in priority list..."
                    )
                    break

                tried_keys.add(key)
                m_key = mask_key(key)

                # Special handler for Antigravity Agent Engine in streaming mode
                if model == "antigravity":
                    logger.info(
                        f"{TerminalColors.CYAN}[ANTIGRAVITY STREAM DISPATCH]{TerminalColors.RESET} "
                        f"Key: {TerminalColors.BOLD}{m_key}{TerminalColors.RESET} | "
                        f"Engine: Antigravity Agent (Agents quota)"
                    )
                    try:
                        messages = raw_anthropic_request.get("messages", []) if raw_anthropic_request else []
                        prompt = extract_text_from_content(messages[-1].get("content", "")) if messages else "Собери проект"
                        sys_txt = str(raw_anthropic_request.get("system", "")) if raw_anthropic_request else ""

                        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
                        start_event = {
                            "type": "message_start",
                            "message": {
                                "id": msg_id,
                                "type": "message",
                                "role": "assistant",
                                "model": requested_model,
                                "content": [],
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                            },
                        }
                        yield f"event: message_start\ndata: {json.dumps(start_event)}\n\n"
                        yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"

                        total_out = 0
                        async for token in antigravity_engine.run_chat_stream(
                            prompt=prompt, system_text=sys_txt, api_key=key
                        ):
                            total_out += 1
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': token}})}\n\n"

                        yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                        yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': total_out}})}\n\n"
                        yield "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
                        return
                    except Exception as e:
                        logger.warning(
                            f"{TerminalColors.YELLOW}[ANTIGRAVITY FAILOVER]{TerminalColors.RESET} "
                            f"Antigravity streaming failed ({e}). Cascading to Flash cascade..."
                        )
                        last_error = e
                        break

                logger.info(
                    f"{TerminalColors.CYAN}[DISPATCH]{TerminalColors.RESET} "
                    f"Key: {TerminalColors.BOLD}{m_key}{TerminalColors.RESET} | "
                    f"Model: {TerminalColors.BOLD}{model}{TerminalColors.RESET} | Mode: Streaming SSE"
                )

                try:
                    # Attempt connecting to stream
                    chunk_gen = gemini_client.stream_generate_content(
                        model=model, api_key=key, payload=gemini_payload
                    )

                    # Prime generator to verify connection succeeds (200 OK)
                    first_chunk = None
                    try:
                        first_chunk = await asyncio.wait_for(chunk_gen.__anext__(), timeout=15.0)
                    except StopAsyncIteration:
                        first_chunk = None

                    logger.info(
                        f"{TerminalColors.GREEN}[STREAM CONNECTED 200]{TerminalColors.RESET} "
                        f"Key: {m_key} | Model: {model} | Streaming response to Claude Code..."
                    )

                    # Reconstruct async generator yielding first_chunk then remainder
                    async def combined_stream():
                        if first_chunk is not None:
                            yield first_chunk
                        async for c in chunk_gen:
                            yield c

                    # Pipe into Anthropic SSE translator
                    async for sse_event in ProtocolTranslator.gemini_stream_to_anthropic_sse(
                        combined_stream(), requested_model
                    ):
                        yield sse_event

                    return

                except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                    logger.warning(
                        f"{TerminalColors.YELLOW}[STREAM INIT TIMEOUT]{TerminalColors.RESET} "
                        f"Model {model} timed out during stream init (>15s). "
                        f"Marking model cooldown 20s and cascading to next model..."
                    )
                    self.mark_model_cooldown(model, duration=20.0)
                    last_error = e
                    break

                except GeminiRateLimitError as e:
                    key_manager.mark_cooldown(key, model=model, duration=settings.cooldown_seconds)
                    logger.warning(
                        f"{TerminalColors.YELLOW}[KEY 429 RATE LIMIT]{TerminalColors.RESET} "
                        f"Key {m_key} hit rate limit on model {model}. Marked cooldown {int(settings.cooldown_seconds)}s. "
                        f"Rotating to next key..."
                    )
                    last_error = e
                    continue

                except GeminiModelNotFoundError as e:
                    logger.warning(
                        f"{TerminalColors.YELLOW}[MODEL 404 NOT FOUND]{TerminalColors.RESET} "
                        f"Model {model} returned 404. Cascading to next model in list..."
                    )
                    last_error = e
                    break

                except GeminiAuthError as e:
                    logger.error(
                        f"{TerminalColors.RED}[AUTH ERROR]{TerminalColors.RESET} "
                        f"Key {m_key} invalid: {e.message}. Marking global cooldown 300s."
                    )
                    key_manager.mark_cooldown(key, model="", duration=300.0)
                    last_error = e
                    continue

                except GeminiServerError as e:
                    logger.warning(
                        f"{TerminalColors.YELLOW}[MODEL 503 OVERLOAD]{TerminalColors.RESET} "
                        f"Stream init for model {model} returned 503 ({e.message}). "
                        f"Marking model cooldown {int(settings.model_overload_cooldown_seconds)}s and cascading to next model..."
                    )
                    self.mark_model_cooldown(model)
                    last_error = e
                    break

                except Exception as e:
                    logger.error(
                        f"{TerminalColors.RED}[ERROR]{TerminalColors.RESET} "
                        f"Stream init failed with Key {m_key} | Model {model} -> {e}. Retrying next key..."
                    )
                    last_error = e
                    continue

        # If everything failed
        err_event = {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": f"All Gemini fallback models and keys exhausted. Last error: {last_error}",
            },
        }
        yield f"event: error\ndata: {err_event}\n\n"


fallback_coordinator = ModelFallbackCoordinator()
