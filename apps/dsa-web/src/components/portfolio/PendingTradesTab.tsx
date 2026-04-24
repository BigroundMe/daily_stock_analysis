import React, { useCallback, useEffect, useState } from 'react';
import { portfolioApi } from '../../api/portfolio';
import { Badge, Card, EmptyState, InlineAlert, Loading } from '../common';
import { cn } from '../../utils/cn';
import type { PendingSimTrade } from '../../types/portfolio';

interface PendingTradesTabProps {
  accountId: number;
}

const PendingTradesTab: React.FC<PendingTradesTabProps> = ({ accountId }) => {
  const [trades, setTrades] = useState<PendingSimTrade[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [reviewNotes, setReviewNotes] = useState<Record<number, string>>({});
  const [actionLoading, setActionLoading] = useState<Record<number, boolean>>({});

  const fetchPending = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await portfolioApi.getPendingSimTrades({ accountId });
      setTrades(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '获取待审批交易失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    fetchPending();
  }, [fetchPending]);

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleApprove = async (id: number) => {
    setActionLoading((prev) => ({ ...prev, [id]: true }));
    try {
      await portfolioApi.approvePendingTrade(id, reviewNotes[id] || undefined);
      await fetchPending();
      setReviewNotes((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '批准操作失败';
      setError(msg);
    } finally {
      setActionLoading((prev) => ({ ...prev, [id]: false }));
    }
  };

  const handleReject = async (id: number) => {
    setActionLoading((prev) => ({ ...prev, [id]: true }));
    try {
      await portfolioApi.rejectPendingTrade(id, reviewNotes[id] || undefined);
      await fetchPending();
      setReviewNotes((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '拒绝操作失败';
      setError(msg);
    } finally {
      setActionLoading((prev) => ({ ...prev, [id]: false }));
    }
  };

  const formatTime = (iso?: string | null) => {
    if (!iso) return '--';
    try {
      return new Date(iso).toLocaleString('zh-CN');
    } catch {
      return iso;
    }
  };

  return (
    <div className="space-y-4">
      {/* 标题 + 数量 badge */}
      <div className="flex items-center gap-2">
        <h3 className="text-lg font-semibold text-foreground">待审批交易</h3>
        <Badge variant={total > 0 ? 'warning' : 'default'} size="sm">
          {total}
        </Badge>
      </div>

      {/* 错误提示 */}
      {error && (
        <InlineAlert variant="danger" message={error} />
      )}

      {/* 加载中 */}
      {loading && <Loading label="加载待审批交易…" />}

      {/* 空状态 */}
      {!loading && !error && trades.length === 0 && (
        <EmptyState title="暂无待审批的模拟交易" />
      )}

      {/* 交易列表 */}
      {!loading && trades.length > 0 && (
        <div className="space-y-3">
          {trades.map((trade) => {
            const isBuy = trade.side === 'buy';
            const expanded = expandedIds.has(trade.id);
            const busy = actionLoading[trade.id] ?? false;

            return (
              <Card key={trade.id} padding="sm" className="space-y-3">
                {/* 主信息行 */}
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-mono text-base font-semibold text-foreground">
                    {trade.symbol}
                  </span>
                  <Badge variant={isBuy ? 'success' : 'danger'} size="sm">
                    {isBuy ? '买入' : '卖出'}
                  </Badge>
                  <span className="text-sm text-secondary-text">
                    数量 {trade.quantity} &times; ¥{trade.price.toFixed(2)}
                  </span>
                  <span className="ml-auto text-xs text-secondary-text">
                    {formatTime(trade.createdAt)}
                  </span>
                </div>

                {/* LLM 决策理由（折叠/展开） */}
                {trade.llmReasoning && (
                  <div>
                    <button
                      type="button"
                      onClick={() => toggleExpand(trade.id)}
                      className="flex items-center gap-1 text-xs text-cyan hover:underline"
                    >
                      <svg
                        className={cn(
                          'h-3.5 w-3.5 transition-transform duration-200',
                          expanded && 'rotate-90',
                        )}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      LLM 决策理由
                    </button>
                    {expanded && (
                      <div className="mt-2 rounded-lg border border-border/40 bg-elevated/50 p-3 text-sm text-secondary-text whitespace-pre-wrap">
                        {trade.llmReasoning}
                      </div>
                    )}
                  </div>
                )}

                {/* 审批备注输入 + 操作按钮 */}
                <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                  <input
                    type="text"
                    placeholder="审批备注（可选）"
                    value={reviewNotes[trade.id] ?? ''}
                    onChange={(e) =>
                      setReviewNotes((prev) => ({ ...prev, [trade.id]: e.target.value }))
                    }
                    className="input-surface input-focus-glow h-9 flex-1 rounded-lg border bg-transparent px-3 text-sm transition-all focus:outline-none disabled:opacity-60"
                    disabled={busy}
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => handleApprove(trade.id)}
                      disabled={busy}
                      className={cn(
                        'inline-flex h-9 items-center gap-1 rounded-lg border px-3 text-sm font-medium transition-colors',
                        'border-success/30 bg-success/10 text-success hover:bg-success/20',
                        'disabled:cursor-not-allowed disabled:opacity-50',
                      )}
                    >
                      ✅ 批准
                    </button>
                    <button
                      type="button"
                      onClick={() => handleReject(trade.id)}
                      disabled={busy}
                      className={cn(
                        'inline-flex h-9 items-center gap-1 rounded-lg border px-3 text-sm font-medium transition-colors',
                        'border-danger/30 bg-danger/10 text-danger hover:bg-danger/20',
                        'disabled:cursor-not-allowed disabled:opacity-50',
                      )}
                    >
                      ❌ 拒绝
                    </button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default PendingTradesTab;
