# -*- coding: utf-8 -*-
"""模拟交易审批功能单元测试（Repository 层）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test-key")


class TestPendingSimTradeCRUD(unittest.TestCase):
    """测试 PendingSimTrade 的 Repository CRUD 操作。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = self.db_path

        from src.config import Config
        Config.reset_instance()

        from src.storage import DatabaseManager
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

        from src.repositories.portfolio_repo import PortfolioRepository
        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        self._create_test_account()

    def _create_test_account(self):
        with self.db.get_session() as session:
            from src.storage import PortfolioAccount
            acct = PortfolioAccount(
                name="test_account", broker="test",
                market="cn", base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

    def test_add_pending_sim_trade(self):
        """测试添加待审批交易。"""
        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id,
            symbol="600519",
            side="buy",
            quantity=100,
            price=1850.0,
            fee=5.0,
            tax=0.0,
            note="sim buy",
            llm_reasoning="MACD 金叉，建议买入",
        )
        self.assertIsNotNone(pid)
        self.assertIsInstance(pid, int)

    def test_get_pending_sim_trade(self):
        """测试获取单条待审批交易。"""
        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        trade = self.repo.get_pending_sim_trade(pid)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.symbol, "600519")
        self.assertEqual(trade.status, "pending")

    def test_list_pending_sim_trades(self):
        """测试列出待审批交易。"""
        self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="000858",
            side="sell", quantity=50, price=200.0,
        )
        items, total = self.repo.list_pending_sim_trades(
            account_id=self.account_id, status="pending", page=1, page_size=20
        )
        self.assertEqual(total, 2)
        self.assertEqual(len(items), 2)

    def test_update_pending_status_to_approved(self):
        """测试更新状态为 approved。"""
        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        result = self.repo.update_pending_sim_trade_status(
            pid, "approved", reviewer_note="同意"
        )
        self.assertTrue(result)
        trade = self.repo.get_pending_sim_trade(pid)
        self.assertEqual(trade.status, "approved")
        self.assertEqual(trade.reviewer_note, "同意")
        self.assertIsNotNone(trade.reviewed_at)

    def test_update_pending_status_to_rejected(self):
        """测试更新状态为 rejected。"""
        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        result = self.repo.update_pending_sim_trade_status(
            pid, "rejected", reviewer_note="风险过高"
        )
        self.assertTrue(result)
        trade = self.repo.get_pending_sim_trade(pid)
        self.assertEqual(trade.status, "rejected")

    def test_delete_pending_sim_trade(self):
        """测试删除待审批交易。"""
        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        result = self.repo.delete_pending_sim_trade(pid)
        self.assertTrue(result)
        trade = self.repo.get_pending_sim_trade(pid)
        self.assertIsNone(trade)

    def test_has_pending_or_approved_today(self):
        """测试当日已有 pending/approved 记录的检查。"""
        self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        result = self.repo.has_pending_or_approved_today(self.account_id)
        self.assertTrue(result)

    def test_has_pending_or_approved_today_empty(self):
        """测试当日无记录时返回 False。"""
        result = self.repo.has_pending_or_approved_today(self.account_id)
        self.assertFalse(result)

    def tearDown(self):
        import shutil
        from src.storage import DatabaseManager
        from src.config import Config
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
