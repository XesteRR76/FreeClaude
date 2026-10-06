import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("gemini-proxy")

# Known valid cryptographic signature token verified against Gemini 3.x / 2.5 API
DEFAULT_VALID_THOUGHT_SIGNATURE = (
    "EmAKXgFpFH0TFD/mzY0podGL54ymwfTkBJrsWJRr3fwrHxovzQ0of25O4G6tckEJrQp8ukiwMELUx6e61WYPjWk+TwLxRj5y2TV+zrCgiQHgNONIllv6wLrfPoryXduOs3k="
)


class ThoughtSignatureStore:
    """
    Persists and matches Google Gemini thought signatures for function calls.
    Gemini 3.x / 2.5 models require thoughtSignature in functionCall parts during
    multi-turn conversations. This store caches signatures across turns and sessions.
    """

    def __init__(self, cache_file: Optional[Path] = None):
        if cache_file is None:
            # Save in project root
            base_dir = Path(__file__).resolve().parent.parent
            self.cache_file = base_dir / "thought_signatures_cache.json"
        else:
            self.cache_file = cache_file

        self._id_to_signature: Dict[str, str] = {}
        self._id_to_name: Dict[str, str] = {}
        self._call_hash_to_signature: Dict[str, str] = {}
        self._name_to_signature: Dict[str, str] = {}
        self._latest_signature: str = DEFAULT_VALID_THOUGHT_SIGNATURE

        self._load_cache()

    def _hash_call(self, tool_name: str, args: Any) -> str:
        """Create a deterministic hash key for a tool name and arguments."""
        try:
            if isinstance(args, dict):
                args_str = json.dumps(args, sort_keys=True)
            elif isinstance(args, str):
                args_str = args
            else:
                args_str = str(args)
        except Exception:
            args_str = str(args)
        return f"{tool_name}:{args_str}"

    def _load_cache(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            self._id_to_signature = data.get("id_to_signature", {})
            self._id_to_name = data.get("id_to_name", {})
            self._call_hash_to_signature = data.get("call_hash_to_signature", {})
            self._name_to_signature = data.get("name_to_signature", {})
            latest = data.get("latest_signature")
            if latest:
                self._latest_signature = latest
        except Exception as e:
            logger.warning(f"Could not load thought signature cache from {self.cache_file}: {e}")

    def _save_cache(self) -> None:
        try:
            # Keep cache size reasonable (max 2000 entries)
            if len(self._id_to_signature) > 2000:
                keys_to_del = list(self._id_to_signature.keys())[:-1000]
                for k in keys_to_del:
                    self._id_to_signature.pop(k, None)
                    self._id_to_name.pop(k, None)

            data = {
                "latest_signature": self._latest_signature,
                "id_to_signature": self._id_to_signature,
                "id_to_name": self._id_to_name,
                "call_hash_to_signature": self._call_hash_to_signature,
                "name_to_signature": self._name_to_signature,
            }
            self.cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save thought signature cache: {e}")

    def store_signature(
        self,
        tool_id: Optional[str],
        tool_name: str,
        args: Any,
        signature: Optional[str],
    ) -> None:
        if not signature or not isinstance(signature, str):
            return

        sig = signature.strip()
        if not sig:
            return

        self._latest_signature = sig
        if tool_name:
            self._name_to_signature[tool_name] = sig

        if tool_id:
            self._id_to_signature[tool_id] = sig
            if tool_name:
                self._id_to_name[tool_id] = tool_name

        if tool_name:
            call_hash = self._hash_call(tool_name, args)
            self._call_hash_to_signature[call_hash] = sig

        self._save_cache()

    def get_signature(
        self,
        tool_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        args: Any = None,
    ) -> str:
        """
        Retrieves the exact thoughtSignature for a tool call.
        If not cached, cascades to call hash, tool name, or fallback signature.
        Never returns empty or None.
        """
        # 1. Match by tool_use id
        if tool_id and tool_id in self._id_to_signature:
            return self._id_to_signature[tool_id]

        # 2. Match by call hash (name + args)
        if tool_name and args is not None:
            call_hash = self._hash_call(tool_name, args)
            if call_hash in self._call_hash_to_signature:
                return self._call_hash_to_signature[call_hash]

        # 3. Match by tool_name
        if tool_name and tool_name in self._name_to_signature:
            return self._name_to_signature[tool_name]

        # 4. Return latest known valid signature or default fallback
        return self._latest_signature or DEFAULT_VALID_THOUGHT_SIGNATURE

    def get_tool_name(self, tool_id: str) -> Optional[str]:
        return self._id_to_name.get(tool_id)

    def clear(self) -> None:
        self._id_to_signature.clear()
        self._id_to_name.clear()
        self._call_hash_to_signature.clear()
        self._name_to_signature.clear()
        self._latest_signature = DEFAULT_VALID_THOUGHT_SIGNATURE
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except Exception:
                pass


# Global singleton instance
signature_store = ThoughtSignatureStore()
