# -*- coding: utf-8 -*-
"""Portfolio API schemas."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class PortfolioAccountCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    broker: Optional[str] = Field(None, max_length=64)
    market: Literal["cn", "hk", "us"] = "cn"
    base_currency: str = Field("CNY", min_length=3, max_length=8)
    owner_id: Optional[str] = Field(None, max_length=64)


class PortfolioAccountUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    broker: Optional[str] = Field(None, max_length=64)
    market: Optional[Literal["cn", "hk", "us"]] = None
    base_currency: Optional[str] = Field(None, min_length=3, max_length=8)
    owner_id: Optional[str] = Field(None, max_length=64)
    is_active: Optional[bool] = None


class PortfolioAccountItem(BaseModel):
    id: int
    owner_id: Optional[str] = None
    name: str
    broker: Optional[str] = None
    market: str
    base_currency: str
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PortfolioAccountListResponse(BaseModel):
    accounts: List[PortfolioAccountItem] = Field(default_factory=list)


class PortfolioTradeCreateRequest(BaseModel):
    account_id: int
    symbol: str = Field(..., min_length=1, max_length=16)
    trade_date: date
    side: Literal["buy", "sell"]
    quantity: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee: float = Field(0.0, ge=0)
    tax: float = Field(0.0, ge=0)
    market: Optional[Literal["cn", "hk", "us"]] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=8)
    trade_uid: Optional[str] = Field(None, max_length=128)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioCashLedgerCreateRequest(BaseModel):
    account_id: int
    event_date: date
    direction: Literal["in", "out"]
    amount: float = Field(..., gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=8)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioCorporateActionCreateRequest(BaseModel):
    account_id: int
    symbol: str = Field(..., min_length=1, max_length=16)
    effective_date: date
    action_type: Literal["cash_dividend", "split_adjustment"]
    market: Optional[Literal["cn", "hk", "us"]] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=8)
    cash_dividend_per_share: Optional[float] = Field(None, ge=0)
    split_ratio: Optional[float] = Field(None, gt=0)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioEventCreatedResponse(BaseModel):
    id: int


class PortfolioDeleteResponse(BaseModel):
    deleted: int


class PortfolioTradeListItem(BaseModel):
    id: int
    account_id: int
    trade_uid: Optional[str] = None
    symbol: str
    market: str
    currency: str
    trade_date: str
    side: str
    quantity: float
    price: float
    fee: float
    tax: float
    note: Optional[str] = None
    created_at: Optional[str] = None


class PortfolioTradeListResponse(BaseModel):
    items: List[PortfolioTradeListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioCashLedgerListItem(BaseModel):
    id: int
    account_id: int
    event_date: str
    direction: str
    amount: float
    currency: str
    note: Optional[str] = None
    created_at: Optional[str] = None


class PortfolioCashLedgerListResponse(BaseModel):
    items: List[PortfolioCashLedgerListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioCorporateActionListItem(BaseModel):
    id: int
    account_id: int
    symbol: str
    market: str
    currency: str
    effective_date: str
    action_type: str
    cash_dividend_per_share: Optional[float] = None
    split_ratio: Optional[float] = None
    note: Optional[str] = None
    created_at: Optional[str] = None


class PortfolioCorporateActionListResponse(BaseModel):
    items: List[PortfolioCorporateActionListItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PortfolioPositionItem(BaseModel):
    symbol: str
    market: str
    currency: str
    quantity: float
    avg_cost: float
    total_cost: float
    last_price: float
    market_value_base: float
    unrealized_pnl_base: float
    valuation_currency: str


class PortfolioAccountSnapshot(BaseModel):
    account_id: int
    account_name: str
    owner_id: Optional[str] = None
    broker: Optional[str] = None
    market: str
    base_currency: str
    as_of: str
    cost_method: str
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    positions: List[PortfolioPositionItem] = Field(default_factory=list)


class PortfolioTradeUpdateRequest(BaseModel):
    """交易编辑请求。仅允许修改以下字段。"""
    quantity: Optional[float] = Field(None, gt=0, description="数量")
    price: Optional[float] = Field(None, gt=0, description="价格")
    fee: Optional[float] = Field(None, ge=0, description="手续费")
    tax: Optional[float] = Field(None, ge=0, description="税费")
    note: Optional[str] = Field(None, max_length=255, description="备注")


class PortfolioTradeUpdateResponse(BaseModel):
    """交易编辑响应。"""
    trade: PortfolioTradeListItem
    oversell_violations: List[str] = Field(default_factory=list, description="Oversell 违规详情")


class PortfolioSnapshotResponse(BaseModel):
    as_of: str
    cost_method: str
    currency: str
    account_count: int
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    accounts: List[PortfolioAccountSnapshot] = Field(default_factory=list)


class PortfolioImportTradeItem(BaseModel):
    trade_date: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float
    price: float
    fee: float
    tax: float
    trade_uid: Optional[str] = None
    dedup_hash: str
    currency: Optional[str] = None


class PortfolioImportParseResponse(BaseModel):
    broker: str
    record_count: int
    skipped_count: int
    error_count: int
    records: List[PortfolioImportTradeItem] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class PortfolioImportCommitResponse(BaseModel):
    account_id: int
    record_count: int
    inserted_count: int
    duplicate_count: int
    failed_count: int
    dry_run: bool
    errors: List[str] = Field(default_factory=list)


class PortfolioImportBrokerItem(BaseModel):
    broker: str
    aliases: List[str] = Field(default_factory=list)
    display_name: Optional[str] = None


class PortfolioImportBrokerListResponse(BaseModel):
    brokers: List[PortfolioImportBrokerItem] = Field(default_factory=list)


class PortfolioFxRefreshResponse(BaseModel):
    as_of: str
    account_count: int
    refresh_enabled: bool
    disabled_reason: Optional[str] = None
    pair_count: int
    updated_count: int
    stale_count: int
    error_count: int


class PortfolioRiskResponse(BaseModel):
    as_of: str
    account_id: Optional[int] = None
    cost_method: str
    currency: str
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    concentration: Dict[str, Any] = Field(default_factory=dict)
    sector_concentration: Dict[str, Any] = Field(default_factory=dict)
    drawdown: Dict[str, Any] = Field(default_factory=dict)
    stop_loss: Dict[str, Any] = Field(default_factory=dict)


# ── 持仓增强（附带分析评分） ──


class LatestAnalysisBrief(BaseModel):
    """最近一次分析结果摘要，附在持仓 position 上"""

    sentiment_score: Optional[int] = Field(None, description="综合评分 0-100")
    operation_advice: Optional[str] = Field(None, description="操作建议：买入/加仓/持有/减仓/卖出/观望")
    trend_prediction: Optional[str] = Field(None, description="趋势预测：强烈看多/看多/震荡/看空/强烈看空")
    ideal_buy: Optional[float] = Field(None, description="理想买入价")
    stop_loss: Optional[float] = Field(None, description="止损价")
    take_profit: Optional[float] = Field(None, description="止盈价")
    analyzed_at: Optional[str] = Field(None, description="分析时间 ISO8601")


class EnrichedPositionItem(PortfolioPositionItem):
    """在 PortfolioPositionItem 基础上叠加最近分析数据"""

    stock_name: Optional[str] = Field(None, description="股票名称")
    latest_analysis: Optional[LatestAnalysisBrief] = Field(
        None, description="最近一次分析结果摘要，无分析记录时为 null"
    )


class EnrichedAccountSnapshot(BaseModel):
    account_id: int
    account_name: str
    owner_id: Optional[str] = None
    broker: Optional[str] = None
    market: str
    base_currency: str
    as_of: str
    cost_method: str
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    positions: List[EnrichedPositionItem] = Field(default_factory=list)


class EnrichedSnapshotResponse(BaseModel):
    as_of: str
    cost_method: str
    currency: str
    account_count: int
    total_cash: float
    total_market_value: float
    total_equity: float
    realized_pnl: float
    unrealized_pnl: float
    fee_total: float
    tax_total: float
    fx_stale: bool
    accounts: List[EnrichedAccountSnapshot] = Field(default_factory=list)


# ── 交易建议 ──


class TradeSuggestionItem(BaseModel):
    """基于分析结果 + 持仓状态生成的交易建议"""

    symbol: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    action: str = Field(..., description="建议操作：buy/add/hold/reduce/sell/watch")
    current_quantity: float = Field(0, description="当前持仓数量")
    quantity_suggestion: Optional[float] = Field(None, description="建议交易数量")
    price_reference: Optional[float] = Field(None, description="参考价位")
    current_price: Optional[float] = Field(None, description="当前价格")
    avg_cost: Optional[float] = Field(None, description="持仓均价")
    sentiment_score: Optional[int] = Field(None, description="综合评分 0-100")
    reason: str = Field("", description="建议理由")
    stop_loss: Optional[float] = Field(None, description="止损价")
    take_profit: Optional[float] = Field(None, description="止盈价")
    confidence: Optional[str] = Field(None, description="置信度：高/中/低")
    is_actionable: bool = Field(False, description="是否可直接操作")


class TradeSuggestionResponse(BaseModel):
    as_of: str
    cost_method: str
    suggestions: List[TradeSuggestionItem] = Field(default_factory=list)


# ─── PendingSimTrade Schema ─────────────────


class PendingSimTradeItem(BaseModel):
    """待审批交易列表项。"""

    id: int
    account_id: int
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float = 0.0
    tax: float = 0.0
    note: Optional[str] = None
    llm_reasoning: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewer_note: Optional[str] = None


class PendingSimTradeListResponse(BaseModel):
    items: List[PendingSimTradeItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class PendingSimTradeReviewRequest(BaseModel):
    reviewer_note: Optional[str] = Field(None, max_length=500)


# ─── SimTrading Config Schema ─────────────


class SimTradingConfigResponse(BaseModel):
    approval_required: bool
    sim_trading_enabled: bool
    sim_trading_account_id: Optional[int] = None


class SimTradingConfigUpdateRequest(BaseModel):
    approval_required: bool
