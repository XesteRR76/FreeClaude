import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from app.thought_signatures import signature_store


def sanitize_gemini_schema(schema: Any) -> Any:
    """
    Recursively sanitize JSON schema to be compatible with Gemini OpenAPI parameter schema.
    Converts lowercase types to uppercase (e.g. 'object' -> 'OBJECT') and strictly
    whitelists allowed fields in Gemini's Schema protobuf object, stripping unsupported
    JSON Schema keywords like exclusiveMinimum, const, default, etc.
    """
    if not isinstance(schema, dict):
        return schema

    # Handle anyOf / oneOf / allOf by picking the first non-null branch
    for comb in ("anyOf", "oneOf", "allOf"):
        if comb in schema and isinstance(schema[comb], list) and schema[comb]:
            merged = {k: v for k, v in schema.items() if k not in ("anyOf", "oneOf", "allOf")}
            first_branch = next(
                (s for s in schema[comb] if isinstance(s, dict) and s.get("type") != "null"),
                schema[comb][0],
            )
            if isinstance(first_branch, dict):
                merged.update(first_branch)
            return sanitize_gemini_schema(merged)

    sanitized = {}
    type_map = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }

    # Handle const by converting to enum
    if "const" in schema:
        c_val = schema["const"]
        sanitized["enum"] = [str(c_val)]
        if "type" not in schema:
            sanitized["type"] = "STRING"

    if "type" in schema:
        val = schema["type"]
        if isinstance(val, str):
            sanitized["type"] = type_map.get(val.lower(), val.upper())
        elif isinstance(val, list):
            first = next((t for t in val if t != "null"), "string")
            sanitized["type"] = type_map.get(first.lower(), "STRING")
        else:
            sanitized["type"] = "OBJECT"

    if "properties" in schema and isinstance(schema["properties"], dict):
        sanitized["properties"] = {
            k: sanitize_gemini_schema(v) for k, v in schema["properties"].items()
        }

    if "items" in schema and isinstance(schema["items"], dict):
        sanitized["items"] = sanitize_gemini_schema(schema["items"])

    if "required" in schema and isinstance(schema["required"], list):
        sanitized["required"] = [str(r) for r in schema["required"]]

    if "enum" in schema and isinstance(schema["enum"], list):
        sanitized["enum"] = [str(e) for e in schema["enum"]]

    if "description" in schema and isinstance(schema["description"], str):
        sanitized["description"] = schema["description"]

    if "nullable" in schema and isinstance(schema["nullable"], bool):
        sanitized["nullable"] = schema["nullable"]

    if "format" in schema and isinstance(schema["format"], str):
        sanitized["format"] = schema["format"]

    # Ensure valid fallback type
    if "type" not in sanitized:
        if "properties" in sanitized:
            sanitized["type"] = "OBJECT"
        elif "items" in sanitized:
            sanitized["type"] = "ARRAY"
        else:
            sanitized["type"] = "STRING"

    # Gemini requires items for ARRAY
    if sanitized.get("type") == "ARRAY" and "items" not in sanitized:
        sanitized["items"] = {"type": "STRING"}

    return sanitized


def extract_text_from_content(content: Any) -> str:
    """Extract plain string from Anthropic message content (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    res = block.get("content", "")
                    if isinstance(res, str):
                        texts.append(res)
                    elif isinstance(res, list):
                        texts.append(
                            "\n".join(
                                b.get("text", "")
                                for b in res
                                if isinstance(b, dict) and "text" in b
                            )
                        )
        return "\n".join(texts)
    return str(content)


class ProtocolTranslator:
    """Translates between Anthropic Messages API and Google Gemini API formats."""

    @staticmethod
    def anthropic_to_gemini(anthropic_request: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """
        Translates an Anthropic /v1/messages request to a Gemini API payload.
        Returns (gemini_payload, tool_id_to_name_map).
        """
        gemini_payload: Dict[str, Any] = {}
        tool_id_to_name: Dict[str, str] = {}

        # 1. First pass: discover all tool definitions and prior tool_use IDs in history
        if "tools" in anthropic_request and anthropic_request["tools"]:
            function_declarations = []
            for tool in anthropic_request["tools"]:
                name = tool.get("name", "unnamed_tool")
                desc = tool.get("description", "")
                schema = sanitize_gemini_schema(tool.get("input_schema", {}))
                function_declarations.append(
                    {
                        "name": name,
                        "description": desc,
                        "parameters": schema,
                    }
                )
            gemini_payload["tools"] = [{"functionDeclarations": function_declarations}]

        # 2. Tool choice
        if "tool_choice" in anthropic_request:
            tc = anthropic_request["tool_choice"]
            if isinstance(tc, dict):
                tc_type = tc.get("type", "auto")
                if tc_type == "auto":
                    gemini_payload["toolConfig"] = {
                        "functionCallingConfig": {"mode": "AUTO"}
                    }
                elif tc_type == "any":
                    gemini_payload["toolConfig"] = {
                        "functionCallingConfig": {"mode": "ANY"}
                    }
                elif tc_type == "tool":
                    name = tc.get("name")
                    gemini_payload["toolConfig"] = {
                        "functionCallingConfig": {
                            "mode": "ANY",
                            "allowedFunctionNames": [name] if name else [],
                        }
                    }

        # 3. System Instruction
        system_val = anthropic_request.get("system")
        system_text = ""
        if isinstance(system_val, str) and system_val.strip():
            system_text = system_val.strip()
        elif isinstance(system_val, list):
            sys_parts = []
            for sb in system_val:
                if isinstance(sb, dict) and sb.get("type") == "text":
                    sys_parts.append(sb.get("text", ""))
                elif isinstance(sb, str):
                    sys_parts.append(sb)
            system_text = "\n\n".join(sys_parts).strip()

        if system_text:
            gemini_payload["systemInstruction"] = {
                "parts": [{"text": system_text}]
            }

        # 4. Generation Config
        gen_config: Dict[str, Any] = {}
        if "max_tokens" in anthropic_request:
            gen_config["maxOutputTokens"] = anthropic_request["max_tokens"]
        if "temperature" in anthropic_request:
            gen_config["temperature"] = anthropic_request["temperature"]
        if "top_p" in anthropic_request:
            gen_config["topP"] = anthropic_request["top_p"]
        if "top_k" in anthropic_request:
            gen_config["topK"] = anthropic_request["top_k"]
        if "stop_sequences" in anthropic_request and anthropic_request["stop_sequences"]:
            gen_config["stopSequences"] = anthropic_request["stop_sequences"]

        if gen_config:
            gemini_payload["generationConfig"] = gen_config

        # 5. Pre-scan messages to map tool_use id -> tool_name
        for msg in anthropic_request.get("messages", []):
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_id = block.get("id")
                        tool_name = block.get("name")
                        if tool_id and tool_name:
                            tool_id_to_name[tool_id] = tool_name

        # 6. Messages / Contents translation
        raw_contents: List[Dict[str, Any]] = []
        for msg in anthropic_request.get("messages", []):
            role = msg.get("role", "user")
            # Anthropic 'user' -> Gemini 'user'; Anthropic 'assistant' -> Gemini 'model'
            gemini_role = "model" if role == "assistant" else "user"

            parts: List[Dict[str, Any]] = []
            content = msg.get("content", "")

            if isinstance(content, str):
                if content:
                    parts.append({"text": content})
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        text_val = block.get("text", "")
                        if text_val:
                            parts.append({"text": text_val})
                    elif btype == "image":
                        source = block.get("source", {})
                        if source.get("type") == "base64":
                            parts.append(
                                {
                                    "inlineData": {
                                        "mimeType": source.get("media_type", "image/png"),
                                        "data": source.get("data", ""),
                                    }
                                }
                            )
                    elif btype == "tool_use":
                        t_id = block.get("id", f"toolu_{uuid.uuid4().hex[:12]}")
                        t_name = block.get("name", "tool")
                        t_input = block.get("input", {})
                        if not isinstance(t_input, dict):
                            t_input = {}
                        tool_id_to_name[t_id] = t_name
                        sig = signature_store.get_signature(
                            tool_id=t_id, tool_name=t_name, args=t_input
                        )
                        parts.append(
                            {
                                "functionCall": {
                                    "name": t_name,
                                    "args": t_input,
                                },
                                "thoughtSignature": sig,
                            }
                        )
                    elif btype == "tool_result":
                        t_id = block.get("tool_use_id", "")
                        t_name = (
                            tool_id_to_name.get(t_id)
                            or signature_store.get_tool_name(t_id)
                            or "unknown_tool"
                        )
                        res_content = block.get("content", "")
                        # Flatten content if list of blocks
                        if isinstance(res_content, list):
                            res_text = "\n".join(
                                b.get("text", "")
                                for b in res_content
                                if isinstance(b, dict) and "text" in b
                            )
                        elif isinstance(res_content, (dict, list)):
                            res_text = res_content
                        else:
                            res_text = str(res_content)

                        parts.append(
                            {
                                "functionResponse": {
                                    "name": t_name,
                                    "response": {
                                        "name": t_name,
                                        "content": res_text,
                                        "is_error": block.get("is_error", False),
                                    },
                                }
                            }
                        )

            if parts:
                raw_contents.append({"role": gemini_role, "parts": parts})

        # 7. Merge consecutive turns of identical roles (Gemini requires alternating user/model)
        merged_contents: List[Dict[str, Any]] = []
        for item in raw_contents:
            if merged_contents and merged_contents[-1]["role"] == item["role"]:
                merged_contents[-1]["parts"].extend(item["parts"])
            else:
                merged_contents.append(item)

        # 8. Gemini requires the conversation to start with 'user'
        if merged_contents and merged_contents[0]["role"] != "user":
            merged_contents.insert(0, {"role": "user", "parts": [{"text": "Hello"}]})

        gemini_payload["contents"] = merged_contents
        return gemini_payload, tool_id_to_name

    @staticmethod
    def gemini_to_anthropic_non_stream(
        gemini_resp: Dict[str, Any], requested_model: str, msg_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Translates a complete Gemini generateContent JSON response to an Anthropic Message response.
        """
        message_id = msg_id or f"msg_{uuid.uuid4().hex[:24]}"
        content_blocks: List[Dict[str, Any]] = []
        stop_reason = "end_turn"

        candidates = gemini_resp.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            finish_reason = candidate.get("finishReason", "STOP")
            if finish_reason == "MAX_TOKENS":
                stop_reason = "max_tokens"
            elif finish_reason in ("SAFETY", "RECITATION"):
                stop_reason = "stop_sequence"

            content_obj = candidate.get("content", {})
            parts = content_obj.get("parts", [])

            for part in parts:
                if "text" in part:
                    content_blocks.append({"type": "text", "text": part["text"]})
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    stop_reason = "tool_use"
                    tool_call_id = f"toolu_{uuid.uuid4().hex[:24]}"
                    tool_name = fc.get("name", "tool")
                    tool_args = fc.get("args", {}) or {}
                    sig = part.get("thoughtSignature") or part.get("thought_signature")
                    if sig:
                        signature_store.store_signature(
                            tool_id=tool_call_id,
                            tool_name=tool_name,
                            args=tool_args,
                            signature=sig,
                        )
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_call_id,
                            "name": tool_name,
                            "input": tool_args,
                        }
                    )

        usage_meta = gemini_resp.get("usageMetadata", {})
        usage = {
            "input_tokens": usage_meta.get("promptTokenCount", 0),
            "output_tokens": usage_meta.get("candidatesTokenCount", 0),
        }

        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": requested_model,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage,
        }

    @staticmethod
    async def gemini_stream_to_anthropic_sse(
        gemini_stream: AsyncGenerator[Dict[str, Any], None],
        requested_model: str,
        msg_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Translates streaming Gemini SSE chunks into Anthropic SSE events.
        """
        message_id = msg_id or f"msg_{uuid.uuid4().hex[:24]}"
        current_block_index = 0
        in_text_block = False
        stop_reason = "end_turn"
        total_input_tokens = 0
        total_output_tokens = 0

        # Send initial message_start event
        initial_msg = {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": requested_model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        yield f"event: message_start\ndata: {json.dumps(initial_msg)}\n\n"

        async for chunk in gemini_stream:
            usage_meta = chunk.get("usageMetadata", {})
            if usage_meta:
                total_input_tokens = usage_meta.get("promptTokenCount", total_input_tokens)
                total_output_tokens = usage_meta.get("candidatesTokenCount", total_output_tokens)

            candidates = chunk.get("candidates", [])
            if not candidates:
                continue

            candidate = candidates[0]
            finish_reason = candidate.get("finishReason")
            if finish_reason == "MAX_TOKENS":
                stop_reason = "max_tokens"
            elif finish_reason in ("SAFETY", "RECITATION"):
                stop_reason = "stop_sequence"

            parts = candidate.get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    text_delta = part["text"]
                    if not in_text_block:
                        start_event = {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {"type": "text", "text": ""},
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(start_event)}\n\n"
                        in_text_block = True

                    delta_event = {
                        "type": "content_block_delta",
                        "index": current_block_index,
                        "delta": {"type": "text_delta", "text": text_delta},
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"

                elif "functionCall" in part:
                    # If we were in a text block, close it first
                    if in_text_block:
                        stop_event = {
                            "type": "content_block_stop",
                            "index": current_block_index,
                        }
                        yield f"event: content_block_stop\ndata: {json.dumps(stop_event)}\n\n"
                        in_text_block = False
                        current_block_index += 1

                    fc = part["functionCall"]
                    tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
                    tool_name = fc.get("name", "tool")
                    tool_args = fc.get("args", {}) or {}
                    stop_reason = "tool_use"

                    sig = part.get("thoughtSignature") or part.get("thought_signature")
                    if sig:
                        signature_store.store_signature(
                            tool_id=tool_id,
                            tool_name=tool_name,
                            args=tool_args,
                            signature=sig,
                        )

                    # Start tool_use content block
                    start_event = {
                        "type": "content_block_start",
                        "index": current_block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": tool_name,
                            "input": {},
                        },
                    }
                    yield f"event: content_block_start\ndata: {json.dumps(start_event)}\n\n"

                    # Emit arguments as json delta
                    delta_event = {
                        "type": "content_block_delta",
                        "index": current_block_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(tool_args),
                        },
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"

                    # Stop tool_use block
                    stop_event = {
                        "type": "content_block_stop",
                        "index": current_block_index,
                    }
                    yield f"event: content_block_stop\ndata: {json.dumps(stop_event)}\n\n"
                    current_block_index += 1

        # Close any open text block
        if in_text_block:
            stop_event = {
                "type": "content_block_stop",
                "index": current_block_index,
            }
            yield f"event: content_block_stop\ndata: {json.dumps(stop_event)}\n\n"

        # Emit message_delta
        delta_event = {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": total_output_tokens},
        }
        yield f"event: message_delta\ndata: {json.dumps(delta_event)}\n\n"

        # Emit message_stop
        yield "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
