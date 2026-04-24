import type React from 'react';
import { useState, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { InlineAlert } from '../common/InlineAlert';
import { Button } from '../common/Button';
import { portfolioApi } from '../../api/portfolio';
import type { PortfolioTradeListItem, TradeUpdateRequest } from '../../types/portfolio';
import axios from 'axios';

interface TradeEditModalProps {
  trade: PortfolioTradeListItem;
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void;
}

const SIDE_LABEL: Record<string, string> = { buy: '买入', sell: '卖出' };

function TradeEditModal({ trade, isOpen, onClose, onSave }: TradeEditModalProps) {
  const [quantity, setQuantity] = useState('');
  const [price, setPrice] = useState('');
  const [fee, setFee] = useState('');
  const [tax, setTax] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 同步 trade 数据到表单
  useEffect(() => {
    if (isOpen) {
      setQuantity(String(trade.quantity));
      setPrice(String(trade.price));
      setFee(String(trade.fee));
      setTax(String(trade.tax));
      setNote(trade.note ?? '');
      setError(null);
    }
  }, [isOpen, trade]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      return () => document.removeEventListener('keydown', handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const parsedQuantity = Number(quantity);
    const parsedPrice = Number(price);
    const parsedFee = Number(fee);
    const parsedTax = Number(tax);

    if (Number.isNaN(parsedQuantity) || parsedQuantity <= 0) {
      setError('数量必须为正数');
      return;
    }
    if (Number.isNaN(parsedPrice) || parsedPrice <= 0) {
      setError('价格必须为正数');
      return;
    }
    if (Number.isNaN(parsedFee) || parsedFee < 0) {
      setError('手续费不能为负数');
      return;
    }
    if (Number.isNaN(parsedTax) || parsedTax < 0) {
      setError('税费不能为负数');
      return;
    }

    const payload: TradeUpdateRequest = {
      quantity: parsedQuantity,
      price: parsedPrice,
      fee: parsedFee,
      tax: parsedTax,
      note: note.trim() || undefined,
    };

    setSaving(true);
    try {
      await portfolioApi.updateTrade(trade.id, payload);
      onSave();
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        if (status === 400) {
          const detail = err.response?.data?.detail;
          const message =
            typeof detail === 'string'
              ? detail
              : typeof detail === 'object' && detail !== null && 'message' in detail
                ? String((detail as Record<string, unknown>).message)
                : '修改后将导致卖出数量超出持仓，请检查数量';
          setError(message);
        } else if (status === 409) {
          setError('数据库忙，请稍后重试');
        } else {
          setError(err.response?.data?.detail?.message ?? err.message ?? '保存失败，请重试');
        }
      } else {
        setError('保存失败，请重试');
      }
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  const inputClass =
    'w-full rounded-lg border border-border/70 bg-surface-2 px-3 py-2 text-sm text-foreground placeholder:text-muted-text/50 focus:border-cyan/40 focus:outline-none focus:ring-2 focus:ring-cyan/15 transition-colors';
  const readonlyClass =
    'w-full rounded-lg border border-border/40 bg-muted/20 px-3 py-2 text-sm text-secondary-text';
  const labelClass = 'block text-xs font-medium text-secondary-text mb-1';

  const modal = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm transition-all"
      onClick={onClose}
    >
      <div
        className="mx-4 w-full max-w-md rounded-xl border border-border/70 bg-elevated p-6 shadow-2xl animate-in fade-in zoom-in duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-4 text-lg font-medium text-foreground">编辑交易记录</h3>

        {/* 只读上下文信息 */}
        <div className="mb-4 grid grid-cols-3 gap-3">
          <div>
            <span className={labelClass}>标的</span>
            <div className={readonlyClass}>{trade.symbol}</div>
          </div>
          <div>
            <span className={labelClass}>方向</span>
            <div className={readonlyClass}>{SIDE_LABEL[trade.side] ?? trade.side}</div>
          </div>
          <div>
            <span className={labelClass}>日期</span>
            <div className={readonlyClass}>{trade.tradeDate}</div>
          </div>
        </div>

        {error && (
          <InlineAlert variant="danger" message={error} className="mb-4" />
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="edit-quantity" className={labelClass}>数量</label>
              <input
                id="edit-quantity"
                type="number"
                step="any"
                min="0"
                className={inputClass}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                disabled={saving}
                required
              />
            </div>
            <div>
              <label htmlFor="edit-price" className={labelClass}>价格</label>
              <input
                id="edit-price"
                type="number"
                step="any"
                min="0"
                className={inputClass}
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                disabled={saving}
                required
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="edit-fee" className={labelClass}>手续费</label>
              <input
                id="edit-fee"
                type="number"
                step="any"
                min="0"
                className={inputClass}
                value={fee}
                onChange={(e) => setFee(e.target.value)}
                disabled={saving}
              />
            </div>
            <div>
              <label htmlFor="edit-tax" className={labelClass}>税费</label>
              <input
                id="edit-tax"
                type="number"
                step="any"
                min="0"
                className={inputClass}
                value={tax}
                onChange={(e) => setTax(e.target.value)}
                disabled={saving}
              />
            </div>
          </div>

          <div>
            <label htmlFor="edit-note" className={labelClass}>备注</label>
            <input
              id="edit-note"
              type="text"
              className={inputClass}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={saving}
              placeholder="可选"
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={onClose}
              disabled={saving}
            >
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              type="submit"
              isLoading={saving}
              loadingText="保存中..."
            >
              保存
            </Button>
          </div>
        </form>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}

export default TradeEditModal;
