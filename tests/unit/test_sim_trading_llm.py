# -*- coding: utf-8 -*-
"""
call_llm() 和 parse_llm_response() 单元测试

覆盖：
- call_llm: 正常调用、异常处理、usage 记录、模型选择
- parse_llm_response: 正常 JSON、空响应、markdown 包裹、无效字段跳过、json_repair 降级
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch, ANY

from src.services.sim_trading_service import SimTradeAction, SimTradingService


@pytest.fixture()
def service():
    with patch("src.services.sim_trading_service.get_config") as mock_cfg:
        cfg = MagicMock()
        cfg.litellm_model = "openai/gpt-4o-mini"
        cfg.litellm_fallback_models = []
        cfg.llm_model_list = []
        cfg.llm_temperature = 0.7
        mock_cfg.return_value = cfg
        svc = SimTradingService(portfolio_service=MagicMock())
    return svc


# ─────────────────────────────────────────────
# call_llm() 测试
# ─────────────────────────────────────────────


class TestCallLlmHappyPath:
    """正常调用 LiteLLM 返回内容"""

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_returns_response_content(self, mock_litellm, mock_persist, service):
        """正常调用返回 LLM 响应文本"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"trades": []}'
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        mock_litellm.completion.return_value = mock_response

        result = service.call_llm("测试 prompt")

        assert result == '{"trades": []}'

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_uses_json_response_format(self, mock_litellm, mock_persist, service):
        """请求包含 response_format=json_object"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_litellm.completion.return_value = mock_response

        service.call_llm("test")

        call_kwargs = mock_litellm.completion.call_args
        assert call_kwargs.kwargs.get("response_format") == {"type": "json_object"} or (
            call_kwargs[1].get("response_format") == {"type": "json_object"}
        )

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_uses_system_and_user_messages(self, mock_litellm, mock_persist, service):
        """消息列表包含 system + user 两条"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_litellm.completion.return_value = mock_response

        service.call_llm("用户 prompt")

        call_kwargs = mock_litellm.completion.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs[1].get("messages")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "用户 prompt"

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_persists_llm_usage(self, mock_litellm, mock_persist, service):
        """成功调用后记录 LLM 使用量"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "{}"
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        mock_litellm.completion.return_value = mock_response

        service.call_llm("test")

        mock_persist.assert_called_once()


class TestCallLlmErrorHandling:
    """异常处理：返回空字符串 + warning 日志"""

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_returns_empty_on_exception(self, mock_litellm, mock_persist, service):
        """litellm 抛异常时返回空字符串"""
        mock_litellm.completion.side_effect = Exception("API error")

        result = service.call_llm("test")

        assert result == ""

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_returns_empty_on_no_content(self, mock_litellm, mock_persist, service):
        """LLM 返回空内容时返回空字符串"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_litellm.completion.return_value = mock_response

        result = service.call_llm("test")

        assert result == ""

    @patch("src.services.sim_trading_service.persist_llm_usage")
    @patch("src.services.sim_trading_service.litellm")
    def test_returns_empty_when_no_model(self, mock_litellm, mock_persist, service):
        """未配置模型时返回空字符串"""
        service.config.litellm_model = ""
        service.config.sim_trading_model = ""

        result = service.call_llm("test")

        assert result == ""
        mock_litellm.completion.assert_not_called()


# ─────────────────────────────────────────────
# parse_llm_response() 测试
# ─────────────────────────────────────────────


class TestParseLlmResponseHappyPath:
    """正常 JSON 解析"""

    def test_parses_valid_json(self, service):
        """有效 JSON 正确解析为 SimTradeAction 列表"""
        data = {
            "trades": [
                {
                    "stock_code": "600519",
                    "action": "buy",
                    "quantity": 100,
                    "price": 1800.0,
                    "reason": "基本面良好",
                }
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert len(result) == 1
        assert isinstance(result[0], SimTradeAction)
        assert result[0].stock_code == "600519"
        assert result[0].action == "buy"
        assert result[0].quantity == 100
        assert result[0].price == 1800.0
        assert result[0].reason == "基本面良好"

    def test_parses_multiple_trades(self, service):
        """多笔交易全部解析"""
        data = {
            "trades": [
                {"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1800.0, "reason": "r1"},
                {"stock_code": "000001", "action": "sell", "quantity": 200, "price": 13.0, "reason": "r2"},
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert len(result) == 2
        assert result[0].action == "buy"
        assert result[1].action == "sell"

    def test_empty_trades_array(self, service):
        """trades 为空数组时返回空列表"""
        data = {"trades": [], "portfolio_summary": "不适合交易"}
        result = service.parse_llm_response(json.dumps(data))

        assert result == []


class TestParseLlmResponseEmptyInput:
    """空/无效输入"""

    def test_empty_string(self, service):
        """空字符串返回空列表"""
        assert service.parse_llm_response("") == []

    def test_none_like_string(self, service):
        """纯空白返回空列表"""
        assert service.parse_llm_response("   ") == []


class TestParseLlmResponseMarkdownWrapped:
    """处理 markdown code fence 包裹"""

    def test_strips_markdown_json_fence(self, service):
        """去掉 ```json ... ``` 包裹后正常解析"""
        raw = '```json\n{"trades": [{"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1800.0, "reason": "ok"}]}\n```'
        result = service.parse_llm_response(raw)

        assert len(result) == 1
        assert result[0].stock_code == "600519"

    def test_strips_bare_code_fence(self, service):
        """去掉 ``` ... ``` 包裹后正常解析"""
        raw = '```\n{"trades": [{"stock_code": "000001", "action": "sell", "quantity": 200, "price": 13.0, "reason": "减仓"}]}\n```'
        result = service.parse_llm_response(raw)

        assert len(result) == 1


class TestParseLlmResponseFieldValidation:
    """字段校验：缺失/无效字段跳过"""

    def test_skips_missing_stock_code(self, service):
        """缺少 stock_code 的条目被跳过"""
        data = {
            "trades": [
                {"action": "buy", "quantity": 100, "price": 1800.0, "reason": "ok"},
                {"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1800.0, "reason": "ok"},
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert len(result) == 1
        assert result[0].stock_code == "600519"

    def test_skips_invalid_action(self, service):
        """action 不是 buy/sell 的条目被跳过"""
        data = {
            "trades": [
                {"stock_code": "600519", "action": "hold", "quantity": 100, "price": 1800.0, "reason": "ok"},
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert result == []

    def test_skips_zero_quantity(self, service):
        """quantity <= 0 的条目被跳过"""
        data = {
            "trades": [
                {"stock_code": "600519", "action": "buy", "quantity": 0, "price": 1800.0, "reason": "ok"},
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert result == []

    def test_skips_negative_price(self, service):
        """price <= 0 的条目被跳过"""
        data = {
            "trades": [
                {"stock_code": "600519", "action": "buy", "quantity": 100, "price": -1.0, "reason": "ok"},
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert result == []

    def test_skips_missing_reason(self, service):
        """缺少 reason 的条目被跳过"""
        data = {
            "trades": [
                {"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1800.0},
            ]
        }
        result = service.parse_llm_response(json.dumps(data))

        assert result == []


class TestParseLlmResponseJsonRepair:
    """json_repair 处理 LLM 常见 JSON 问题"""

    def test_handles_trailing_comma(self, service):
        """尾部逗号通过 repair 修复"""
        raw = '{"trades": [{"stock_code": "600519", "action": "buy", "quantity": 100, "price": 1800.0, "reason": "ok",}],}'
        result = service.parse_llm_response(raw)

        assert len(result) == 1

    def test_handles_totally_broken_json(self, service):
        """完全无法修复的内容返回空列表"""
        result = service.parse_llm_response("这不是 JSON 格式的内容，只是一段文字。")

        assert result == []


class TestParseLlmResponseNoTradesKey:
    """响应中没有 trades 键"""

    def test_no_trades_key_returns_empty(self, service):
        """响应 JSON 没有 trades 键返回空列表"""
        data = {"summary": "不建议交易"}
        result = service.parse_llm_response(json.dumps(data))

        assert result == []
