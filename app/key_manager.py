import asyncio
import time
from typing import Dict, List, Optional, Set, Tuple
from app.config import settings


def mask_key(key: str) -> str:
    """Mask an API key for safe logging, showing first 4 and last 4 characters."""
    if not key:
        return "[EMPTY_KEY]"
    if len(key) < 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


class KeyManager:
    """
    Manages Gemini API keys, tracking cooldowns per (key, model) or global key,
    allowing independent quotas across different Gemini models.
    """

    def __init__(self, keys: Optional[List[str]] = None, cooldown_seconds: float = 60.0):
        self._keys: List[str] = list(keys) if keys is not None else list(settings.gemini_api_keys)
        self.cooldown_seconds = cooldown_seconds
        # Cooldown map: (key, model_or_global) -> expire_at_timestamp
        self._cooldowns: Dict[Tuple[str, str], float] = {}
        self._index: int = 0
        self._lock = asyncio.Lock()

    def update_keys(self, keys: List[str]):
        """Dynamically update keys from settings or runtime reload."""
        self._keys = [k for k in keys if k]
        # Clean up cooldowns for removed keys
        self._cooldowns = {
            (k, m): v for (k, m), v in self._cooldowns.items() if k in self._keys
        }

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    def is_cooling_down(self, key: str, model: str = "") -> bool:
        """Check if a key is on cooldown (either model-specific or global)."""
        now = time.time()
        # Check global key cooldown
        if self._cooldowns.get((key, ""), 0.0) > now:
            return True
        # Check model-specific cooldown
        if model and self._cooldowns.get((key, model), 0.0) > now:
            return True
        return False

    def get_cooldown_remaining(self, key: str, model: str = "") -> float:
        """Get remaining cooldown time in seconds."""
        now = time.time()
        exp_global = self._cooldowns.get((key, ""), 0.0)
        exp_model = self._cooldowns.get((key, model), 0.0) if model else 0.0
        expire_at = max(exp_global, exp_model)
        return max(0.0, expire_at - now)

    def mark_cooldown(
        self, key: str, model: str = "", duration: Optional[float] = None
    ):
        """
        Mark a key on cooldown for a specific model (or globally if model is empty).
        Default duration is 60s.
        """
        dur = duration if duration is not None else self.cooldown_seconds
        expire_at = time.time() + dur
        self._cooldowns[(key, model)] = expire_at

    def reset_cooldown(self, key: str, model: str = ""):
        """Clear cooldown for a key (specific model or all)."""
        if model:
            self._cooldowns.pop((key, model), None)
        else:
            to_del = [k_m for k_m in self._cooldowns if k_m[0] == key]
            for k_m in to_del:
                self._cooldowns.pop(k_m, None)

    async def get_available_key(
        self, model: str = "", excluded_keys: Optional[Set[str]] = None
    ) -> Optional[str]:
        """
        Get the next available key that is not in cooldown for the requested model
        and not in excluded_keys. Uses round-robin.
        """
        async with self._lock:
            if not self._keys:
                return None

            excluded = excluded_keys or set()
            now = time.time()
            n = len(self._keys)

            for i in range(n):
                idx = (self._index + i) % n
                candidate = self._keys[idx]

                if candidate in excluded:
                    continue

                if not self.is_cooling_down(candidate, model=model):
                    # Key is active and ready for this model
                    self._index = (idx + 1) % n
                    return candidate

            return None

    def get_status(self) -> List[Dict]:
        """Return status summary for all managed keys."""
        now = time.time()
        status_list = []
        for k in self._keys:
            # Active if not globally cooled down
            in_global_cd = self._cooldowns.get((k, ""), 0.0) > now
            model_cds = {
                m: round(exp - now, 1)
                for (key_cand, m), exp in self._cooldowns.items()
                if key_cand == k and m != "" and exp > now
            }
            status_list.append(
                {
                    "masked": mask_key(k),
                    "in_global_cooldown": in_global_cd,
                    "model_cooldowns": model_cds,
                }
            )
        return status_list


key_manager = KeyManager(settings.gemini_api_keys, settings.cooldown_seconds)
