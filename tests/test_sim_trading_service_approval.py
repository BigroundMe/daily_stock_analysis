# -*- coding: utf-8 -*-
"""SimTradingService 审批逻辑单元测试。

覆盖场景：
- check_approval_required() 返回 True/False
- save_pending_trades() 正确保存到 PendingSimTrade 表
- execute_pending_trade() 审批通过后正确执行交易
- execute_pending_trade() 使用 created_at 日期作为 trade_date
- execute_pending_trade() 中 record_trade + update_status 在同一事务内
- run() 方法在审批开启时走 pending 路径
- run() 方法在审批关闭时走原始路径
- _has_executed_today() 同时检查 pending 表
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")


def _reset_singletons():
    """重置 Config 和 DatabaseManager 单例，确保测试隔离。"""
    from src.config import Config
    from src.storage import DatabaseManager
    Config.reset_instance()
    DatabaseManager.reset_instance()


class TestCheckApprovalRequired(unittest.TestCase):
    """测试 check_approval_required() 返回值取决于配置。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = self.db_path
        _reset_singletons()

    def tearDown(self):
        import shutil
        _reset_singletons()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_approval_required_true(self):
        """当配置 sim_trading_approval_required=True 时返回 True。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.config = MagicMock()
        svc.config.sim_trading_approval_required = True
        self.assertTrue(svc.check_approval_required())

    def test_approval_required_false(self):
        """当配置 sim_trading_approval_required=False 时返回 False。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.config = MagicMock()
        svc.config.sim_trading_approval_required = False
        self.assertFalse(svc.check_approval_required())


class TestSavePendingTrades(unittest.TestCase):
    """测试 save_pending_trades() 正确保存到 PendingSimTrade 表。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = self.db_path
        _reset_singletons()

        from src.storage import DatabaseManager
        self.db = DatabaseManager.get_instance()

        from src.repositories.portfolio_repo import PortfolioRepository
        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        with self.db.get_session() as session:
            from src.storage import PortfolioAccount
            acct = PortfolioAccount(
                name="test_account", broker="test",
                market="cn", base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

    def tearDown(self):
        import shutil
        _reset_singletons()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_pending_trades_basic(self):
        """save_pending_trades 保存多笔交易并返回 pending_id 列表。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.repo = self.repo
        svc.config = MagicMock()
        svc.config.sim_trading_default_commission = 5.0

        actions = [
            {"symbol": "600519", "side": "buy", "quantity": 100, "price": 1850.0, "reason": "MACD 金叉"},
            {"symbol": "000858", "side": "sell", "quantity": 200, "price": 200.0, "reason": "止盈"},
        ]
        pending_ids = svc.save_pending_trades(actions, self.account_id)

        self.assertEqual(len(pending_ids), 2)
        for pid in pending_ids:
            self.assertIsInstance(pid, int)

        # 验证 DB 中的记录
        trade1 = self.repo.get_pending_sim_trade(pending_ids[0])
        self.assertEqual(trade1.symbol, "600519")
        self.assertEqual(trade1.side, "buy")
        self.assertEqual(trade1.quantity, 100)
        self.assertEqual(trade1.price, 1850.0)
        self.assertEqual(trade1.fee, 5.0)
        self.assertEqual(trade1.status, "pending")
        self.assertIn("600519", trade1.note)
        self.assertEqual(trade1.llm_reasoning, "MACD 金叉")

        trade2 = self.repo.get_pending_sim_trade(pending_ids[1])
        self.assertEqual(trade2.symbol, "000858")
        self.assertEqual(trade2.side, "sell")

    def test_save_pending_trades_empty(self):
        """空 actions 列表返回空列表。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.repo = self.repo
        svc.config = MagicMock()
        svc.config.sim_trading_default_commission = 5.0

        pending_ids = svc.save_pending_trades([], self.account_id)
        self.assertEqual(pending_ids, [])


class TestExecutePendingTrade(unittest.TestCase):
    """测试 execute_pending_trade() 审批执行逻辑。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = self.db_path
        _reset_singletons()

        from src.storage import DatabaseManager
        self.db = DatabaseManager.get_instance()

        from src.repositories.portfolio_repo import PortfolioRepository
        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        with self.db.get_session() as session:
            from src.storage import PortfolioAccount
            acct = PortfolioAccount(
                name="test_account", broker="test",
                market="cn", base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

    def tearDown(self):
        import shutil
        _reset_singletons()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_execute_pending_trade_not_found(self):
        """pending_id 不存在时返回失败。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.repo = self.repo
        result = svc.execute_pending_trade(9999)
        self.assertFalse(result["success"])
        self.assertIn("not found", result["message"])

    def test_execute_pending_trade_already_approved(self):
        """已审批的记录不能重复执行。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.repo = self.repo

        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )
        self.repo.update_pending_sim_trade_status(pid, "approved")

        result = svc.execute_pending_trade(pid)
        self.assertFalse(result["success"])
        self.assertIn("already", result["message"])

    def test_execute_pending_trade_uses_created_at_date(self):
        """trade_date 使用 pending.created_at 的日期部分。"""
        from src.services.sim_trading_service import SimTradingService

        mock_ps = MagicMock()
        mock_ps.record_trade.return_value = {"id": 42}
        svc = SimTradingService(portfolio_service=mock_ps)
        svc.repo = self.repo

        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0, fee=5.0,
        )
        pending = self.repo.get_pending_sim_trade(pid)
        expected_date = pending.created_at.date() if pending.created_at else date.today()

        result = svc.execute_pending_trade(pid, reviewer_note="批准")
        self.assertTrue(result["success"])

        # 验证 record_trade 的 trade_date 参数
        call_kwargs = mock_ps.record_trade.call_args
        self.assertEqual(call_kwargs.kwargs.get("trade_date") or call_kwargs[1].get("trade_date"), expected_date)

    def test_execute_pending_trade_success(self):
        """审批通过后正确执行交易。"""
        from src.services.sim_trading_service import SimTradingService

        mock_ps = MagicMock()
        mock_ps.record_trade.return_value = {"id": 42}
        svc = SimTradingService(portfolio_service=mock_ps)
        svc.repo = self.repo

        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0, fee=5.0, tax=0.0,
            note="[sim-trading] 600519", llm_reasoning="买入理由",
        )

        result = svc.execute_pending_trade(pid, reviewer_note="批准")
        self.assertTrue(result["success"])
        self.assertEqual(result["trade_id"], 42)

        # 验证 record_trade 被调用
        mock_ps.record_trade.assert_called_once()
        call_kwargs = mock_ps.record_trade.call_args.kwargs
        self.assertEqual(call_kwargs["account_id"], self.account_id)
        self.assertEqual(call_kwargs["symbol"], "600519")
        self.assertEqual(call_kwargs["side"], "buy")
        self.assertEqual(call_kwargs["quantity"], 100)
        self.assertEqual(call_kwargs["price"], 1850.0)
        self.assertEqual(call_kwargs["fee"], 5.0)

        # 验证 pending 状态已更新为 approved
        updated = self.repo.get_pending_sim_trade(pid)
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.reviewer_note, "批准")

    def test_execute_pending_trade_record_failure_rolls_back(self):
        """record_trade 失败时返回错误，pending 状态不变。"""
        from src.services.sim_trading_service import SimTradingService

        mock_ps = MagicMock()
        mock_ps.record_trade.side_effect = Exception("insufficient cash")
        svc = SimTradingService(portfolio_service=mock_ps)
        svc.repo = self.repo

        pid = self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )

        result = svc.execute_pending_trade(pid)
        self.assertFalse(result["success"])
        self.assertIn("insufficient cash", result["message"])

        # pending 状态应仍为 pending
        pending = self.repo.get_pending_sim_trade(pid)
        self.assertEqual(pending.status, "pending")


class TestRunApprovalBranch(unittest.TestCase):
    """测试 run() 方法的审批分支逻辑。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = self.db_path
        _reset_singletons()

        from src.storage import DatabaseManager
        self.db = DatabaseManager.get_instance()

        from src.repositories.portfolio_repo import PortfolioRepository
        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        with self.db.get_session() as session:
            from src.storage import PortfolioAccount
            acct = PortfolioAccount(
                name="test_account", broker="test",
                market="cn", base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

    def tearDown(self):
        import shutil
        _reset_singletons()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_service(self, approval_required=True):
        """创建一个 mock 了外部依赖的 SimTradingService。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=MagicMock())
        svc.repo = self.repo
        svc.config = MagicMock()
        svc.config.sim_trading_enabled = True
        svc.config.sim_trading_account_id = self.account_id
        svc.config.sim_trading_approval_required = approval_required
        svc.config.sim_trading_default_commission = 5.0
        svc.config.sim_trading_max_single_amount = 100000.0
        svc.config.sim_trading_model = "test-model"
        svc.config.sim_trading_fallback_models = []
        svc.config.litellm_model = "test-model"
        svc.config.llm_temperature = 0.7
        return svc

    @patch("src.services.sim_trading_service.SimTradingService.call_llm")
    @patch("src.services.sim_trading_service.SimTradingService.build_portfolio_context")
    @patch("src.services.sim_trading_service.SimTradingService._has_executed_today")
    def test_run_approval_on_saves_pending(self, mock_has_exec, mock_ctx, mock_llm):
        """审批开启 + is_scheduled=True 时走 pending 路径。"""
        mock_has_exec.return_value = False
        mock_ctx.return_value = {
            "account_id": self.account_id,
            "total_equity": 500000,
            "cash_balance": 200000,
            "positions": [],
            "recent_trades": [],
        }
        mock_llm.return_value = '{"trades": [{"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1850.0, "reason": "test"}], "portfolio_summary": "test"}'

        svc = self._make_service(approval_required=True)
        fake_result = MagicMock()
        fake_result.code = "600519"
        fake_result.name = "贵州茅台"
        fake_result.sentiment_score = 80
        fake_result.decision_type = "buy"
        fake_result.operation_advice = "建议买入"
        fake_result.analysis_summary = "测试"
        fake_result.current_price = 1850.0

        result = svc.run([fake_result], is_scheduled=True)

        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["pending_count"], 1)
        self.assertIsInstance(result["pending_ids"], list)
        self.assertEqual(len(result["pending_ids"]), 1)

        # 验证 validate_and_execute 没有被调用
        svc.portfolio_service.record_trade.assert_not_called()

    @patch("src.services.sim_trading_service.SimTradingService.call_llm")
    @patch("src.services.sim_trading_service.SimTradingService.build_portfolio_context")
    @patch("src.services.sim_trading_service.SimTradingService._has_executed_today")
    def test_run_approval_off_executes_directly(self, mock_has_exec, mock_ctx, mock_llm):
        """审批关闭时走原始执行路径。"""
        mock_has_exec.return_value = False
        mock_ctx.return_value = {
            "account_id": self.account_id,
            "account_name": "test",
            "total_equity": 500000,
            "cash_balance": 200000,
            "positions": [],
            "recent_trades": [],
        }
        mock_llm.return_value = '{"trades": [{"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1850.0, "reason": "test"}], "portfolio_summary": "test"}'

        svc = self._make_service(approval_required=False)
        # Mock validate_and_execute 以避免真实交易
        from src.services.sim_trading_service import SimTradingResult
        svc.validate_and_execute = MagicMock(return_value=SimTradingResult(
            account_id=self.account_id,
            executed_trades=[{"stock_code": "600519"}],
            skipped_trades=[],
            errors=[],
        ))

        fake_result = MagicMock()
        fake_result.code = "600519"
        fake_result.name = "贵州茅台"
        fake_result.sentiment_score = 80
        fake_result.decision_type = "buy"
        fake_result.operation_advice = "建议买入"
        fake_result.analysis_summary = "测试"
        fake_result.current_price = 1850.0

        result = svc.run([fake_result], is_scheduled=True)

        self.assertEqual(result["status"], "completed")
        svc.validate_and_execute.assert_called_once()

    @patch("src.services.sim_trading_service.SimTradingService.call_llm")
    @patch("src.services.sim_trading_service.SimTradingService.build_portfolio_context")
    @patch("src.services.sim_trading_service.SimTradingService._has_executed_today")
    def test_run_approval_on_not_scheduled_executes_directly(self, mock_has_exec, mock_ctx, mock_llm):
        """审批开启但 is_scheduled=False 时走原始路径（手动触发不走审批）。"""
        mock_has_exec.return_value = False
        mock_ctx.return_value = {
            "account_id": self.account_id,
            "account_name": "test",
            "total_equity": 500000,
            "cash_balance": 200000,
            "positions": [],
            "recent_trades": [],
        }
        mock_llm.return_value = '{"trades": [{"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1850.0, "reason": "test"}], "portfolio_summary": "test"}'

        svc = self._make_service(approval_required=True)
        from src.services.sim_trading_service import SimTradingResult
        svc.validate_and_execute = MagicMock(return_value=SimTradingResult(
            account_id=self.account_id,
            executed_trades=[{"stock_code": "600519"}],
            skipped_trades=[],
            errors=[],
        ))

        fake_result = MagicMock()
        fake_result.code = "600519"
        fake_result.name = "贵州茅台"
        fake_result.sentiment_score = 80
        fake_result.decision_type = "buy"
        fake_result.operation_advice = "建议买入"
        fake_result.analysis_summary = "测试"
        fake_result.current_price = 1850.0

        result = svc.run([fake_result], is_scheduled=False)

        self.assertEqual(result["status"], "completed")
        svc.validate_and_execute.assert_called_once()


class TestHasExecutedTodayWithPending(unittest.TestCase):
    """测试 _has_executed_today() 同时检查 pending 表。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = self.db_path
        _reset_singletons()

        from src.storage import DatabaseManager
        self.db = DatabaseManager.get_instance()

        from src.repositories.portfolio_repo import PortfolioRepository
        self.repo = PortfolioRepository(self.db)

        # 创建测试账户
        with self.db.get_session() as session:
            from src.storage import PortfolioAccount
            acct = PortfolioAccount(
                name="test_account", broker="test",
                market="cn", base_currency="CNY",
            )
            session.add(acct)
            session.commit()
            self.account_id = acct.id

    def tearDown(self):
        import shutil
        _reset_singletons()
        os.environ.pop("DATABASE_PATH", None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_has_executed_today_detects_pending(self):
        """审批模式下，pending 表有记录时返回 True。"""
        from src.services.sim_trading_service import SimTradingService
        from src.services.portfolio_service import PortfolioService

        mock_ps = MagicMock(spec=PortfolioService)
        mock_ps.repo = self.repo
        # query_trades 返回空（无已执行的 trade）
        mock_ps.repo.query_trades = MagicMock(return_value=([], 0))

        svc = SimTradingService(portfolio_service=mock_ps)
        svc.config = MagicMock()
        svc.config.sim_trading_approval_required = True
        svc.repo = self.repo

        # 添加一条 pending 记录
        self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )

        result = svc._has_executed_today(self.account_id)
        self.assertTrue(result)

    def test_has_executed_today_no_pending_no_trade(self):
        """审批模式下，无 pending 无 trade 时返回 False。"""
        from src.services.sim_trading_service import SimTradingService
        from src.services.portfolio_service import PortfolioService

        mock_ps = MagicMock(spec=PortfolioService)
        mock_ps.repo = self.repo
        mock_ps.repo.query_trades = MagicMock(return_value=([], 0))

        svc = SimTradingService(portfolio_service=mock_ps)
        svc.config = MagicMock()
        svc.config.sim_trading_approval_required = False
        svc.repo = self.repo

        result = svc._has_executed_today(self.account_id)
        self.assertFalse(result)

    def test_has_executed_today_approval_off_ignores_pending(self):
        """审批关闭时，不检查 pending 表。"""
        from src.services.sim_trading_service import SimTradingService
        from src.services.portfolio_service import PortfolioService

        mock_ps = MagicMock(spec=PortfolioService)
        mock_ps.repo = self.repo
        mock_ps.repo.query_trades = MagicMock(return_value=([], 0))

        svc = SimTradingService(portfolio_service=mock_ps)
        svc.config = MagicMock()
        svc.config.sim_trading_approval_required = False
        svc.repo = self.repo

        # 添加 pending 记录
        self.repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519",
            side="buy", quantity=100, price=1850.0,
        )

        result = svc._has_executed_today(self.account_id)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
