# -*- coding: utf-8 -*-
"""API 测试：PUT /api/v1/portfolio/trades/{trade_id} 交易编辑端点。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class TestPortfolioTradeEditApi(unittest.TestCase):
    """PUT /api/v1/portfolio/trades/{trade_id} 端点测试。"""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "trade_edit_api_test.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        app = create_app(static_dir=self.data_dir / "empty-static")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _create_account(self) -> int:
        resp = self.client.post(
            "/api/v1/portfolio/accounts",
            json={"name": "Test", "broker": "Demo", "market": "cn", "base_currency": "CNY"},
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["id"]

    def _create_trade(self, account_id: int, *, symbol: str = "600519",
                      side: str = "buy", quantity: float = 100,
                      price: float = 50.0, fee: float = 0.0,
                      tax: float = 0.0) -> int:
        resp = self.client.post(
            "/api/v1/portfolio/trades",
            json={
                "account_id": account_id,
                "symbol": symbol,
                "trade_date": "2026-01-02",
                "side": side,
                "quantity": quantity,
                "price": price,
                "fee": fee,
                "tax": tax,
                "market": "cn",
                "currency": "CNY",
            },
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["id"]

    def test_update_trade_success(self) -> None:
        """PUT /trades/{id} 正常编辑成功。"""
        account_id = self._create_account()
        trade_id = self._create_trade(account_id, quantity=100, price=50.0)

        resp = self.client.put(
            f"/api/v1/portfolio/trades/{trade_id}",
            json={"quantity": 200, "price": 55.0, "note": "updated"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("trade", data)
        self.assertEqual(data["trade"]["quantity"], 200)
        self.assertEqual(data["trade"]["price"], 55.0)
        self.assertEqual(data["trade"]["note"], "updated")
        self.assertEqual(data["oversell_violations"], [])

    def test_update_trade_not_found_returns_404(self) -> None:
        """PUT /trades/{id} 不存在的 trade 返回 404。"""
        resp = self.client.put(
            "/api/v1/portfolio/trades/99999",
            json={"quantity": 100},
        )
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["error"], "not_found")

    def test_update_trade_oversell_returns_400(self) -> None:
        """PUT /trades/{id} oversell 返回 400 + violations。"""
        account_id = self._create_account()
        buy_id = self._create_trade(account_id, side="buy", quantity=100, price=50.0)
        # 卖出 80 股
        self._create_trade(account_id, side="sell", quantity=80, price=60.0)

        # 把买入数量从 100 改为 50，导致 oversell（持仓 50 - 已卖 80 = -30）
        resp = self.client.put(
            f"/api/v1/portfolio/trades/{buy_id}",
            json={"quantity": 50},
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "oversell")
        self.assertIn("violations", body)
        self.assertIsInstance(body["violations"], list)
        self.assertTrue(len(body["violations"]) > 0)

    def test_update_trade_empty_body_no_change(self) -> None:
        """PUT /trades/{id} 空 body 返回成功（无变更）。"""
        account_id = self._create_account()
        trade_id = self._create_trade(account_id, quantity=100, price=50.0)

        resp = self.client.put(
            f"/api/v1/portfolio/trades/{trade_id}",
            json={},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["trade"]["quantity"], 100)
        self.assertEqual(data["trade"]["price"], 50.0)

    def test_update_trade_fee_and_tax(self) -> None:
        """PUT /trades/{id} 仅修改手续费和税费。"""
        account_id = self._create_account()
        trade_id = self._create_trade(account_id, quantity=100, price=50.0)

        resp = self.client.put(
            f"/api/v1/portfolio/trades/{trade_id}",
            json={"fee": 10.5, "tax": 3.0},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["trade"]["fee"], 10.5)
        self.assertAlmostEqual(data["trade"]["tax"], 3.0)


if __name__ == "__main__":
    unittest.main()
