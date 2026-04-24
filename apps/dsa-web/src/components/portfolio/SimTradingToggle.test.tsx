import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SimTradingConfig } from '../../types/portfolio';

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    getSimTradingConfig: vi.fn(),
    updateSimTradingConfig: vi.fn(),
  },
}));

vi.mock('../../api/error', () => ({
  getParsedApiError: (err: unknown) => ({
    message: err instanceof Error ? err.message : '未知错误',
  }),
}));

import SimTradingToggle from './SimTradingToggle';
import { portfolioApi } from '../../api/portfolio';

const mockGetConfig = vi.mocked(portfolioApi.getSimTradingConfig);
const mockUpdateConfig = vi.mocked(portfolioApi.updateSimTradingConfig);

const enabledConfig: SimTradingConfig = {
  approvalRequired: false,
  simTradingEnabled: true,
  simTradingAccountId: 1,
};

const disabledConfig: SimTradingConfig = {
  approvalRequired: false,
  simTradingEnabled: false,
  simTradingAccountId: null,
};

describe('SimTradingToggle', () => {
  const onChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── 初始化：加载配置、显示 Toggle ──

  it('加载配置后显示 Toggle 开关', async () => {
    mockGetConfig.mockResolvedValueOnce(enabledConfig);

    render(<SimTradingToggle onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByRole('switch')).toBeInTheDocument();
    });

    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('模拟交易需要手动审批')).toBeInTheDocument();
  });

  it('approvalRequired=true 时 Toggle 为开启状态', async () => {
    mockGetConfig.mockResolvedValueOnce({ ...enabledConfig, approvalRequired: true });

    render(<SimTradingToggle onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
    });
  });

  // ── 切换：点击 Toggle 调用 updateSimTradingConfig ──

  it('点击 Toggle 调用 updateSimTradingConfig 并回调 onChange', async () => {
    mockGetConfig.mockResolvedValueOnce(enabledConfig);
    mockUpdateConfig.mockResolvedValueOnce({ ...enabledConfig, approvalRequired: true });

    render(<SimTradingToggle onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByRole('switch')).not.toBeDisabled();
    });

    fireEvent.click(screen.getByRole('switch'));

    await waitFor(() => {
      expect(mockUpdateConfig).toHaveBeenCalledWith({ approvalRequired: true });
    });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(true);
    });
  });

  // ── 禁用态 ──

  it('sim_trading_enabled=false 时 Toggle 禁用', async () => {
    mockGetConfig.mockResolvedValueOnce(disabledConfig);

    render(<SimTradingToggle onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByRole('switch')).toBeDisabled();
    });

    expect(screen.getByText('模拟交易功能未启用，审批开关不可操作')).toBeInTheDocument();
  });

  // ── 错误处理 ──

  it('加载配置失败显示错误提示', async () => {
    mockGetConfig.mockRejectedValueOnce(new Error('加载模拟交易配置失败'));

    render(<SimTradingToggle onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('加载模拟交易配置失败');
    });
  });

  it('更新配置失败显示错误提示', async () => {
    mockGetConfig.mockResolvedValueOnce(enabledConfig);
    mockUpdateConfig.mockRejectedValueOnce(new Error('更新模拟交易配置失败'));

    render(<SimTradingToggle onChange={onChange} />);

    await waitFor(() => {
      expect(screen.getByRole('switch')).not.toBeDisabled();
    });

    fireEvent.click(screen.getByRole('switch'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('更新模拟交易配置失败');
    });
  });
});
