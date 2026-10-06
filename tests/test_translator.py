import json
import pytest
from app.translator import ProtocolTranslator, sanitize_gemini_schema, extract_text_from_content


def test_sanitize_gemini_schema():
    raw_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "BashTool",
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to run"},
            "timeout": {"type": "integer"},
            "flags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    sanitized = sanitize_gemini_schema(raw_schema)
    assert "$schema" not in sanitized
    assert "additionalProperties" not in sanitized
    assert sanitized["type"] == "OBJECT"
    assert sanitized["properties"]["command"]["type"] == "STRING"
    assert sanitized["properties"]["timeout"]["type"] == "INTEGER"
    assert sanitized["properties"]["flags"]["type"] == "ARRAY"
    assert sanitized["properties"]["flags"]["items"]["type"] == "STRING"
    assert sanitized["required"] == ["command"]


def test_anthropic_to_gemini_basic():
    request = {
        "model": "claude-3-5-sonnet-20241022",
        "system": "You are a helpful Linux assistant.",
        "messages": [
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there! How can I help you today?"},
            {"role": "user", "content": "Show me files."},
        ],
        "temperature": 0.5,
        "max_tokens": 1024,
    }

    gemini_payload, tool_map = ProtocolTranslator.anthropic_to_gemini(request)

    assert "systemInstruction" in gemini_payload
    assert gemini_payload["systemInstruction"]["parts"][0]["text"] == "You are a helpful Linux assistant."
    assert gemini_payload["generationConfig"]["temperature"] == 0.5
    assert gemini_payload["generationConfig"]["maxOutputTokens"] == 1024

    contents = gemini_payload["contents"]
    assert len(contents) == 3
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0]["text"] == "Hello!"
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["text"] == "Hi there! How can I help you today?"
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["text"] == "Show me files."


def test_anthropic_to_gemini_tools_and_results():
    request = {
        "model": "claude-3-5-sonnet-20241022",
        "tools": [
            {
                "name": "Bash",
                "description": "Execute bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }
        ],
        "messages": [
            {"role": "user", "content": "List files in /tmp"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running ls..."},
                    {
                        "type": "tool_use",
                        "id": "toolu_12345",
                        "name": "Bash",
                        "input": {"command": "ls /tmp"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_12345",
                        "content": "file1.txt\nfile2.txt",
                    }
                ],
            },
        ],
    }

    gemini_payload, tool_map = ProtocolTranslator.anthropic_to_gemini(request)

    assert "tools" in gemini_payload
    funcs = gemini_payload["tools"][0]["functionDeclarations"]
    assert len(funcs) == 1
    assert funcs[0]["name"] == "Bash"

    assert tool_map["toolu_12345"] == "Bash"

    contents = gemini_payload["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["text"] == "Running ls..."
    assert contents[1]["parts"][1]["functionCall"]["name"] == "Bash"
    assert contents[1]["parts"][1]["functionCall"]["args"] == {"command": "ls /tmp"}

    assert contents[2]["role"] == "user"
    func_resp = contents[2]["parts"][0]["functionResponse"]
    assert func_resp["name"] == "Bash"
    assert func_resp["response"]["content"] == "file1.txt\nfile2.txt"


def test_gemini_to_anthropic_non_stream():
    gemini_resp = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "I will check the directory."},
                        {
                            "functionCall": {
                                "name": "Bash",
                                "args": {"command": "pwd"},
                            }
                        },
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 42,
            "candidatesTokenCount": 18,
            "totalTokenCount": 60,
        },
    }

    anthropic_resp = ProtocolTranslator.gemini_to_anthropic_non_stream(
        gemini_resp, requested_model="claude-3-5-sonnet-20241022"
    )

    assert anthropic_resp["role"] == "assistant"
    assert anthropic_resp["stop_reason"] == "tool_use"
    assert anthropic_resp["usage"]["input_tokens"] == 42
    assert anthropic_resp["usage"]["output_tokens"] == 18

    content = anthropic_resp["content"]
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "I will check the directory."
    assert content[1]["type"] == "tool_use"
    assert content[1]["name"] == "Bash"
    assert content[1]["input"] == {"command": "pwd"}


@pytest.mark.asyncio
async def test_gemini_stream_to_anthropic_sse():
    async def mock_stream():
        yield {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello "}],
                    }
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
        }
        yield {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "World!"}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }

    events = []
    async for event_str in ProtocolTranslator.gemini_stream_to_anthropic_sse(
        mock_stream(), requested_model="claude-3-5-sonnet-20241022"
    ):
        events.append(event_str)

    joined = "".join(events)
    assert "event: message_start" in joined
    assert "event: content_block_start" in joined
    assert "event: content_block_delta" in joined
    assert "Hello " in joined
    assert "World!" in joined
    assert "event: content_block_stop" in joined
    assert "event: message_delta" in joined
    assert "event: message_stop" in joined


def test_thought_signature_injection_and_caching():
    from app.thought_signatures import signature_store, DEFAULT_VALID_THOUGHT_SIGNATURE

    signature_store.clear()

    # 1. Non-stream tool call emits thoughtSignature and stores it
    gemini_resp = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "Bash",
                                "args": {"command": "git status"},
                            },
                            "thoughtSignature": "TEST_SIG_ABC_123",
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ]
    }
    anthropic_resp = ProtocolTranslator.gemini_to_anthropic_non_stream(
        gemini_resp, requested_model="claude-3-5-sonnet-20241022"
    )
    tool_use_block = anthropic_resp["content"][0]
    tool_id = tool_use_block["id"]

    # 2. Next turn from Anthropic sends back this tool_use and its result
    anthropic_req = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Check status"},
            {"role": "assistant", "content": [tool_use_block]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": "On branch main",
                    }
                ],
            },
        ],
    }

    gemini_payload, _ = ProtocolTranslator.anthropic_to_gemini(anthropic_req)
    model_turn = gemini_payload["contents"][1]
    fc_part = model_turn["parts"][0]

    assert fc_part["functionCall"]["name"] == "Bash"
    assert fc_part["thoughtSignature"] == "TEST_SIG_ABC_123"

    # 3. Test fallback signature for unknown prior tool call
    anthropic_req_unknown = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "user", "content": "Old command"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_never_seen_before",
                        "name": "CustomTool",
                        "input": {"x": 1},
                    }
                ],
            },
        ],
    }
    gemini_payload_unknown, _ = ProtocolTranslator.anthropic_to_gemini(anthropic_req_unknown)
    fc_unknown = gemini_payload_unknown["contents"][1]["parts"][0]
    assert fc_unknown["thoughtSignature"] == "TEST_SIG_ABC_123"

    # 4. When store is completely empty, it falls back to DEFAULT_VALID_THOUGHT_SIGNATURE
    signature_store.clear()
    gemini_payload_empty, _ = ProtocolTranslator.anthropic_to_gemini(anthropic_req_unknown)
    fc_empty = gemini_payload_empty["contents"][1]["parts"][0]
    assert fc_empty["thoughtSignature"] == DEFAULT_VALID_THOUGHT_SIGNATURE

