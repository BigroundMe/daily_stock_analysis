# -*- coding: utf-8 -*-
"""run() 主编排和 _has_executed_today() 幂等检查 单元测试"""

from __future__ import annotations

import sys
import types
import pytest
from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

# 注入 stub 以避免顶层 import 失败
for _mod_name in ("litellm", "json_repair"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = types.ModuleType(_mod_name)
_jr = sys.modules["json_repair"]
if not hasattr(_jr, "repair_json"):
    _jr.repair_json = lambda x, **kw: x  # type: ignore[attr-defined]

from src.services.sim_trading_service import SimTradeAction, SimTradingResult, SimTradingService


@pytest.fixture()
def mock_portfolio_service():
    return MagicMock()


@pytest.fixture()
def service(mock_portfolio_service):
    with patch("src.services.sim_trading_service.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.sim_trading_enabled = True
        cfg.sim_trading_account_id = 1
        cfg.sim_trading_max_single_amount = 100000.0
        cfg.sim_trading_approval_required = False
        cfg.litellm_model = "openai/gpt-4o-mini"
        cfg.llm_temperature = 0.7
        mock_cfg.return_value = cfg
        svc = SimTradingService(portfolio_service=mock_portfolio_service)
    return svc


# ==================================================================
# _has_executed_today() 测试
# ==================================================================


class TestHasExecutedToday:
    """幂等检查：查询今日是否已有 [sim-trading] 标记的交易记录"""

    def test_no_records_returns_false(self, service, mock_portfolio_service):
        """今日无 sim-trading 记录 → False"""
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)
        assert service._has_executed_today(1) is False

    def test_has_sim_trading_records_returns_true(self, service, mock_portfolio_service):
        """今日有 [sim-trading] 标记的记录 → True"""
        trade = MagicMock()
        trade.note = "[sim-trading] 看好"
        mock_portfolio_service.repo.query_trades.return_value = ([trade], 1)
        assert service._has_executed_today(1) is True

    def test_has_non_sim_records_returns_false(self, service, mock_portfolio_service):
        """今日有记录但不含 [sim-trading] 标记 → False"""
        trade = MagicMock()
        trade.note = "手动交易"
        mock_portfolio_service.repo.query_trades.return_value = ([trade], 1)
        assert service._has_executed_today(1) is False

    def test_none_note_returns_false(self, service, mock_portfolio_service):
        """今日有记录但 note 为 None → False"""
        trade = MagicMock()
        trade.note = None
        mock_portfolio_service.repo.query_trades.return_value = ([trade], 1)
        assert service._has_executed_today(1) is False

    def test_query_exception_returns_true(self, service, mock_portfolio_service):
        """查询异常 → 返回 True（安全方向：跳过执行）"""
        mock_portfolio_service.repo.query_trades.side_effect = Exception("db error")
        assert service._has_executed_today(1) is True

    def test_query_uses_today_date(self, service, mock_portfolio_service):
        """确认查询使用 date.today()"""
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)
        today = date.today()
        service._has_executed_today(1)
        mock_portfolio_service.repo.query_trades.assert_called_once()
        call_kwargs = mock_portfolio_service.repo.query_trades.call_args[1]
        assert call_kwargs["account_id"] == 1
        assert call_kwargs["date_from"] == today
        assert call_kwargs["date_to"] == today


# ==================================================================
# run() 前置检查测试
# ==================================================================


class TestRunPreconditions:
    """run() 各种前置检查分支"""

    def test_disabled(self, service):
        """sim_trading_enabled=False → disabled"""
        service.config.sim_trading_enabled = False
        result = service.run([MagicMock()])
        assert result["status"] == "disabled"

    def test_no_results(self, service):
        """空分析结果 → no_results"""
        result = service.run([])
        assert result["status"] == "no_results"

    def test_none_results(self, service):
        """None 分析结果 → no_results"""
        result = service.run(None)
        assert result["status"] == "no_results"

    def test_no_account_id(self, service):
        """account_id 未配置 → no_account"""
        service.config.sim_trading_account_id = None
        result = service.run([MagicMock()])
        assert result["status"] == "no_account"

    def test_already_executed(self, service, mock_portfolio_service):
        """今日已执行 → already_executed"""
        trade = MagicMock()
        trade.note = "[sim-trading] 之前执行的"
        mock_portfolio_service.repo.query_trades.return_value = ([trade], 1)
        result = service.run([MagicMock()])
        assert result["status"] == "already_executed"


# ==================================================================
# run() 编排流程测试
# ==================================================================


class TestRunOrchestration:
    """run() 完整编排流程的各个分支"""

    def _setup_no_idempotent_hit(self, mock_portfolio_service):
        """设置幂等检查通过（无今日记录）"""
        mock_portfolio_service.repo.query_trades.return_value = ([], 0)

    def test_no_prompt(self, service, mock_portfolio_service):
        """build_llm_prompt 返回空 → no_prompt"""
        self._setup_no_idempotent_hit(mock_portfolio_service)
        with patch.object(service, "build_portfolio_context", return_value={}), \
             patch.object(service, "build_llm_prompt", return_value=""):
            result = service.run([MagicMock()])
        assert result["status"] == "no_prompt"

    def test_llm_failed(self, service, mock_portfolio_service):
        """call_llm 返回空 → llm_failed"""
        self._setup_no_idempotent_hit(mock_portfolio_service)
        with patch.object(service, "build_portfolio_context", return_value={}), \
             patch.object(service, "build_llm_prompt", return_value="有效 prompt"), \
             patch.object(service, "call_llm", return_value=""):
            result = service.run([MagicMock()])
        assert result["status"] == "llm_failed"

    def test_no_actions(self, service, mock_portfolio_service):
        """parse_llm_response 返回空列表 → no_actions"""
        self._setup_no_idempotent_hit(mock_portfolio_service)
        with patch.object(service, "build_portfolio_context", return_value={}), \
             patch.object(service, "build_llm_prompt", return_value="有效 prompt"), \
             patch.object(service, "call_llm", return_value='{"trades": []}'), \
             patch.object(service, "parse_llm_response", return_value=[]):
            result = service.run([MagicMock()])
        assert result["status"] == "no_actions"
        assert "llm_response" in result

    def test_completed_success(self, service, mock_portfolio_service):
        """完整流程 → completed + 结果统计"""
        self._setup_no_idempotent_hit(mock_portfolio_service)
        actions = [
            SimTradeAction(stock_code="600519", action="buy", quantity=100, price=800.0, reason="看好"),
        ]
        exec_result = SimTradingResult(
            account_id=1,
            executed_trades=[{"stock_code": "600519", "action": "buy", "quantity": 100}],
            skipped_trades=[],
            errors=[],
            total_buy_amount=80000.0,
            total_sell_amount=0.0,
        )
        with patch.object(service, "build_portfolio_context", return_value={"cash_balance": 200000}) as mock_bpc, \
             patch.object(service, "build_llm_prompt", return_value="有效 prompt") as mock_blp, \
             patch.object(service, "call_llm", return_value='{"trades": [...]}') as mock_llm, \
             patch.object(service, "parse_llm_response", return_value=actions) as mock_parse, \
             patch.object(service, "validate_and_execute", return_value=exec_result) as mock_exec:
            result = service.run([MagicMock()])

        assert result["status"] == "completed"
        assert result["actions_count"] == 1
        assert result["executed_count"] == 1
        assert result["skipped_count"] == 0
        assert result["error_count"] == 0
        assert result["result"] is exec_result

        # 验证 validate_and_execute 被调用
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args[0][0] == actions
        assert call_args[0][1] == 1  # account_id

    def test_build_portfolio_context_called_with_account_id(self, service, mock_portfolio_service):
        """build_portfolio_context 使用正确的 account_id"""
        self._setup_no_idempotent_hit(mock_portfolio_service)
        service.config.sim_trading_account_id = 42
        with patch.object(service, "build_portfolio_context", return_value={}) as mock_bpc, \
             patch.object(service, "build_llm_prompt", return_value=""):
            service.run([MagicMock()])
        mock_bpc.assert_called_once_with(42)
