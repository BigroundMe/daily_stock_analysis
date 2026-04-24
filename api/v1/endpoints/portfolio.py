# -*- coding: utf-8 -*-
"""Portfolio endpoints (P0 core account + snapshot workflow)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.portfolio import (
    PortfolioAccountCreateRequest,
    PortfolioAccountItem,
    PortfolioAccountListResponse,
    PortfolioAccountUpdateRequest,
    PortfolioCashLedgerListResponse,
    PortfolioCashLedgerCreateRequest,
    PortfolioCorporateActionListResponse,
    PortfolioCorporateActionCreateRequest,
    PortfolioDeleteResponse,
    PortfolioEventCreatedResponse,
    PortfolioFxRefreshResponse,
    PortfolioImportBrokerListResponse,
    PortfolioImportCommitResponse,
    PortfolioImportParseResponse,
    PortfolioImportTradeItem,
    PortfolioRiskResponse,
    PortfolioSnapshotResponse,
    PortfolioTradeListItem,
    PortfolioTradeListResponse,
    PortfolioTradeCreateRequest,
    PortfolioTradeUpdateRequest,
    PortfolioTradeUpdateResponse,
    EnrichedSnapshotResponse,
    PendingSimTradeReviewRequest,
    SimTradingConfigUpdateRequest,
    TradeSuggestionResponse,
)
from src.services.portfolio_import_service import PortfolioImportService
from src.services.portfolio_risk_service import PortfolioRiskService
from src.services.portfolio_service import (
    OversellError,
    PortfolioBusyError,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)
from src.services.sim_trading_service import SimTradingService

logger = logging.getLogger(__name__)

router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "validation_error", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error(f"{message}: {exc}", exc_info=True)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: {str(exc)}"},
    )


def _conflict_error(*, error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": error, "message": message},
    )


def _serialize_import_record(item: dict) -> PortfolioImportTradeItem:
    payload = dict(item)
    trade_date = payload.get("trade_date")
    if isinstance(trade_date, date):
        payload["trade_date"] = trade_date.isoformat()
    else:
        payload["trade_date"] = str(trade_date)
    return PortfolioImportTradeItem(**payload)


@router.post(
    "/accounts",
    response_model=PortfolioAccountItem,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Create portfolio account",
)
def create_account(request: PortfolioAccountCreateRequest) -> PortfolioAccountItem:
    service = PortfolioService()
    try:
        row = service.create_account(
            name=request.name,
            broker=request.broker,
            market=request.market,
            base_currency=request.base_currency,
            owner_id=request.owner_id,
        )
        return PortfolioAccountItem(**row)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Create account failed", exc)


@router.get(
    "/accounts",
    response_model=PortfolioAccountListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List portfolio accounts",
)
def list_accounts(
    include_inactive: bool = Query(False, description="Whether to include inactive accounts"),
) -> PortfolioAccountListResponse:
    service = PortfolioService()
    try:
        rows = service.list_accounts(include_inactive=include_inactive)
        return PortfolioAccountListResponse(accounts=[PortfolioAccountItem(**item) for item in rows])
    except Exception as exc:
        raise _internal_error("List accounts failed", exc)


@router.put(
    "/accounts/{account_id}",
    response_model=PortfolioAccountItem,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Update portfolio account",
)
def update_account(account_id: int, request: PortfolioAccountUpdateRequest) -> PortfolioAccountItem:
    service = PortfolioService()
    try:
        updated = service.update_account(
            account_id,
            name=request.name,
            broker=request.broker,
            market=request.market,
            base_currency=request.base_currency,
            owner_id=request.owner_id,
            is_active=request.is_active,
        )
        if updated is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": f"Account not found: {account_id}"},
            )
        return PortfolioAccountItem(**updated)
    except HTTPException:
        raise
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Update account failed", exc)


@router.delete(
    "/accounts/{account_id}",
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Deactivate portfolio account",
)
def delete_account(account_id: int):
    service = PortfolioService()
    try:
        ok = service.deactivate_account(account_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": f"Account not found: {account_id}"},
            )
        return {"deleted": 1}
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Deactivate account failed", exc)


@router.post(
    "/trades",
    response_model=PortfolioEventCreatedResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Record trade event",
)
def create_trade(request: PortfolioTradeCreateRequest) -> PortfolioEventCreatedResponse:
    service = PortfolioService()
    try:
        data = service.record_trade(
            account_id=request.account_id,
            symbol=request.symbol,
            trade_date=request.trade_date,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            fee=request.fee,
            tax=request.tax,
            market=request.market,
            currency=request.currency,
            trade_uid=request.trade_uid,
            note=request.note,
        )
        return PortfolioEventCreatedResponse(**data)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except PortfolioOversellError as exc:
        raise _conflict_error(error="portfolio_oversell", message=str(exc))
    except PortfolioConflictError as exc:
        raise _conflict_error(error="conflict", message=str(exc))
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Create trade failed", exc)


@router.get(
    "/trades",
    response_model=PortfolioTradeListResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List trade events",
)
def list_trades(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    date_from: Optional[date] = Query(None, description="Trade date from"),
    date_to: Optional[date] = Query(None, description="Trade date to"),
    symbol: Optional[str] = Query(None, description="Optional stock symbol filter"),
    side: Optional[str] = Query(None, description="Optional side filter: buy/sell"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortfolioTradeListResponse:
    service = PortfolioService()
    try:
        data = service.list_trade_events(
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            symbol=symbol,
            side=side,
            page=page,
            page_size=page_size,
        )
        return PortfolioTradeListResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List trade events failed", exc)


@router.delete(
    "/trades/{trade_id}",
    response_model=PortfolioDeleteResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete trade event",
)
def delete_trade(trade_id: int) -> PortfolioDeleteResponse:
    service = PortfolioService()
    try:
        ok = service.delete_trade_event(trade_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": f"Trade not found: {trade_id}"},
            )
        return PortfolioDeleteResponse(deleted=1)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Delete trade event failed", exc)


def _trade_orm_to_dict(trade) -> dict:
    """将 PortfolioTrade ORM 对象序列化为 PortfolioTradeListItem 兼容的 dict。"""
    return {
        "id": trade.id,
        "account_id": trade.account_id,
        "trade_uid": trade.trade_uid,
        "symbol": trade.symbol,
        "market": trade.market,
        "currency": trade.currency,
        "trade_date": trade.trade_date.isoformat() if trade.trade_date else None,
        "side": trade.side,
        "quantity": trade.quantity,
        "price": trade.price,
        "fee": trade.fee or 0.0,
        "tax": trade.tax or 0.0,
        "note": trade.note,
        "created_at": trade.created_at.isoformat() if trade.created_at else None,
    }


@router.put(
    "/trades/{trade_id}",
    response_model=PortfolioTradeUpdateResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Update trade event",
)
def update_trade(trade_id: int, body: PortfolioTradeUpdateRequest) -> PortfolioTradeUpdateResponse:
    """编辑交易记录（仅 quantity/price/fee/tax/note）。

    如果编辑后出现 oversell，返回 400 错误。
    """
    service = PortfolioService()
    try:
        updates = body.model_dump(exclude_none=True)
        trade = service.update_trade_event(trade_id, updates)
        return PortfolioTradeUpdateResponse(
            trade=PortfolioTradeListItem(**_trade_orm_to_dict(trade)),
            oversell_violations=[],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": str(exc)},
        )
    except OversellError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "oversell", "message": "编辑导致 oversell 违规", "violations": exc.violations},
        )
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except Exception as exc:
        raise _internal_error("Update trade event failed", exc)


@router.post(
    "/cash-ledger",
    response_model=PortfolioEventCreatedResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Record cash event",
)
def create_cash_ledger(request: PortfolioCashLedgerCreateRequest) -> PortfolioEventCreatedResponse:
    service = PortfolioService()
    try:
        data = service.record_cash_ledger(
            account_id=request.account_id,
            event_date=request.event_date,
            direction=request.direction,
            amount=request.amount,
            currency=request.currency,
            note=request.note,
        )
        return PortfolioEventCreatedResponse(**data)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Create cash ledger event failed", exc)


@router.get(
    "/cash-ledger",
    response_model=PortfolioCashLedgerListResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List cash ledger events",
)
def list_cash_ledger(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    date_from: Optional[date] = Query(None, description="Cash event date from"),
    date_to: Optional[date] = Query(None, description="Cash event date to"),
    direction: Optional[str] = Query(None, description="Optional direction filter: in/out"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortfolioCashLedgerListResponse:
    service = PortfolioService()
    try:
        data = service.list_cash_ledger_events(
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            direction=direction,
            page=page,
            page_size=page_size,
        )
        return PortfolioCashLedgerListResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List cash ledger events failed", exc)


@router.delete(
    "/cash-ledger/{entry_id}",
    response_model=PortfolioDeleteResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete cash ledger event",
)
def delete_cash_ledger(entry_id: int) -> PortfolioDeleteResponse:
    service = PortfolioService()
    try:
        ok = service.delete_cash_ledger_event(entry_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": f"Cash ledger entry not found: {entry_id}"},
            )
        return PortfolioDeleteResponse(deleted=1)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Delete cash ledger event failed", exc)


@router.post(
    "/corporate-actions",
    response_model=PortfolioEventCreatedResponse,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Record corporate action event",
)
def create_corporate_action(request: PortfolioCorporateActionCreateRequest) -> PortfolioEventCreatedResponse:
    service = PortfolioService()
    try:
        data = service.record_corporate_action(
            account_id=request.account_id,
            symbol=request.symbol,
            effective_date=request.effective_date,
            action_type=request.action_type,
            market=request.market,
            currency=request.currency,
            cash_dividend_per_share=request.cash_dividend_per_share,
            split_ratio=request.split_ratio,
            note=request.note,
        )
        return PortfolioEventCreatedResponse(**data)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Create corporate action event failed", exc)


@router.get(
    "/corporate-actions",
    response_model=PortfolioCorporateActionListResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List corporate action events",
)
def list_corporate_actions(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    date_from: Optional[date] = Query(None, description="Corporate action effective date from"),
    date_to: Optional[date] = Query(None, description="Corporate action effective date to"),
    symbol: Optional[str] = Query(None, description="Optional stock symbol filter"),
    action_type: Optional[str] = Query(None, description="Optional action type filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PortfolioCorporateActionListResponse:
    service = PortfolioService()
    try:
        data = service.list_corporate_action_events(
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            symbol=symbol,
            action_type=action_type,
            page=page,
            page_size=page_size,
        )
        return PortfolioCorporateActionListResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List corporate action events failed", exc)


@router.delete(
    "/corporate-actions/{action_id}",
    response_model=PortfolioDeleteResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Delete corporate action event",
)
def delete_corporate_action(action_id: int) -> PortfolioDeleteResponse:
    service = PortfolioService()
    try:
        ok = service.delete_corporate_action_event(action_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": f"Corporate action not found: {action_id}"},
            )
        return PortfolioDeleteResponse(deleted=1)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Delete corporate action event failed", exc)


@router.get(
    "/snapshot",
    response_model=PortfolioSnapshotResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get portfolio snapshot",
)
def get_snapshot(
    account_id: Optional[int] = Query(None, description="Optional account id, default returns all accounts"),
    as_of: Optional[date] = Query(None, description="Snapshot date, default today"),
    cost_method: str = Query("fifo", description="Cost method: fifo or avg"),
) -> PortfolioSnapshotResponse:
    service = PortfolioService()
    try:
        data = service.get_portfolio_snapshot(
            account_id=account_id,
            as_of=as_of,
            cost_method=cost_method,
        )
        return PortfolioSnapshotResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get snapshot failed", exc)


@router.post(
    "/imports/csv/parse",
    response_model=PortfolioImportParseResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Parse broker CSV into normalized trade records",
)
def parse_csv_import(
    broker: str = Form(..., description="Broker id: huatai/citic/cmb"),
    file: UploadFile = File(...),
) -> PortfolioImportParseResponse:
    importer = PortfolioImportService()
    try:
        content = file.file.read()
        parsed = importer.parse_trade_csv(broker=broker, content=content)
        return PortfolioImportParseResponse(
            broker=parsed["broker"],
            record_count=parsed["record_count"],
            skipped_count=parsed["skipped_count"],
            error_count=parsed["error_count"],
            records=[_serialize_import_record(item) for item in parsed.get("records", [])],
            errors=list(parsed.get("errors", [])),
        )
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Parse CSV import failed", exc)


@router.get(
    "/imports/csv/brokers",
    response_model=PortfolioImportBrokerListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List supported broker CSV parsers",
)
def list_csv_brokers() -> PortfolioImportBrokerListResponse:
    importer = PortfolioImportService()
    try:
        return PortfolioImportBrokerListResponse(brokers=importer.list_supported_brokers())
    except Exception as exc:
        raise _internal_error("List CSV brokers failed", exc)


@router.post(
    "/imports/csv/commit",
    response_model=PortfolioImportCommitResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Parse and commit broker CSV with dedup",
)
def commit_csv_import(
    account_id: int = Form(...),
    broker: str = Form(..., description="Broker id: huatai/citic/cmb"),
    dry_run: bool = Form(False),
    file: UploadFile = File(...),
) -> PortfolioImportCommitResponse:
    importer = PortfolioImportService()
    try:
        content = file.file.read()
        parsed = importer.parse_trade_csv(broker=broker, content=content)
        result = importer.commit_trade_records(
            account_id=account_id,
            broker=parsed["broker"],
            records=list(parsed.get("records", [])),
            dry_run=dry_run,
        )
        return PortfolioImportCommitResponse(**result)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Commit CSV import failed", exc)


@router.post(
    "/fx/refresh",
    response_model=PortfolioFxRefreshResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Refresh FX cache online with stale fallback",
)
def refresh_fx_rates(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    as_of: Optional[date] = Query(None, description="Rate date, default today"),
) -> PortfolioFxRefreshResponse:
    service = PortfolioService()
    try:
        data = service.refresh_fx_rates(account_id=account_id, as_of=as_of)
        return PortfolioFxRefreshResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Refresh FX rates failed", exc)


@router.get(
    "/snapshot/enriched",
    response_model=EnrichedSnapshotResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get enriched portfolio snapshot with latest analysis scores",
)
def get_enriched_snapshot(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    as_of: Optional[date] = Query(None, description="Snapshot date, default today"),
    cost_method: str = Query("fifo", description="Cost method: fifo or avg"),
) -> EnrichedSnapshotResponse:
    service = PortfolioService()
    try:
        data = service.get_enriched_snapshot(
            account_id=account_id,
            as_of=as_of,
            cost_method=cost_method,
        )
        return EnrichedSnapshotResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get enriched snapshot failed", exc)


@router.get(
    "/trade-suggestions",
    response_model=TradeSuggestionResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get trade suggestions based on analysis + portfolio",
)
def get_trade_suggestions(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    as_of: Optional[date] = Query(None, description="Date, default today"),
    cost_method: str = Query("fifo", description="Cost method: fifo or avg"),
) -> TradeSuggestionResponse:
    service = PortfolioService()
    try:
        data = service.generate_trade_suggestions(
            account_id=account_id,
            as_of=as_of,
            cost_method=cost_method,
        )
        return TradeSuggestionResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get trade suggestions failed", exc)


@router.get(
    "/risk",
    response_model=PortfolioRiskResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get portfolio risk report",
)
def get_risk_report(
    account_id: Optional[int] = Query(None, description="Optional account id"),
    as_of: Optional[date] = Query(None, description="Risk report date, default today"),
    cost_method: str = Query("fifo", description="Cost method: fifo or avg"),
) -> PortfolioRiskResponse:
    service = PortfolioRiskService()
    try:
        data = service.get_risk_report(account_id=account_id, as_of=as_of, cost_method=cost_method)
        return PortfolioRiskResponse(**data)
    except ValueError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Get risk report failed", exc)


# ------------------------------------------------------------------
# PendingSimTrade 审批端点
# ------------------------------------------------------------------


def _pending_to_dict(item) -> dict:
    """将 PendingSimTrade ORM 对象转换为字典。"""
    return {
        "id": item.id,
        "account_id": item.account_id,
        "symbol": item.symbol,
        "side": item.side,
        "quantity": float(item.quantity),
        "price": float(item.price),
        "fee": float(item.fee or 0),
        "tax": float(item.tax or 0),
        "note": item.note,
        "llm_reasoning": item.llm_reasoning,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "reviewed_at": item.reviewed_at.isoformat() if item.reviewed_at else None,
        "reviewer_note": item.reviewer_note,
    }


@router.get(
    "/sim-trades/pending",
    summary="List pending sim trades",
)
def list_pending_sim_trades(
    account_id: Optional[int] = Query(None, description="Optional account id filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """获取待审批模拟交易列表。"""
    service = PortfolioService()
    repo = service.repo
    try:
        items, total = repo.list_pending_sim_trades(
            account_id=account_id, status="pending", page=page, page_size=page_size
        )
        return {
            "items": [_pending_to_dict(i) for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Exception as exc:
        raise _internal_error("List pending sim trades failed", exc)


@router.post(
    "/sim-trades/{pending_id}/approve",
    summary="Approve pending sim trade",
)
def approve_pending_trade(
    pending_id: int,
    body: PendingSimTradeReviewRequest = None,
):
    """批准待审批交易。"""
    sim_service = SimTradingService()
    repo = sim_service.repo
    try:
        pending = repo.get_pending_sim_trade(pending_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="Pending trade not found")
        if pending.status != "pending":
            raise HTTPException(status_code=400, detail=f"Trade already {pending.status}")

        reviewer_note = body.reviewer_note if body else ""
        result = sim_service.execute_pending_trade(pending_id, reviewer_note or "")
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("message", "Execute failed"))
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Approve pending trade failed", exc)


@router.post(
    "/sim-trades/{pending_id}/reject",
    summary="Reject pending sim trade",
)
def reject_pending_trade(
    pending_id: int,
    body: PendingSimTradeReviewRequest = None,
):
    """拒绝待审批交易。"""
    service = PortfolioService()
    repo = service.repo
    try:
        pending = repo.get_pending_sim_trade(pending_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="Pending trade not found")
        if pending.status != "pending":
            raise HTTPException(status_code=400, detail=f"Trade already {pending.status}")

        reviewer_note = body.reviewer_note if body else ""
        success = repo.update_pending_sim_trade_status(pending_id, "rejected", reviewer_note or "")
        if not success:
            raise HTTPException(status_code=500, detail="Failed to reject")
        return {"status": "rejected"}
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Reject pending trade failed", exc)


@router.delete(
    "/sim-trades/{pending_id}",
    summary="Delete pending sim trade",
)
def delete_pending_trade(pending_id: int):
    """删除待审批交易记录。"""
    service = PortfolioService()
    repo = service.repo
    try:
        success = repo.delete_pending_sim_trade(pending_id)
        if not success:
            raise HTTPException(status_code=404, detail="Pending trade not found")
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Delete pending trade failed", exc)


# ------------------------------------------------------------------
# SimTrading 配置端点
# ------------------------------------------------------------------


@router.get(
    "/sim-trading/config",
    summary="Get sim trading config",
)
def get_sim_trading_config():
    """获取模拟交易配置。"""
    from src.config import get_config

    config = get_config()
    return {
        "approval_required": config.sim_trading_approval_required,
        "sim_trading_enabled": config.sim_trading_enabled,
        "sim_trading_account_id": config.sim_trading_account_id,
    }


@router.put(
    "/sim-trading/config",
    summary="Update sim trading config",
)
def update_sim_trading_config(body: SimTradingConfigUpdateRequest):
    """更新审批配置并持久化到 .env。"""
    from src.config import Config, get_config
    from src.core.config_manager import ConfigManager

    config = get_config()
    config.sim_trading_approval_required = body.approval_required

    # 持久化到 .env 文件
    try:
        config_mgr = ConfigManager()
        config_mgr.apply_updates(
            updates=[("SIM_TRADING_APPROVAL_REQUIRED", str(body.approval_required).lower())],
            sensitive_keys=set(),
            mask_token="",
        )
    except Exception as exc:
        logger.warning("持久化 .env 失败: %s", exc)

    return {
        "approval_required": config.sim_trading_approval_required,
        "sim_trading_enabled": config.sim_trading_enabled,
        "sim_trading_account_id": config.sim_trading_account_id,
    }
