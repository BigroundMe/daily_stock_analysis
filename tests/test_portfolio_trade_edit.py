# -*- coding: utf-8 -*-
"""交易流水编辑功能单元测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from src.config import Config
from src.repositories.portfolio_repo import PortfolioRepository
from src.storage import DatabaseManager, PortfolioAccount


class TestUpdateTradeInSession(unittest.TestCase):
    """测试 PortfolioRepository.update_trade / update_trade_in_session()。"""

    def setUp(self):
        """创建临时数据库和测试交易记录。"""
        self.tmp_dir = tempfile.mkdtemp()
        db_path = Path(self.tmp_dir) / "test.db"
        os.environ["DATABASE_PATH"] = str(db_path)

        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(f"sqlite:///{db_path}")

        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        self._create_test_account()
        # 创建测试交易
        self._create_test_trade()

    def _create_test_account(self):
        with self.db.get_session() as session:
            acct = PortfolioAccount(
                name="test_account",
                broker="test",
                market="cn",
                base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

    def _create_test_trade(self):
        self.trade = self.repo.add_trade(
            account_id=self.account_id,
            trade_uid=None,
            symbol="600519",
            market="cn",
            currency="CNY",
            trade_date=date(2026, 4, 20),
            side="buy",
            quantity=100,
            price=1850.0,
            fee=5.0,
            tax=0.0,
            note="test trade",
        )
        self.trade_id = self.trade.id

    def test_update_quantity(self):
        """测试更新数量。"""
        result = self.repo.update_trade(self.trade_id, {"quantity": 200})
        self.assertIsNotNone(result)
        self.assertEqual(result.quantity, 200)
        # 其他字段不变
        self.assertEqual(result.price, 1850.0)

    def test_update_multiple_fields(self):
        """测试同时更新多个字段。"""
        result = self.repo.update_trade(
            self.trade_id,
            {
                "quantity": 300,
                "price": 1900.0,
                "fee": 10.0,
                "tax": 2.0,
                "note": "updated",
            },
        )
        self.assertEqual(result.quantity, 300)
        self.assertEqual(result.price, 1900.0)
        self.assertEqual(result.fee, 10.0)
        self.assertEqual(result.tax, 2.0)
        self.assertEqual(result.note, "updated")

    def test_update_nonexistent_trade_returns_none(self):
        """测试更新不存在的交易返回 None。"""
        result = self.repo.update_trade(99999, {"quantity": 100})
        self.assertIsNone(result)

    def test_update_rejects_forbidden_fields(self):
        """测试不允许修改的字段（symbol/side/trade_date）被忽略。"""
        result = self.repo.update_trade(
            self.trade_id,
            {"quantity": 200, "symbol": "AAPL", "side": "sell"},
        )
        self.assertEqual(result.quantity, 200)
        self.assertEqual(result.symbol, "600519")  # 未被修改
        self.assertEqual(result.side, "buy")  # 未被修改

    def tearDown(self):
        import shutil

        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class TestUpdateTradeEvent(unittest.TestCase):
    """测试 PortfolioService.update_trade_event() + oversell 硬阻断。"""

    def setUp(self):
        """创建临时数据库、账户和交易序列。

        交易序列：
        1. buy 600519 100股 @ 1850
        2. buy 600519 100股 @ 1860
        3. sell 600519 150股 @ 1870
        position = 100 + 100 - 150 = 50
        """
        self.tmp_dir = tempfile.mkdtemp()
        db_path = Path(self.tmp_dir) / "test_svc.db"
        os.environ["DATABASE_PATH"] = str(db_path)

        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(f"sqlite:///{db_path}")

        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        with self.db.get_session() as session:
            acct = PortfolioAccount(
                name="svc_test_account",
                broker="test",
                market="cn",
                base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

        # 创建交易序列
        self.buy1 = self.repo.add_trade(
            account_id=self.account_id,
            trade_uid=None,
            symbol="600519",
            market="cn",
            currency="CNY",
            trade_date=date(2026, 4, 20),
            side="buy",
            quantity=100,
            price=1850.0,
            fee=5.0,
            tax=0.0,
        )
        self.buy2 = self.repo.add_trade(
            account_id=self.account_id,
            trade_uid=None,
            symbol="600519",
            market="cn",
            currency="CNY",
            trade_date=date(2026, 4, 21),
            side="buy",
            quantity=100,
            price=1860.0,
            fee=5.0,
            tax=0.0,
        )
        self.sell1 = self.repo.add_trade(
            account_id=self.account_id,
            trade_uid=None,
            symbol="600519",
            market="cn",
            currency="CNY",
            trade_date=date(2026, 4, 22),
            side="sell",
            quantity=150,
            price=1870.0,
            fee=5.0,
            tax=0.0,
        )

        from src.services.portfolio_service import PortfolioService

        self.svc = PortfolioService(repo=self.repo)

    def test_update_quantity_no_oversell(self):
        """编辑不导致 oversell 时正常更新。"""
        result = self.svc.update_trade_event(self.buy1.id, {"quantity": 200})
        self.assertEqual(result.quantity, 200)

    def test_update_price_no_oversell(self):
        """编辑 price 不影响持仓数量，不触发 oversell。"""
        result = self.svc.update_trade_event(self.buy1.id, {"price": 1900.0})
        self.assertEqual(result.price, 1900.0)
        self.assertEqual(result.quantity, 100)  # quantity 不变

    def test_update_quantity_causes_oversell(self):
        """编辑导致 oversell 时抛出 OversellError。"""
        from src.services.portfolio_service import OversellError

        # 将第一笔 buy 的数量从 100 减到 10
        # position = 10 + 100 - 150 = -40，触发 oversell
        with self.assertRaises(OversellError) as ctx:
            self.svc.update_trade_event(self.buy1.id, {"quantity": 10})
        self.assertTrue(len(ctx.exception.violations) > 0)

    def test_update_quantity_oversell_rolls_back(self):
        """oversell 时事务回滚，原始值不变。"""
        from src.services.portfolio_service import OversellError

        try:
            self.svc.update_trade_event(self.buy1.id, {"quantity": 10})
        except OversellError:
            pass
        # 验证原值未变——重新从数据库读取
        with self.db.get_session() as session:
            from src.storage import PortfolioTrade

            trade = session.get(PortfolioTrade, self.buy1.id)
            self.assertEqual(trade.quantity, 100)  # 原始值

    def test_update_nonexistent_trade_raises_value_error(self):
        """更新不存在的交易抛出 ValueError。"""
        with self.assertRaises(ValueError):
            self.svc.update_trade_event(99999, {"quantity": 100})

    def test_update_ignores_forbidden_fields(self):
        """不允许修改的字段（symbol/side/trade_date）被忽略。"""
        result = self.svc.update_trade_event(
            self.buy1.id,
            {"quantity": 200, "symbol": "AAPL", "side": "sell"},
        )
        self.assertEqual(result.quantity, 200)
        self.assertEqual(result.symbol, "600519")  # 未被修改
        self.assertEqual(result.side, "buy")  # 未被修改

    def tearDown(self):
        import shutil

        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
