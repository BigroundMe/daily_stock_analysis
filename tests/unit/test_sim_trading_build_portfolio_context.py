# -*- coding: utf-8 -*-
"""build_portfolio_context() 单元测试"""

from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


@pytest.fixture()
def mock_portfolio_service():
    return MagicMock()


@pytest.fixture()
def service(mock_portfolio_service):
    with patch("src.services.sim_trading_service.get_config") as mock_cfg:
        cfg = MagicMock()
        mock_cfg.return_value = cfg
        from src.services.sim_trading_service import SimTradingService

        svc = SimTradingService(portfolio_service=mock_portfolio_service)
    return svc


class TestBuildPortfolioContextHappyPath:
    """正常路径：快照 + 交易记录均返回有效数据"""

    def test_returns_correct_structure(self, service, mock_portfolio_service):
        """返回的 Dict 包含所有必要字段"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "测试账户",
                    "total_cash": 100000.0,
                    "total_equity": 150000.0,
                    "positions": [
                        {
                            "symbol": "600519",
                            "quantity": 100,
                            "avg_cost": 1800.0,
                            "last_price": 1900.0,
                            "market_value_base": 190000.0,
                            "unrealized_pnl_base": 10000.0,
                            "total_cost": 180000.0,
                        }
                    ],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

        result = service.build_portfolio_context(1)

        assert result["account_id"] == 1
        assert result["account_name"] == "测试账户"
        assert result["total_equity"] == 150000.0
        assert result["cash_balance"] == 100000.0
        assert isinstance(result["positions"], list)
        assert isinstance(result["recent_trades"], list)

    def test_positions_mapped_correctly(self, service, mock_portfolio_service):
        """持仓字段正确映射"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "A",
                    "total_cash": 50000.0,
                    "total_equity": 250000.0,
                    "positions": [
                        {
                            "symbol": "600519",
                            "quantity": 200,
                            "avg_cost": 1800.0,
                            "last_price": 1900.0,
                            "market_value_base": 380000.0,
                            "unrealized_pnl_base": 20000.0,
                            "total_cost": 360000.0,
                        }
                    ],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

        result = service.build_portfolio_context(1)
        pos = result["positions"][0]

        assert pos["stock_code"] == "600519"
        assert pos["quantity"] == 200
        assert pos["avg_cost"] == 1800.0
        assert pos["current_price"] == 1900.0
        assert pos["market_value"] == 380000.0
        assert pos["unrealized_pnl"] == 20000.0

    def test_unrealized_pnl_pct_calculated(self, service, mock_portfolio_service):
        """unrealized_pnl_pct 基于 total_cost 计算"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "A",
                    "total_cash": 0.0,
                    "total_equity": 110000.0,
                    "positions": [
                        {
                            "symbol": "000001",
                            "quantity": 1000,
                            "avg_cost": 10.0,
                            "last_price": 11.0,
                            "market_value_base": 11000.0,
                            "unrealized_pnl_base": 1000.0,
                            "total_cost": 10000.0,
                        }
                    ],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

        result = service.build_portfolio_context(1)
        pos = result["positions"][0]
        # pnl_pct = 1000 / 10000 * 100 = 10.0
        assert abs(pos["unrealized_pnl_pct"] - 10.0) < 0.01

    def test_recent_trades_mapped(self, service, mock_portfolio_service):
        """近期交易记录正确映射"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "A",
                    "total_cash": 100000.0,
                    "total_equity": 100000.0,
                    "positions": [],
                }
            ],
        }
        trade_row = MagicMock()
        trade_row.trade_date = date(2026, 4, 20)
        trade_row.symbol = "600519"
        trade_row.side = "buy"
        trade_row.quantity = 100
        trade_row.price = 1800.0
        mock_portfolio_service.repo.query_trades.return_value = ([trade_row], 1)

        result = service.build_portfolio_context(1)
        assert len(result["recent_trades"]) == 1
        t = result["recent_trades"][0]
        assert t["trade_date"] == "2026-04-20"
        assert t["stock_code"] == "600519"
        assert t["action"] == "buy"
        assert t["quantity"] == 100
        assert t["price"] == 1800.0
        assert t["amount"] == 180000.0

    def test_query_trades_called_with_7day_window(self, service, mock_portfolio_service):
        """query_trades 应使用近 7 天日期范围"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "A",
                    "total_cash": 0.0,
                    "total_equity": 0.0,
                    "positions": [],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

        service.build_portfolio_context(1)

        call_kwargs = mock_portfolio_service.repo.query_trades.call_args
        assert call_kwargs is not None
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        # 确认 account_id 和日期范围
        assert kwargs.get("account_id") == 1
        assert kwargs.get("date_from") == date.today() - timedelta(days=7)
        assert kwargs.get("date_to") == date.today()


class TestBuildPortfolioContextErrorHandling:
    """容错处理"""

    def test_snapshot_exception_returns_empty_structure(self, service, mock_portfolio_service):
        """get_portfolio_snapshot 异常时返回空结构"""
        mock_portfolio_service.get_portfolio_snapshot.side_effect = Exception("DB error")

        result = service.build_portfolio_context(1)

        assert result["account_id"] == 1
        assert result["cash_balance"] == 0.0
        assert result["total_equity"] == 0.0
        assert result["positions"] == []
        assert result["recent_trades"] == []

    def test_snapshot_missing_accounts_key(self, service, mock_portfolio_service):
        """快照缺少 accounts 键时返回空结构"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {}

        result = service.build_portfolio_context(1)

        assert result["cash_balance"] == 0.0
        assert result["positions"] == []

    def test_snapshot_empty_accounts(self, service, mock_portfolio_service):
        """快照 accounts 为空列表时返回空结构"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {"accounts": []}

        result = service.build_portfolio_context(1)

        assert result["cash_balance"] == 0.0
        assert result["positions"] == []

    def test_query_trades_exception_returns_empty_trades(self, service, mock_portfolio_service):
        """query_trades 异常时 recent_trades 为空，但持仓数据正常"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "测试",
                    "total_cash": 50000.0,
                    "total_equity": 50000.0,
                    "positions": [],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.side_effect = Exception("query failed")

        result = service.build_portfolio_context(1)

        assert result["cash_balance"] == 50000.0
        assert result["recent_trades"] == []

    def test_position_missing_fields_uses_defaults(self, service, mock_portfolio_service):
        """持仓字段缺失时使用安全默认值"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "A",
                    "total_cash": 0.0,
                    "total_equity": 0.0,
                    "positions": [
                        {
                            "symbol": "000001",
                            # 缺少其他字段
                        }
                    ],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

        result = service.build_portfolio_context(1)
        pos = result["positions"][0]
        assert pos["stock_code"] == "000001"
        assert pos["quantity"] == 0
        assert pos["avg_cost"] == 0.0
        assert pos["current_price"] == 0.0
        assert pos["market_value"] == 0.0
        assert pos["unrealized_pnl"] == 0.0
        assert pos["unrealized_pnl_pct"] == 0.0

    def test_zero_total_cost_pnl_pct_is_zero(self, service, mock_portfolio_service):
        """total_cost 为 0 时 unrealized_pnl_pct 应为 0（避免除零）"""
        mock_portfolio_service.get_portfolio_snapshot.return_value = {
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "A",
                    "total_cash": 0.0,
                    "total_equity": 0.0,
                    "positions": [
                        {
                            "symbol": "000001",
                            "quantity": 100,
                            "avg_cost": 0.0,
                            "last_price": 10.0,
                            "market_value_base": 1000.0,
                            "unrealized_pnl_base": 1000.0,
                            "total_cost": 0.0,
                        }
                    ],
                }
            ],
        }
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

        result = service.build_portfolio_context(1)
        assert result["positions"][0]["unrealized_pnl_pct"] == 0.0
