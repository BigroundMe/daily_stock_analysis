# -*- coding: utf-8 -*-
"""
模拟交易服务

职责：
- 批量 LLM 组合审查：收集一轮分析结果 + 持仓快照，调用 LLM 做组合级交易决策
- 模拟交易执行：解析 LLM 决策，通过 PortfolioService 执行模拟买卖
- 安全护栏：交易前校验（现金余额、持仓数量、单笔上限、A 股手数）
- 幂等机制：每日仅执行一次，防止重复交易
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import litellm
from json_repair import repair_json

from src.config import extra_litellm_params, get_api_keys_for_model, get_config
from src.storage import persist_llm_usage

logger = logging.getLogger(__name__)


@dataclass
class SimTradeAction:
    """LLM 组合审查输出的单笔交易动作"""

    stock_code: str  # 股票代码（如 600519）
    action: str  # buy 或 sell
    quantity: int  # 交易数量（股）
    price: float  # 目标价格
    reason: str  # LLM 给出的理由


@dataclass
class SimTradingResult:
    """模拟交易执行结果"""

    account_id: int
    executed_trades: List[Dict[str, Any]]
    skipped_trades: List[Dict[str, Any]]
    errors: List[str]
    llm_model: Optional[str] = None
    total_buy_amount: float = 0.0
    total_sell_amount: float = 0.0


class SimTradingService:
    """批量 LLM 组合审查 + 模拟交易执行服务"""

    def __init__(self, portfolio_service=None):
        self.config = get_config()
        # 延迟导入 PortfolioService 避免循环引用
        if portfolio_service is not None:
            self.portfolio_service = portfolio_service
        else:
            from src.services.portfolio_service import PortfolioService

            self.portfolio_service = PortfolioService()

    def run(self, analysis_results: List[Any], *, is_scheduled: bool = False) -> Dict[str, Any]:
        """主编排方法：收集结果 → 构建上下文 → LLM 审查 → 执行交易"""
        # 0. 前置检查
        if not self.config.sim_trading_enabled:
            logger.info("模拟交易未启用")
            return {"status": "disabled"}

        if not analysis_results:
            logger.info("无分析结果，跳过模拟交易")
            return {"status": "no_results"}

        account_id = self.config.sim_trading_account_id
        if account_id is None:
            logger.warning("模拟交易未配置账户 ID (SIM_TRADING_ACCOUNT_ID)")
            return {"status": "no_account"}

        # 1. 幂等检查
        if self._has_executed_today(account_id):
            logger.info("今日已执行过模拟交易，跳过")
            return {"status": "already_executed"}

        # 2. 构建持仓上下文
        logger.info("开始构建持仓上下文（账户 %s）", account_id)
        portfolio_ctx = self.build_portfolio_context(account_id)

        # 3. 构造 LLM prompt
        prompt = self.build_llm_prompt(analysis_results, portfolio_ctx)
        if not prompt:
            logger.info("构造 prompt 失败（可能无有效分析结果），跳过模拟交易")
            return {"status": "no_prompt"}

        # 4. 调用 LLM
        logger.info("调用 LLM 进行组合审查...")
        response_text = self.call_llm(prompt)
        if not response_text:
            logger.warning("LLM 调用失败或返回空响应")
            return {"status": "llm_failed"}

        logger.info("[SimTrading] LLM 原始响应（前 500 字符）: %s", response_text[:500])

        # 5. 解析响应
        actions = self.parse_llm_response(response_text)
        if not actions:
            logger.info("LLM 未给出交易建议（可能判断当前不适合交易）")
            return {"status": "no_actions", "llm_response": response_text[:500]}

        # 6. 校验并执行
        logger.info("开始执行 %d 笔模拟交易...", len(actions))
        result = self.validate_and_execute(actions, account_id, portfolio_ctx)

        executed = len(result.executed_trades)
        skipped = len(result.skipped_trades)
        errors = len(result.errors)
        logger.info(
            "模拟交易完成: 执行=%d 跳过=%d 错误=%d 买入=%.2f 卖出=%.2f",
            executed, skipped, errors, result.total_buy_amount, result.total_sell_amount,
        )

        return {
            "status": "completed",
            "result": result,
            "actions_count": len(actions),
            "executed_count": executed,
            "skipped_count": skipped,
            "error_count": errors,
        }

    def build_portfolio_context(self, account_id: int) -> Dict[str, Any]:
        """获取持仓快照 + 现金余额 + 近期交易记录"""
        empty_result: Dict[str, Any] = {
            "account_id": account_id,
            "account_name": "",
            "total_equity": 0.0,
            "cash_balance": 0.0,
            "positions": [],
            "recent_trades": [],
        }

        # 1. 获取持仓快照
        try:
            snapshot = self.portfolio_service.get_portfolio_snapshot(account_id=account_id)
        except Exception:
            logger.warning("获取持仓快照失败，account_id=%s，返回空结构", account_id, exc_info=True)
            return empty_result

        # 2. 提取账户信息
        accounts = snapshot.get("accounts", [])
        if not accounts:
            return empty_result

        acct = accounts[0]
        result: Dict[str, Any] = {
            "account_id": acct.get("account_id", account_id),
            "account_name": acct.get("account_name", ""),
            "total_equity": acct.get("total_equity", 0.0),
            "cash_balance": acct.get("total_cash", 0.0),
            "positions": [],
            "recent_trades": [],
        }

        # 3. 映射持仓
        for pos in acct.get("positions", []):
            total_cost = float(pos.get("total_cost", 0.0))
            unrealized_pnl = float(pos.get("unrealized_pnl_base", 0.0))
            pnl_pct = (unrealized_pnl / total_cost * 100.0) if total_cost else 0.0
            result["positions"].append(
                {
                    "stock_code": pos.get("symbol", ""),
                    "stock_name": pos.get("stock_name", ""),
                    "quantity": int(pos.get("quantity", 0)),
                    "avg_cost": float(pos.get("avg_cost", 0.0)),
                    "current_price": float(pos.get("last_price", 0.0)),
                    "market_value": float(pos.get("market_value_base", 0.0)),
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": round(pnl_pct, 2),
                }
            )

        # 4. 获取近 7 天交易记录
        try:
            today = date.today()
            trades, _ = self.portfolio_service.repo.query_trades(
                account_id=account_id,
                date_from=today - timedelta(days=7),
                date_to=today,
                symbol=None,
                side=None,
                page=1,
                page_size=100,
            )
            for t in trades:
                qty = float(t.quantity or 0)
                price = float(t.price or 0)
                result["recent_trades"].append(
                    {
                        "trade_date": t.trade_date.isoformat() if t.trade_date else "",
                        "stock_code": t.symbol or "",
                        "action": (t.side or "").lower(),
                        "quantity": int(qty),
                        "price": price,
                        "amount": round(qty * price, 2),
                    }
                )
        except Exception:
            logger.warning("获取近期交易记录失败，account_id=%s", account_id, exc_info=True)

        return result

    def build_llm_prompt(self, results: List[Any], portfolio_ctx: Dict[str, Any]) -> str:
        """构造 LLM 组合审查 prompt

        Args:
            results: 本轮 AnalysisResult 列表
            portfolio_ctx: 持仓快照（total_assets / available_cash / positions / recent_trades）

        Returns:
            组装好的中文 prompt；results 为空时返回空字符串
        """
        if not results:
            return ""

        max_single = self.config.sim_trading_max_single_amount
        ctx = portfolio_ctx or {}

        total_assets = ctx.get("total_equity", 0)
        available_cash = ctx.get("cash_balance", 0)
        positions = ctx.get("positions", [])
        recent_trades = ctx.get("recent_trades", [])

        parts: List[str] = []

        # ── 系统角色 ──
        parts.append(
            "你是一个专业的投资组合管理 AI。"
            "你需要根据当前持仓状况和最新的个股分析结果，从组合角度做出交易决策。"
        )

        # ── 当前持仓快照 ──
        parts.append("\n## 当前持仓快照\n")
        parts.append(f"- 总资产: {total_assets}")
        parts.append(f"- 可用现金: {available_cash}")

        if positions:
            parts.append("\n| 代码 | 名称 | 数量 | 成本 | 现价 | 市值 | 浮盈亏 |")
            parts.append("|------|------|------|------|------|------|--------|")
            for p in positions:
                parts.append(
                    f"| {p.get('stock_code', '')} "
                    f"| {p.get('stock_name', '')} "
                    f"| {p.get('quantity', 0)} "
                    f"| {p.get('avg_cost', 0)} "
                    f"| {p.get('current_price', 0)} "
                    f"| {p.get('market_value', 0)} "
                    f"| {p.get('unrealized_pnl', 0)} |"
                )
        else:
            parts.append("\n当前无持仓。")

        # ── 近期交易记录 ──
        parts.append("\n## 近期交易记录（最近 7 天）\n")
        if recent_trades:
            for t in recent_trades:
                parts.append(
                    f"- {t.get('date', '')} {t.get('stock_code', '')} "
                    f"{t.get('action', '')} {t.get('quantity', 0)}股 @ {t.get('price', 0)}"
                )
        else:
            parts.append("无近期交易记录。")

        # ── 本轮分析结果 ──
        parts.append("\n## 本轮分析结果\n")
        parts.append("| 代码 | 名称 | 评分 | 决策类型 | 操作建议 | 核心结论 | 当前价格 |")
        parts.append("|------|------|------|----------|----------|----------|----------|")
        for r in results:
            code = getattr(r, "code", "")
            name = getattr(r, "name", "")
            score = getattr(r, "sentiment_score", "N/A")
            decision = getattr(r, "decision_type", "hold")
            advice = getattr(r, "operation_advice", "")
            summary_raw = getattr(r, "analysis_summary", "") or ""
            summary = summary_raw[:200]
            price = getattr(r, "current_price", "N/A")
            parts.append(
                f"| {code} | {name} | {score} | {decision} | {advice} | {summary} | {price} |"
            )

        # ── 约束条件 ──
        # 计算非稳健资产的已占用市值
        non_core_value = sum(
            p.get("market_value", 0) for p in positions if p.get("stock_code") != "515080"
        )
        core_value = sum(
            p.get("market_value", 0) for p in positions if p.get("stock_code") == "515080"
        )
        non_core_budget = total_assets * 0.4 - non_core_value
        core_budget = total_assets * 0.6 - core_value

        parts.append("\n## 约束条件\n")
        parts.append("- 只能买入或卖出，不能做空")
        parts.append("- 买入金额不能超过可用现金")
        parts.append("- 卖出数量不能超过当前持仓")
        parts.append(f"- 单笔交易金额不超过 {max_single} 元")
        parts.append("- A 股（6 位数字代码）买卖数量必须是 100 的整数倍")
        parts.append("- 如果判断当前不适合交易，可以返回空数组")
        parts.append(
            f"- 仓位比例限制（基于总资产 {total_assets:.2f} 元）：\n"
            f"  - 515080（中证红利ETF招商）为稳健型核心资产，持仓市值上限为总资产的 60%"
            f"（当前占 {core_value:.0f} 元，剩余额度 {max(core_budget, 0):.0f} 元）\n"
            f"  - 除 515080 以外的**所有标的合计**持仓市值不得超过总资产的 40%"
            f"（当前合计 {non_core_value:.0f} 元，剩余额度 {max(non_core_budget, 0):.0f} 元）\n"
            f"  - 本次所有非 515080 标的的买入金额之和不得超过 {max(non_core_budget, 0):.0f} 元"
        )

        # ── 输出格式要求 ──
        parts.append("\n## 输出格式要求\n")
        parts.append("请返回 JSON 格式，结构如下：")
        parts.append(
            "```json\n"
            "{\n"
            '  "trades": [\n'
            "    {\n"
            '      "stock_code": "600519",\n'
            '      "action": "buy",\n'
            '      "quantity": 100,\n'
            '      "price": 1800.00,\n'
            '      "reason": "理由说明"\n'
            "    }\n"
            "  ],\n"
            '  "portfolio_summary": "简要说明本次决策的整体思路"\n'
            "}\n"
            "```\n"
            '如果没有合适的交易机会，返回 {"trades": [], "portfolio_summary": "说明原因"}'
        )

        return "\n".join(parts)

    def call_llm(self, prompt: str) -> str:
        """调用 LiteLLM 获取组合决策

        优先使用 sim_trading_model，为空时回退到 litellm_model。
        主模型失败后依次尝试 sim_trading_fallback_models 中的模型。

        Returns:
            LLM 响应文本；所有模型均失败时返回空字符串
        """
        primary_model = self.config.sim_trading_model or self.config.litellm_model
        if not primary_model:
            logger.warning("[SimTrading] 未配置可用的 LLM 模型，跳过调用")
            return ""

        # 构建候选模型列表：主模型 + fallback 模型
        candidates = [primary_model] + [
            m for m in (self.config.sim_trading_fallback_models or []) if m != primary_model
        ]

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个专业的投资组合管理 AI。"
                    "请根据用户提供的持仓和分析数据，输出 JSON 格式的交易决策。"
                ),
            },
            {"role": "user", "content": prompt},
        ]

        for idx, model in enumerate(candidates):
            try:
                call_kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": self.config.llm_temperature,
                    "max_tokens": 4096,
                    "timeout": 120,
                    "response_format": {"type": "json_object"},
                }

                # 补充 API key 和 extra params（legacy 路径）
                keys = get_api_keys_for_model(model, self.config)
                if keys:
                    call_kwargs["api_key"] = keys[0]
                call_kwargs.update(extra_litellm_params(model, self.config))

                response = litellm.completion(**call_kwargs)

                content = None
                if response and response.choices and response.choices[0].message:
                    content = response.choices[0].message.content

                if not content:
                    logger.warning("[SimTrading] 模型 %s 返回空内容", model)
                    continue

                # 记录 usage
                usage = {}
                usage_obj = getattr(response, "usage", None)
                if usage_obj:
                    usage = {
                        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
                    }
                persist_llm_usage(usage, model=model, call_type="sim_trading")

                if idx > 0:
                    logger.info("[SimTrading] 使用 fallback 模型 %s 成功", model)
                return content

            except Exception:
                remaining = len(candidates) - idx - 1
                if remaining > 0:
                    logger.warning(
                        "[SimTrading] 模型 %s 调用失败，剩余 %d 个 fallback 模型",
                        model,
                        remaining,
                        exc_info=True,
                    )
                else:
                    logger.warning("[SimTrading] 所有模型均调用失败（最后: %s）", model, exc_info=True)

        return ""

    def parse_llm_response(self, response_text: str) -> List[SimTradeAction]:
        """解析 LLM 响应为 List[SimTradeAction]

        处理：空响应、markdown code fence、json_repair、字段校验
        """
        if not response_text or not response_text.strip():
            return []

        text = response_text.strip()

        # 去掉 markdown code fence 包裹
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        if not text:
            return []

        # 尝试 json_repair 修复后解析
        try:
            repaired = repair_json(text, return_objects=False)
            data = json.loads(repaired)
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("[SimTrading] JSON 解析失败，原始文本: %s", text[:200])
            return []

        if not isinstance(data, dict):
            logger.warning("[SimTrading] LLM 响应不是 JSON 对象")
            return []

        trades_raw = data.get("trades")
        if not isinstance(trades_raw, list):
            return []

        actions: List[SimTradeAction] = []
        for idx, item in enumerate(trades_raw):
            # 支持两种格式：
            # 1. 对象: {"stock_code": "...", "action": "...", ...}
            # 2. 数组: ["stock_code", "action", quantity, price, "reason"]
            if isinstance(item, list) and len(item) >= 5:
                stock_code = item[0]
                action = item[1]
                quantity = item[2]
                price = item[3]
                reason = item[4]
            elif isinstance(item, dict):
                stock_code = item.get("stock_code")
                action = item.get("action")
                quantity = item.get("quantity")
                price = item.get("price")
                reason = item.get("reason")
            else:
                logger.warning("[SimTrading] trades[%d] 格式不支持（需对象或数组），跳过", idx)
                continue

            # 必填字段检查
            if not stock_code or not isinstance(stock_code, str):
                logger.warning("[SimTrading] trades[%d] 缺少 stock_code，跳过", idx)
                continue
            if action not in ("buy", "sell"):
                logger.warning("[SimTrading] trades[%d] action=%s 无效（需 buy/sell），跳过", idx, action)
                continue
            if not reason or not isinstance(reason, str):
                logger.warning("[SimTrading] trades[%d] 缺少 reason，跳过", idx)
                continue

            # 数值校验
            try:
                quantity_int = int(quantity)
                price_float = float(price)
            except (TypeError, ValueError):
                logger.warning("[SimTrading] trades[%d] quantity/price 类型无效，跳过", idx)
                continue

            if quantity_int <= 0:
                logger.warning("[SimTrading] trades[%d] quantity=%d <= 0，跳过", idx, quantity_int)
                continue
            if price_float <= 0:
                logger.warning("[SimTrading] trades[%d] price=%s <= 0，跳过", idx, price_float)
                continue

            actions.append(
                SimTradeAction(
                    stock_code=str(stock_code),
                    action=action,
                    quantity=quantity_int,
                    price=price_float,
                    reason=str(reason),
                )
            )

        return actions

    def validate_and_execute(
        self, actions: List[SimTradeAction], account_id: int, portfolio_ctx: Dict[str, Any]
    ) -> SimTradingResult:
        """逐笔校验并执行交易

        Args:
            actions: LLM 输出的交易动作列表
            account_id: 账户 ID
            portfolio_ctx: 持仓快照上下文（cash_balance, positions 等）

        Returns:
            SimTradingResult 包含已执行、已跳过、错误列表及汇总金额
        """
        executed_trades: List[Dict[str, Any]] = []
        skipped_trades: List[Dict[str, Any]] = []
        errors: List[str] = []

        # 当前可用现金
        available_cash = float(portfolio_ctx.get("cash_balance", 0.0))

        # 持仓查找表 {stock_code: quantity}
        position_map: Dict[str, float] = {}
        for pos in portfolio_ctx.get("positions", []):
            code = pos.get("stock_code", "")
            if code:
                position_map[code] = float(pos.get("quantity", 0))

        max_single = self.config.sim_trading_max_single_amount
        today = date.today()

        total_buy_amount = 0.0
        total_sell_amount = 0.0

        # ── 仓位限制相关计算 ──
        total_assets = float(portfolio_ctx.get("total_equity", 0.0))
        # 非 515080 持仓市值（含已执行买入的增量）
        non_core_value = sum(
            float(p.get("market_value", 0))
            for p in portfolio_ctx.get("positions", [])
            if p.get("stock_code") != "515080"
        )
        core_value = sum(
            float(p.get("market_value", 0))
            for p in portfolio_ctx.get("positions", [])
            if p.get("stock_code") == "515080"
        )
        # 动态追踪本轮已买入的增量
        non_core_bought = 0.0
        core_bought = 0.0

        for action in actions:
            trade_amount = action.quantity * action.price

            # A 股手数校验（买卖通用）
            if self._is_a_share(action.stock_code) and action.quantity % 100 != 0:
                skipped_trades.append({
                    "stock_code": action.stock_code,
                    "action": action.action,
                    "quantity": action.quantity,
                    "price": action.price,
                    "reason": f"A 股交易数量必须是 100 的整数倍，当前 {action.quantity}",
                })
                continue

            if action.action == "buy":
                # 现金不足
                if trade_amount > available_cash:
                    skipped_trades.append({
                        "stock_code": action.stock_code,
                        "action": action.action,
                        "quantity": action.quantity,
                        "price": action.price,
                        "reason": f"现金不足: 需要 {trade_amount:.2f}, 可用 {available_cash:.2f}",
                    })
                    continue

                # 单笔上限
                if trade_amount > max_single:
                    skipped_trades.append({
                        "stock_code": action.stock_code,
                        "action": action.action,
                        "quantity": action.quantity,
                        "price": action.price,
                        "reason": f"单笔金额 {trade_amount:.2f} 超过上限 {max_single:.2f}",
                    })
                    continue

                # 仓位比例硬性限制
                if total_assets > 0:
                    if action.stock_code == "515080":
                        if core_value + core_bought + trade_amount > total_assets * 0.6:
                            skipped_trades.append({
                                "stock_code": action.stock_code,
                                "action": action.action,
                                "quantity": action.quantity,
                                "price": action.price,
                                "reason": (
                                    f"515080 仓位超限: 当前 {core_value + core_bought:.0f} + "
                                    f"本笔 {trade_amount:.0f} > 上限 {total_assets * 0.6:.0f}"
                                ),
                            })
                            continue
                    else:
                        if non_core_value + non_core_bought + trade_amount > total_assets * 0.4:
                            skipped_trades.append({
                                "stock_code": action.stock_code,
                                "action": action.action,
                                "quantity": action.quantity,
                                "price": action.price,
                                "reason": (
                                    f"非515080 合计仓位超限: 当前 {non_core_value + non_core_bought:.0f} + "
                                    f"本笔 {trade_amount:.0f} > 上限 {total_assets * 0.4:.0f}"
                                ),
                            })
                            continue

            elif action.action == "sell":
                # 持仓不足
                current_qty = position_map.get(action.stock_code, 0)
                if action.quantity > current_qty:
                    skipped_trades.append({
                        "stock_code": action.stock_code,
                        "action": action.action,
                        "quantity": action.quantity,
                        "price": action.price,
                        "reason": f"持仓不足: 需要卖出 {action.quantity}, 当前持有 {int(current_qty)}",
                    })
                    continue

            # 执行交易
            try:
                commission = self.config.sim_trading_default_commission
                result = self.portfolio_service.record_trade(
                    account_id=account_id,
                    symbol=action.stock_code,
                    trade_date=today,
                    side=action.action,
                    quantity=float(action.quantity),
                    price=action.price,
                    fee=commission,
                    note=f"[sim-trading] {action.reason}",
                )
                executed_trades.append({
                    "stock_code": action.stock_code,
                    "action": action.action,
                    "quantity": action.quantity,
                    "price": action.price,
                    "amount": trade_amount,
                    "trade_id": result.get("id"),
                })
                # 更新可用现金与持仓
                if action.action == "buy":
                    available_cash -= trade_amount + commission
                    position_map[action.stock_code] = position_map.get(action.stock_code, 0) + action.quantity
                    total_buy_amount += trade_amount
                    # 更新仓位限制追踪
                    if action.stock_code == "515080":
                        core_bought += trade_amount
                    else:
                        non_core_bought += trade_amount
                elif action.action == "sell":
                    available_cash += trade_amount
                    position_map[action.stock_code] = position_map.get(action.stock_code, 0) - action.quantity
                    total_sell_amount += trade_amount
            except Exception as exc:
                logger.warning("模拟交易执行失败: %s %s %s, error=%s", action.action, action.stock_code, action.quantity, exc)
                errors.append(f"{action.action} {action.stock_code} x{action.quantity}: {exc}")

        return SimTradingResult(
            account_id=account_id,
            executed_trades=executed_trades,
            skipped_trades=skipped_trades,
            errors=errors,
            total_buy_amount=total_buy_amount,
            total_sell_amount=total_sell_amount,
        )

    def _is_a_share(self, stock_code: str) -> bool:
        """判断是否为 A 股代码（6 位纯数字）"""
        return len(stock_code) == 6 and stock_code.isdigit()

    def _has_executed_today(self, account_id: int) -> bool:
        """检查今日是否已执行过模拟交易（幂等检查）"""
        try:
            today = date.today()
            trades, _ = self.portfolio_service.repo.query_trades(
                account_id=account_id,
                date_from=today,
                date_to=today,
                symbol=None,
                side=None,
                page=1,
                page_size=1000,
            )
            return any(
                (t.note or "").startswith("[sim-trading]") for t in trades
            )
        except Exception:
            logger.error("幂等检查查询失败，account_id=%s，安全跳过本次执行", account_id, exc_info=True)
            return True
