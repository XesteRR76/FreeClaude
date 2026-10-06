import asyncio
import time
import pytest
from app.key_manager import KeyManager, mask_key


def test_mask_key():
    assert mask_key("AIzaSyB123456789XyZ") == "AIza...9XyZ"
    assert mask_key("short") == "***"
    assert mask_key("") == "[EMPTY_KEY]"


@pytest.mark.asyncio
async def test_key_rotation_round_robin():
    keys = ["key-alpha", "key-beta", "key-gamma"]
    km = KeyManager(keys=keys, cooldown_seconds=60.0)

    # First three requests should cycle through all 3 keys in order
    k1 = await km.get_available_key()
    k2 = await km.get_available_key()
    k3 = await km.get_available_key()
    k4 = await km.get_available_key()

    assert k1 == "key-alpha"
    assert k2 == "key-beta"
    assert k3 == "key-gamma"
    assert k4 == "key-alpha"


@pytest.mark.asyncio
async def test_key_cooldown_on_429():
    keys = ["key-alpha", "key-beta", "key-gamma"]
    km = KeyManager(keys=keys, cooldown_seconds=2.0)

    # Put key-alpha on cooldown
    km.mark_cooldown("key-alpha", duration=2.0)
    assert km.is_cooling_down("key-alpha") is True
    assert km.get_cooldown_remaining("key-alpha") > 1.0

    # Getting available keys should now only yield beta and gamma
    k_next_1 = await km.get_available_key()
    k_next_2 = await km.get_available_key()
    k_next_3 = await km.get_available_key()

    assert k_next_1 in ("key-beta", "key-gamma")
    assert k_next_2 in ("key-beta", "key-gamma")
    assert k_next_3 in ("key-beta", "key-gamma")
    assert "key-alpha" not in (k_next_1, k_next_2, k_next_3)


@pytest.mark.asyncio
async def test_all_keys_in_cooldown():
    keys = ["key-alpha", "key-beta"]
    km = KeyManager(keys=keys, cooldown_seconds=10.0)

    km.mark_cooldown("key-alpha", duration=10.0)
    km.mark_cooldown("key-beta", duration=10.0)

    assert await km.get_available_key() is None


@pytest.mark.asyncio
async def test_excluded_keys():
    keys = ["key-alpha", "key-beta", "key-gamma"]
    km = KeyManager(keys=keys, cooldown_seconds=60.0)

    # Exclude key-alpha and key-beta (simulating already tried in current request)
    k = await km.get_available_key(excluded_keys={"key-alpha", "key-beta"})
    assert k == "key-gamma"
