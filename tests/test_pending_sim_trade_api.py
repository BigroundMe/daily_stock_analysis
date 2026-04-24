# -*- coding: utf-8 -*-
"""审批端点 API 测试（GET/POST/DELETE /sim-trades/…）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from fastapi.testclient import TestClient

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


class PendingSimTradeApiTestCase(unittest.TestCase):
    """审批端点集成测试。"""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "test_pending_api.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                    "SIM_TRADING_ENABLED=true",
                    "SIM_TRADING_APPROVAL_REQUIRED=true",
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
        self.db = DatabaseManager.get_instance()

        # 创建测试账户并插入 pending 记录
        from src.services.portfolio_service import PortfolioService

        svc = PortfolioService()
        acct = svc.create_account(name="Test", broker="Demo", market="cn", base_currency="CNY")
        self.account_id = acct["id"]
        self.repo = svc.repo

        # 插入一笔 pending 记录
        self.pending_id = self.repo.add_pending_sim_trade(
            account_id=self.account_id,
            symbol="600519",
            side="buy",
            quantity=100,
            price=1800.0,
            fee=5.0,
            tax=0.0,
            note="test pending",
            llm_reasoning="LLM says buy",
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        _reset_auth_globals()
        self.temp_dir.cleanup()

    # ── GET /sim-trades/pending ────────────────────────

    def test_list_pending_trades(self) -> None:
        resp = self.client.get("/api/v1/portfolio/sim-trades/pending")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["symbol"], "600519")
        self.assertEqual(item["side"], "buy")
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["quantity"], 100.0)

    def test_list_pending_trades_with_account_filter(self) -> None:
        resp = self.client.get(
            "/api/v1/portfolio/sim-trades/pending",
            params={"account_id": 99999},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 0)

    def test_list_pending_trades_pagination(self) -> None:
        resp = self.client.get(
            "/api/v1/portfolio/sim-trades/pending",
            params={"page": 2, "page_size": 10},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 1)
        self.assertEqual(len(resp.json()["items"]), 0)

    # ── POST /sim-trades/{id}/approve ──────────────────

    def test_approve_pending_trade_success(self) -> None:
        # 先注入现金使余额足够
        from src.services.portfolio_service import PortfolioService
        from datetime import date

        svc = PortfolioService()
        svc.record_cash_ledger(
            account_id=self.account_id,
            event_date=date.today(),
            direction="in",
            amount=500000.0,
        )

        resp = self.client.post(
            f"/api/v1/portfolio/sim-trades/{self.pending_id}/approve",
            json={"reviewer_note": "LGTM"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        self.assertIn("trade_id", body)

    def test_approve_not_found(self) -> None:
        resp = self.client.post("/api/v1/portfolio/sim-trades/99999/approve")
        self.assertEqual(resp.status_code, 404)

    def test_approve_already_rejected(self) -> None:
        # 先 reject
        self.repo.update_pending_sim_trade_status(self.pending_id, "rejected", "no")
        resp = self.client.post(f"/api/v1/portfolio/sim-trades/{self.pending_id}/approve")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        msg = body.get("message") or body.get("detail") or ""
        self.assertIn("already", msg)

    # ── POST /sim-trades/{id}/reject ───────────────────

    def test_reject_pending_trade_success(self) -> None:
        resp = self.client.post(
            f"/api/v1/portfolio/sim-trades/{self.pending_id}/reject",
            json={"reviewer_note": "Too risky"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "rejected")

    def test_reject_not_found(self) -> None:
        resp = self.client.post("/api/v1/portfolio/sim-trades/99999/reject")
        self.assertEqual(resp.status_code, 404)

    def test_reject_already_approved(self) -> None:
        # 先注入现金并 approve
        from src.services.portfolio_service import PortfolioService
        from datetime import date

        svc = PortfolioService()
        svc.record_cash_ledger(
            account_id=self.account_id, event_date=date.today(), direction="in", amount=500000.0
        )
        from src.services.sim_trading_service import SimTradingService

        sim_svc = SimTradingService()
        sim_svc.execute_pending_trade(self.pending_id, "ok")

        resp = self.client.post(f"/api/v1/portfolio/sim-trades/{self.pending_id}/reject")
        self.assertEqual(resp.status_code, 400)

    # ── DELETE /sim-trades/{id} ────────────────────────

    def test_delete_pending_trade_success(self) -> None:
        resp = self.client.delete(f"/api/v1/portfolio/sim-trades/{self.pending_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deleted")

    def test_delete_not_found(self) -> None:
        resp = self.client.delete("/api/v1/portfolio/sim-trades/99999")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
