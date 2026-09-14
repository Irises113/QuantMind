import json

import pytest

from backend.services.trade_shared.simulation_manager import SimulationAccountManager
from backend.shared.simulation_account_keys import account_lookup_keys, ledger_user_id_candidates


class _FakeRedisClient:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def eval(self, script, numkeys, *args):
        key = args[0]
        symbol = args[1]
        delta_cash = float(args[2])
        delta_volume = float(args[3])
        price = float(args[4])
        account = json.loads(self.store[key])
        account["cash"] = float(account.get("cash") or 0.0) + delta_cash
        positions = dict(account.get("positions") or {})
        pos = dict(positions.get(symbol) or {"volume": 0, "cost": 0, "market_value": 0, "price": 0})
        pos["volume"] = float(pos.get("volume") or 0.0) + delta_volume
        pos["price"] = price
        pos["market_value"] = float(pos["volume"] or 0.0) * price
        positions[symbol] = pos
        account["positions"] = positions
        account["market_value"] = sum(float(p.get("volume") or 0.0) * float(p.get("price") or 0.0) for p in positions.values())
        account["total_asset"] = float(account.get("cash") or 0.0) + float(account.get("market_value") or 0.0)
        self.store[key] = json.dumps(account, ensure_ascii=False)
        return {"success": True}


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()


@pytest.mark.asyncio
async def test_simulation_manager_writes_settings_and_account_json():
    redis = _FakeRedis()
    manager = SimulationAccountManager(redis)

    await manager.set_initial_cash(
        user_id=1,
        tenant_id="default",
        initial_cash=1_000_000,
    )
    settings = await manager.get_settings(
        user_id=1,
        tenant_id="default",
        default_initial_cash=500_000,
    )
    assert settings["initial_cash"] == 1_000_000
    assert json.loads(redis.client.get("simulation:settings:default:1"))["initial_cash"] == 1_000_000

    account = await manager.init_account(user_id=1, tenant_id="default", initial_cash=2_000_000)
    assert account["cash"] == 2_000_000
    assert json.loads(redis.client.get("simulation:account:default:1"))["cash"] == 2_000_000


@pytest.mark.asyncio
async def test_simulation_manager_account_update_uses_cache_helper():
    redis = _FakeRedis()
    manager = SimulationAccountManager(redis)

    await manager.init_account(user_id=1, tenant_id="default", initial_cash=1_000_000)
    result = await manager.update_balance(
        user_id=1,
        symbol="600000.SH",
        delta_cash=-1000,
        delta_volume=100,
        price=10.0,
        tenant_id="default",
    )

    assert result["success"] is True
    cached = json.loads(redis.client.get("simulation:account:default:1"))
    assert cached["cash"] == 999000
    assert cached["total_asset"] > 0


@pytest.mark.asyncio
async def test_get_account_reads_zfill_alias_key():
    redis = _FakeRedis()
    manager = SimulationAccountManager(redis)
    redis.client.set(
        "simulation:account:default:00000001",
        json.dumps({"cash": 1_000_000.0, "positions": {}}),
    )

    account = await manager.get_account(1, tenant_id="default")
    assert account is not None
    assert account["cash"] == 1_000_000.0


@pytest.mark.asyncio
async def test_get_account_reconnects_detached_redis(monkeypatch):
    shared = _FakeRedis()
    await SimulationAccountManager(shared).init_account(
        user_id=1, tenant_id="default", initial_cash=1_000_000
    )

    class _Detached:
        client = None

    manager = SimulationAccountManager(_Detached())
    monkeypatch.setattr(
        "backend.services.trade_shared.simulation_manager._shared_redis",
        lambda: shared.client,
    )
    account = await manager.get_account(1, tenant_id="default")
    assert account is not None
    assert account["cash"] == 1_000_000.0


def test_account_lookup_keys_covers_int_and_zfill():
    keys = account_lookup_keys("default", "00000001")
    assert keys[0] == "simulation:account:default:00000001"
    assert "simulation:account:default:1" in keys
    assert keys == list(dict.fromkeys(keys))


def test_ledger_user_id_candidates_does_not_mix_numeric_user_with_reserved_zero():
    assert ledger_user_id_candidates("00000001") == ["1", "00000001"]
    assert ledger_user_id_candidates("1") == ["1", "00000001"]
    assert ledger_user_id_candidates("admin") == ["admin", "0"]
    assert "0" not in ledger_user_id_candidates("00000001")
