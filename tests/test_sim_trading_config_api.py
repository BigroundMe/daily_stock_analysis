# -*- coding: utf-8 -*-
"""模拟交易配置端点 API 测试（GET/PUT /sim-trading/config）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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


class SimTradingConfigApiTestCase(unittest.TestCase):
    """模拟交易配置端点集成测试。"""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "test_config_api.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                    "SIM_TRADING_ENABLED=true",
                    "SIM_TRADING_APPROVAL_REQUIRED=false",
                    "SIM_TRADING_ACCOUNT_ID=1",
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
        _reset_auth_globals()
        self.temp_dir.cleanup()

    # ── GET /sim-trading/config ────────────────────────

    def test_get_config_returns_current_values(self) -> None:
        resp = self.client.get("/api/v1/portfolio/sim-trading/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("approval_required", data)
        self.assertIn("sim_trading_enabled", data)
        self.assertIn("sim_trading_account_id", data)
        self.assertIs(data["sim_trading_enabled"], True)

    # ── PUT /sim-trading/config ────────────────────────

    def test_update_config_enables_approval(self) -> None:
        resp = self.client.put(
            "/api/v1/portfolio/sim-trading/config",
            json={"approval_required": True},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIs(data["approval_required"], True)

        # 验证内存中的 Config 已更新
        config = Config.get_instance()
        self.assertTrue(config.sim_trading_approval_required)

        # 验证 .env 文件已持久化
        env_content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("SIM_TRADING_APPROVAL_REQUIRED=true", env_content)

    def test_update_config_disables_approval(self) -> None:
        # 先开启
        self.client.put("/api/v1/portfolio/sim-trading/config", json={"approval_required": True})
        # 再关闭
        resp = self.client.put(
            "/api/v1/portfolio/sim-trading/config",
            json={"approval_required": False},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIs(data["approval_required"], False)

        env_content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("SIM_TRADING_APPROVAL_REQUIRED=false", env_content)

    def test_update_config_preserves_other_env_keys(self) -> None:
        self.client.put("/api/v1/portfolio/sim-trading/config", json={"approval_required": True})
        env_content = self.env_path.read_text(encoding="utf-8")
        self.assertIn("STOCK_LIST=600519", env_content)
        self.assertIn("SIM_TRADING_ENABLED=true", env_content)

    def test_get_config_reflects_put_update(self) -> None:
        self.client.put("/api/v1/portfolio/sim-trading/config", json={"approval_required": True})
        resp = self.client.get("/api/v1/portfolio/sim-trading/config")
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.json()["approval_required"], True)


if __name__ == "__main__":
    unittest.main()
