import logging
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
from app.config import settings
from app.key_manager import key_manager, mask_key
from app.translator import extract_text_from_content

logger = logging.getLogger("gemini-proxy")

# Regex patterns matching project assembly, scaffolding, and creation intents
BUILD_PATTERNS = [
    r"соб(ери|рать)\s+проект",
    r"сборк[аеи]\s+проект[ауе]",
    r"созда(й|ть)\s+проект",
    r"сдела(й|ть)\s+проект",
    r"разверн(и|уть)\s+проект",
    r"напиш(и|ить)\s+проект",
    r"соб(ери|рать)\s+приложени[ея]",
    r"сборк[аеи]\s+приложени[ея]",
    r"структур[ауе]\s+проект[ауе]",
    r"архитектур[ауе]\s+проект[ауе]",
    r"каркас\s+проект[ауе]",
    r"скомпилируй\s+проект",
    r"build\s+(the\s+)?project",
    r"assemble\s+(the\s+)?project",
    r"scaffold\s+(the\s+)?project",
    r"create\s+(the\s+)?project",
    r"create\s+(a\s+)?new\s+app",
    r"build\s+(a\s+)?new\s+app",
    r"setup\s+(the\s+)?project",
    r"generate\s+(the\s+)?project",
]

COMPILED_BUILD_REGEX = [re.compile(p, re.IGNORECASE) for p in BUILD_PATTERNS]


def is_project_assembly_intent(anthropic_request: Dict[str, Any]) -> bool:
    """
    Detect if the incoming request is intended for building, scaffolding,
    or assembling a project.
    """
    # 1. Explicit model or header
    requested_model = anthropic_request.get("model", "").lower()
    if "antigravity" in requested_model:
        return True

    # 2. Check system prompt
    system_text = str(anthropic_request.get("system", ""))
    for reg in COMPILED_BUILD_REGEX:
        if reg.search(system_text):
            return True

    # 3. Check messages (focusing on latest user message)
    messages = anthropic_request.get("messages", [])
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content_str = extract_text_from_content(msg.get("content", ""))
            for reg in COMPILED_BUILD_REGEX:
                if reg.search(content_str):
                    return True
            break

    return False


class AntigravityEngine:
    """
    Executes tasks using the Google Antigravity Agent runtime (60 RPM / 100 RPD quota),
    with internal model resilience across available models and strictly read-only capabilities.
    """

    AGENT_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.6-flash",
    ]

    @staticmethod
    async def run_chat_stream(
        prompt: str,
        system_text: str,
        api_key: str,
    ) -> AsyncGenerator[str, None]:
        from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig

        last_error = None
        for m_name in AntigravityEngine.AGENT_MODELS:
            try:
                # Disable all agent tools so Antigravity acts as a pure code generator
                capabilities = CapabilitiesConfig(enabled_tools=[])
                config = LocalAgentConfig(
                    api_key=api_key,
                    model=m_name,
                    capabilities=capabilities,
                    system_instructions=system_text
                    or "You are a senior build architect and autonomous project generator.",
                )
                async with Agent(config) as agent:
                    response = await agent.chat(prompt)
                    async for token in response:
                        yield token
                return
            except Exception as e:
                logger.warning(
                    f"[ANTIGRAVITY INTERNAL RETRY] Model {m_name} failed: {e}. Trying next agent model..."
                )
                last_error = e
                continue

        if last_error:
            raise last_error

    @staticmethod
    async def run_chat_non_stream(
        prompt: str,
        system_text: str,
        api_key: str,
    ) -> str:
        tokens = []
        async for token in AntigravityEngine.run_chat_stream(
            prompt, system_text, api_key
        ):
            tokens.append(token)
        return "".join(tokens)


antigravity_engine = AntigravityEngine()
