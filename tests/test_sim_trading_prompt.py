# -*- coding: utf-8 -*-
"""
build_llm_prompt() 单元测试

覆盖：正常路径、空结果、缺失字段降级、A股代码判别、输出格式要求
"""

import unittest
from unittest.mock import MagicMock, patch

from src.services.sim_trading_service import SimTradingService


def _make_result(
    code="600519",
    name="贵州茅台",
    sentiment_score=75,
    decision_type="buy",
    operation_advice="建议买入",
    current_price=1800.0,
    analysis_summary="基本面良好，技术面向好" * 20,
    dashboard=None,
):
    """构造伪 AnalysisResult 对象（用 MagicMock 模拟属性访问）"""
    r = MagicMock()
    r.code = code
    r.name = name
    r.sentiment_score = sentiment_score
    r.decision_type = decision_type
    r.operation_advice = operation_advice
    r.current_price = current_price
    r.analysis_summary = analysis_summary
    r.dashboard = dashboard
    return r


def _base_ctx():
    """构造基本 portfolio_ctx（字段名与 build_portfolio_context() 实际输出一致）"""
    return {
        "total_equity": 500000.0,
        "cash_balance": 200000.0,
        "positions": [
            {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "quantity": 1000,
                "avg_cost": 12.0,
                "current_price": 13.0,
                "market_value": 13000.0,
                "unrealized_pnl": 1000.0,
            }
        ],
        "recent_trades": [
            {"date": "2026-04-20", "stock_code": "000001", "action": "buy", "quantity": 500, "price": 12.5}
        ],
    }


class TestBuildLlmPromptEmpty(unittest.TestCase):
    """results 为空时返回空字符串"""

    @patch("src.services.sim_trading_service.get_config")
    def test_empty_results_returns_empty(self, mock_cfg):
        mock_cfg.return_value = MagicMock(sim_trading_max_single_amount=100000.0)
        svc = SimTradingService(portfolio_service=MagicMock())
        prompt = svc.build_llm_prompt([], _base_ctx())
        self.assertEqual(prompt, "")

    @patch("src.services.sim_trading_service.get_config")
    def test_none_results_returns_empty(self, mock_cfg):
        mock_cfg.return_value = MagicMock(sim_trading_max_single_amount=100000.0)
        svc = SimTradingService(portfolio_service=MagicMock())
        prompt = svc.build_llm_prompt(None, _base_ctx())
        self.assertEqual(prompt, "")


class TestBuildLlmPromptStructure(unittest.TestCase):
    """验证 prompt 包含所有要求的段落"""

    @patch("src.services.sim_trading_service.get_config")
    def setUp(self, mock_cfg):
        mock_cfg.return_value = MagicMock(sim_trading_max_single_amount=100000.0)
        self.svc = SimTradingService(portfolio_service=MagicMock())
        results = [_make_result(), _make_result(code="000001", name="平安银行", decision_type="sell", sentiment_score=30)]
        self.prompt = self.svc.build_llm_prompt(results, _base_ctx())

    def test_contains_system_role(self):
        self.assertIn("投资组合管理", self.prompt)

    def test_contains_portfolio_snapshot(self):
        self.assertIn("总资产", self.prompt)
        self.assertIn("可用现金", self.prompt)
        self.assertIn("500000", self.prompt)
        self.assertIn("200000", self.prompt)

    def test_contains_positions_table(self):
        self.assertIn("平安银行", self.prompt)
        self.assertIn("000001", self.prompt)

    def test_contains_recent_trades(self):
        self.assertIn("2026-04-20", self.prompt)

    def test_contains_analysis_results(self):
        self.assertIn("600519", self.prompt)
        self.assertIn("贵州茅台", self.prompt)
        self.assertIn("75", self.prompt)
        self.assertIn("buy", self.prompt)

    def test_contains_constraints(self):
        self.assertIn("100000", self.prompt)
        self.assertIn("100", self.prompt)

    def test_contains_json_output_format(self):
        self.assertIn("trades", self.prompt)
        self.assertIn("stock_code", self.prompt)
        self.assertIn("action", self.prompt)
        self.assertIn("quantity", self.prompt)
        self.assertIn("portfolio_summary", self.prompt)

    def test_analysis_summary_truncated(self):
        """核心结论应截取前 200 字"""
        r = _make_result(analysis_summary="X" * 500)
        results = [r]
        prompt = self.svc.build_llm_prompt(results, _base_ctx())
        # 原文 500 字不应完整出现；截断后最多 200 字
        full_text = "X" * 500
        self.assertNotIn(full_text, prompt)


class TestBuildLlmPromptDefaults(unittest.TestCase):
    """portfolio_ctx 缺失字段时使用默认值"""

    @patch("src.services.sim_trading_service.get_config")
    def test_missing_fields_uses_defaults(self, mock_cfg):
        mock_cfg.return_value = MagicMock(sim_trading_max_single_amount=50000.0)
        svc = SimTradingService(portfolio_service=MagicMock())
        # 传入空字典 — 不含任何字段
        prompt = svc.build_llm_prompt([_make_result()], {})
        # 仍应能生成有效 prompt（不报错）
        self.assertIn("投资组合管理", prompt)
        # 默认值应出现
        self.assertIn("0", prompt)  # 总资产 / 现金 默认 0

    @patch("src.services.sim_trading_service.get_config")
    def test_max_single_amount_from_config(self, mock_cfg):
        mock_cfg.return_value = MagicMock(sim_trading_max_single_amount=88888.0)
        svc = SimTradingService(portfolio_service=MagicMock())
        prompt = svc.build_llm_prompt([_make_result()], _base_ctx())
        self.assertIn("88888", prompt)


class TestBuildLlmPromptResultAttributes(unittest.TestCase):
    """AnalysisResult 属性缺失时优雅降级"""

    @patch("src.services.sim_trading_service.get_config")
    def test_result_missing_attributes(self, mock_cfg):
        mock_cfg.return_value = MagicMock(sim_trading_max_single_amount=100000.0)
        svc = SimTradingService(portfolio_service=MagicMock())
        # 用普通对象，只有 code
        r = MagicMock(spec=[])
        r.code = "600519"
        # 让其他属性访问抛 AttributeError
        prompt = svc.build_llm_prompt([r], _base_ctx())
        self.assertIn("600519", prompt)
        self.assertIn("投资组合管理", prompt)


if __name__ == "__main__":
    unittest.main()
