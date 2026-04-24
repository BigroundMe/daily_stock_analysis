import { useCallback, useEffect, useState } from 'react';
import { portfolioApi } from '../../api/portfolio';
import { getParsedApiError } from '../../api/error';
import { InlineAlert } from '../common';
import type { SimTradingConfig } from '../../types/portfolio';

interface SimTradingToggleProps {
  onChange?: (approvalRequired: boolean) => void;
}

/**
 * 模拟交易审批开关：加载配置 → 展示 Toggle → 切换时调用 API 更新
 */
function SimTradingToggle({ onChange }: SimTradingToggleProps) {
  const [config, setConfig] = useState<SimTradingConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    portfolioApi
      .getSimTradingConfig()
      .then((data) => {
        if (!cancelled) setConfig(data);
      })
      .catch((err) => {
        if (!cancelled) {
          const parsed = getParsedApiError(err);
          setError(parsed.message || '加载模拟交易配置失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleToggle = useCallback(async () => {
    if (!config || updating) return;
    const next = !config.approvalRequired;
    setUpdating(true);
    setError(null);
    try {
      const updated = await portfolioApi.updateSimTradingConfig({ approvalRequired: next });
      setConfig(updated);
      onChange?.(updated.approvalRequired);
    } catch (err) {
      const parsed = getParsedApiError(err);
      setError(parsed.message || '更新模拟交易配置失败');
    } finally {
      setUpdating(false);
    }
  }, [config, updating, onChange]);

  const simEnabled = config?.simTradingEnabled ?? false;
  const approvalOn = config?.approvalRequired ?? false;
  const disabled = loading || updating || !simEnabled;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3">
        <button
          type="button"
          role="switch"
          aria-checked={approvalOn}
          disabled={disabled}
          onClick={handleToggle}
          className={
            'relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ' +
            'focus:outline-none focus:ring-2 focus:ring-cyan/30 focus:ring-offset-2 focus:ring-offset-base ' +
            (disabled
              ? 'cursor-not-allowed opacity-50 bg-border/40'
              : approvalOn
                ? 'bg-cyan'
                : 'bg-border/60')
          }
        >
          <span
            className={
              'pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ' +
              (approvalOn ? 'translate-x-5' : 'translate-x-0.5')
            }
          />
        </button>
        <span className="select-none text-sm font-medium text-foreground">
          模拟交易需要手动审批
        </span>
        {updating && (
          <span className="text-xs text-muted-foreground animate-pulse">保存中…</span>
        )}
      </div>
      {!simEnabled && !loading && !error && (
        <p className="text-xs text-muted-foreground">
          模拟交易功能未启用，审批开关不可操作
        </p>
      )}
      {error && (
        <InlineAlert variant="danger" message={error} />
      )}
    </div>
  );
}

export default SimTradingToggle;
