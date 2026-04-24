# -*- coding: utf-8 -*-
"""validate_and_execute() 单元测试"""

from __future__ import annotations

import sys
import types
import pytest
from datetime import date
from unittest.mock import MagicMock, patch


# 注入 stub 以避免顶层 import 失败
for _mod_name in ("litellm", "json_repair"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
_jr = sys.modules["json_repair"]
if not hasattr(_jr, "repair_json"):
    _jr.repair_json = lambda x, **kw: x  # type: ignore[attr-defined]

from src.services.sim_trading_service import SimTradeAction, SimTradingResult


@pytest.fixture()
def mock_portfolio_service():
    return MagicMock()


@pytest.fixture()
def service(mock_portfolio_service):
    with patch("src.services.sim_trading_service.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.sim_trading_max_single_amount = 100000.0
        cfg.sim_trading_default_commission = 5.0
        mock_cfg.return_value = cfg
        from src.services.sim_trading_service import SimTradingService

        svc = SimTradingService(portfolio_service=mock_portfolio_service)
    return svc


def _make_ctx(cash=500000.0, positions=None):
    """构造 portfolio_ctx"""
    return {
        "account_id": 1,
        "cash_balance": cash,
        "positions": positions or [],
    }


# ------------------------------------------------------------------
# 买入场景
# ------------------------------------------------------------------
class TestBuyValidation:
    """买入校验"""

    def test_buy_success(self, service, mock_portfolio_service):
        """正常买入 → executed_trades 包含该笔"""
        mock_portfolio_service.record_trade.return_value = {"id": 101}
        actions = [SimTradeAction(stock_code="600519", action="buy", quantity=100, price=800.0, reason="看好")]
        ctx = _make_ctx(cash=200000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert isinstance(result, SimTradingResult)
        assert len(result.executed_trades) == 1
        assert len(result.skipped_trades) == 0
        assert result.total_buy_amount == 80000.0

        # 验证 record_trade 调用参数
        call_kwargs = mock_portfolio_service.record_trade.call_args.kwargs
        assert call_kwargs["symbol"] == "600519"
        assert call_kwargs["side"] == "buy"
        assert call_kwargs["quantity"] == 100
        assert call_kwargs["price"] == 800.0
        assert "[sim-trading]" in call_kwargs["note"]

    def test_buy_insufficient_cash(self, service, mock_portfolio_service):
        """可用现金不足 → skipped"""
        actions = [SimTradeAction(stock_code="600519", action="buy", quantity=100, price=1800.0, reason="看好")]
        ctx = _make_ctx(cash=100000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.skipped_trades) == 1
        assert len(result.executed_trades) == 0
        assert "现金不足" in result.skipped_trades[0]["reason"] or "cash" in result.skipped_trades[0]["reason"].lower()
        mock_portfolio_service.record_trade.assert_not_called()

    def test_buy_exceeds_max_single_amount(self, service, mock_portfolio_service):
        """单笔金额超限 → skipped"""
        actions = [SimTradeAction(stock_code="600519", action="buy", quantity=100, price=1200.0, reason="看好")]
        ctx = _make_ctx(cash=500000.0)
        # max_single_amount = 100000, 100*1200 = 120000 > 100000
        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.skipped_trades) == 1
        assert len(result.executed_trades) == 0

    def test_buy_a_share_lot_not_multiple_of_100(self, service, mock_portfolio_service):
        """A 股买入数量非 100 整数倍 → skipped"""
        actions = [SimTradeAction(stock_code="600519", action="buy", quantity=150, price=100.0, reason="看好")]
        ctx = _make_ctx(cash=500000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.skipped_trades) == 1

    def test_buy_hk_share_no_lot_check(self, service, mock_portfolio_service):
        """港股不需要 100 整数倍限制"""
        mock_portfolio_service.record_trade.return_value = {"id": 102}
        actions = [SimTradeAction(stock_code="hk00700", action="buy", quantity=50, price=350.0, reason="看好")]
        ctx = _make_ctx(cash=500000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.executed_trades) == 1

    def test_buy_us_share_no_lot_check(self, service, mock_portfolio_service):
        """美股不需要 100 整数倍限制"""
        mock_portfolio_service.record_trade.return_value = {"id": 103}
        actions = [SimTradeAction(stock_code="AAPL", action="buy", quantity=5, price=180.0, reason="看好")]
        ctx = _make_ctx(cash=500000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.executed_trades) == 1


# ------------------------------------------------------------------
# 卖出场景
# ------------------------------------------------------------------
class TestSellValidation:
    """卖出校验"""

    def test_sell_success(self, service, mock_portfolio_service):
        """正常卖出"""
        mock_portfolio_service.record_trade.return_value = {"id": 201}
        actions = [SimTradeAction(stock_code="600519", action="sell", quantity=100, price=1900.0, reason="获利了结")]
        ctx = _make_ctx(positions=[{"stock_code": "600519", "quantity": 200}])

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.executed_trades) == 1
        assert result.total_sell_amount == 190000.0

    def test_sell_exceeds_position(self, service, mock_portfolio_service):
        """卖出数量超过持仓 → skipped"""
        actions = [SimTradeAction(stock_code="600519", action="sell", quantity=300, price=1900.0, reason="止损")]
        ctx = _make_ctx(positions=[{"stock_code": "600519", "quantity": 200}])

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.skipped_trades) == 1
        mock_portfolio_service.record_trade.assert_not_called()

    def test_sell_no_position(self, service, mock_portfolio_service):
        """无持仓卖出 → skipped"""
        actions = [SimTradeAction(stock_code="600519", action="sell", quantity=100, price=1900.0, reason="止损")]
        ctx = _make_ctx(positions=[])

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.skipped_trades) == 1

    def test_sell_a_share_lot_not_multiple_of_100(self, service, mock_portfolio_service):
        """A 股卖出数量非 100 整数倍 → skipped"""
        actions = [SimTradeAction(stock_code="600519", action="sell", quantity=150, price=1900.0, reason="部分卖出")]
        ctx = _make_ctx(positions=[{"stock_code": "600519", "quantity": 500}])

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.skipped_trades) == 1


# ------------------------------------------------------------------
# 执行异常
# ------------------------------------------------------------------
class TestExecutionErrors:
    """record_trade 抛异常时的容错"""

    def test_record_trade_exception_captured(self, service, mock_portfolio_service):
        """record_trade 抛异常 → 加入 errors，继续处理下一笔"""
        mock_portfolio_service.record_trade.side_effect = [
            Exception("DB error"),
            {"id": 302},
        ]
        actions = [
            SimTradeAction(stock_code="600519", action="buy", quantity=100, price=500.0, reason="第一笔"),
            SimTradeAction(stock_code="000001", action="buy", quantity=100, price=10.0, reason="第二笔"),
        ]
        ctx = _make_ctx(cash=500000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.errors) == 1
        assert len(result.executed_trades) == 1
        assert "DB error" in result.errors[0]


# ------------------------------------------------------------------
# 多笔交易 + 现金追踪
# ------------------------------------------------------------------
class TestMultipleTradesAndCashTracking:
    """多笔交易时现金余额递减"""

    def test_cash_deducted_after_buy(self, service, mock_portfolio_service):
        """两笔买入，第二笔因现金不足被跳过"""
        mock_portfolio_service.record_trade.return_value = {"id": 401}
        actions = [
            SimTradeAction(stock_code="600519", action="buy", quantity=100, price=800.0, reason="第一笔"),
            SimTradeAction(stock_code="000001", action="buy", quantity=100, price=300.0, reason="第二笔"),
        ]
        # 可用现金 90000；第一笔 80000 ok → 剩 10000；第二笔 30000 > 10000 → skip
        ctx = _make_ctx(cash=90000.0)

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.executed_trades) == 1
        assert len(result.skipped_trades) == 1
        assert result.total_buy_amount == 80000.0

    def test_sell_adds_cash(self, service, mock_portfolio_service):
        """卖出后现金增加，可用于后续买入"""
        mock_portfolio_service.record_trade.return_value = {"id": 501}
        actions = [
            SimTradeAction(stock_code="600519", action="sell", quantity=100, price=1000.0, reason="先卖"),
            SimTradeAction(stock_code="000001", action="buy", quantity=100, price=800.0, reason="再买"),
        ]
        # 初始现金 10000，卖出获得 100000 → 110000；买入 80000 → 剩 30000
        ctx = _make_ctx(
            cash=10000.0,
            positions=[{"stock_code": "600519", "quantity": 200}],
        )

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.executed_trades) == 2
        assert result.total_sell_amount == 100000.0
        assert result.total_buy_amount == 80000.0

    def test_sell_updates_position_tracking(self, service, mock_portfolio_service):
        """卖出后持仓减少，影响后续卖出校验"""
        mock_portfolio_service.record_trade.return_value = {"id": 601}
        actions = [
            SimTradeAction(stock_code="600519", action="sell", quantity=100, price=1000.0, reason="第一笔"),
            SimTradeAction(stock_code="600519", action="sell", quantity=200, price=1000.0, reason="第二笔"),
        ]
        # 持仓 200 股，第一笔卖 100 → 剩 100；第二笔卖 200 > 100 → skip
        ctx = _make_ctx(positions=[{"stock_code": "600519", "quantity": 200}])

        result = service.validate_and_execute(actions, account_id=1, portfolio_ctx=ctx)

        assert len(result.executed_trades) == 1
        assert len(result.skipped_trades) == 1


# ------------------------------------------------------------------
# _is_a_share 辅助方法
# ------------------------------------------------------------------
class TestIsAShare:
    """A 股代码判断"""

    def test_a_share_codes(self, service):
        assert service._is_a_share("600519") is True
        assert service._is_a_share("000001") is True
        assert service._is_a_share("300001") is True

    def test_hk_codes(self, service):
        assert service._is_a_share("hk00700") is False

    def test_us_codes(self, service):
        assert service._is_a_share("AAPL") is False

    def test_edge_cases(self, service):
        assert service._is_a_share("") is False
        assert service._is_a_share("12345") is False
        assert service._is_a_share("1234567") is False


# ------------------------------------------------------------------
# 边界场景
# ------------------------------------------------------------------
class TestEdgeCases:
    """边界与空输入"""

    def test_empty_actions(self, service, mock_portfolio_service):
        """空动作列表 → 空结果"""
        ctx = _make_ctx()
        result = service.validate_and_execute([], account_id=1, portfolio_ctx=ctx)

        assert isinstance(result, SimTradingResult)
        assert result.executed_trades == []
        assert result.skipped_trades == []
        assert result.total_buy_amount == 0.0
        assert result.total_sell_amount == 0.0

    def test_result_account_id(self, service, mock_portfolio_service):
        """结果包含正确的 account_id"""
        ctx = _make_ctx()
        result = service.validate_and_execute([], account_id=42, portfolio_ctx=ctx)
        assert result.account_id == 42
