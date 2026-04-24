import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PendingSimTrade } from '../../types/portfolio';

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    getPendingSimTrades: vi.fn(),
    approvePendingTrade: vi.fn(),
    rejectPendingTrade: vi.fn(),
  },
}));

import PendingTradesTab from './PendingTradesTab';
import { portfolioApi } from '../../api/portfolio';

const mockGetPending = vi.mocked(portfolioApi.getPendingSimTrades);
const mockApprove = vi.mocked(portfolioApi.approvePendingTrade);
const mockReject = vi.mocked(portfolioApi.rejectPendingTrade);

const sampleTrade: PendingSimTrade = {
  id: 101,
  accountId: 1,
  symbol: '000001',
  side: 'buy',
  quantity: 500,
  price: 12.5,
  fee: 3,
  tax: 0,
  status: 'pending',
  createdAt: '2026-04-24T10:00:00Z',
  llmReasoning: 'AI 建议买入理由',
};

describe('PendingTradesTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── 加载态 ──

  it('初次渲染显示 loading', () => {
    mockGetPending.mockReturnValue(new Promise(() => {})); // 永不 resolve
    render(<PendingTradesTab accountId={1} />);
    expect(screen.getByText('加载待审批交易…')).toBeInTheDocument();
  });

  // ── 数据展示 ──

  it('显示 pending trades 列表', async () => {
    mockGetPending.mockResolvedValueOnce({
      items: [sampleTrade],
      total: 1,
      page: 1,
      pageSize: 20,
    });

    render(<PendingTradesTab accountId={1} />);

    await waitFor(() => {
      expect(screen.getByText('000001')).toBeInTheDocument();
    });

    expect(screen.getByText('买入')).toBeInTheDocument();
    // 数量和价格展示
    expect(screen.getByText(/500/)).toBeInTheDocument();
    expect(screen.getByText(/12\.50/)).toBeInTheDocument();
  });

  // ── 空状态 ──

  it('无数据时显示空状态提示', async () => {
    mockGetPending.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      pageSize: 20,
    });

    render(<PendingTradesTab accountId={1} />);

    await waitFor(() => {
      expect(screen.getByText('暂无待审批的模拟交易')).toBeInTheDocument();
    });
  });

  // ── 操作：批准 ──

  it('点击批准按钮调用 approvePendingTrade', async () => {
    mockGetPending
      .mockResolvedValueOnce({
        items: [sampleTrade],
        total: 1,
        page: 1,
        pageSize: 20,
      })
      .mockResolvedValueOnce({
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
      });

    mockApprove.mockResolvedValueOnce(undefined);

    render(<PendingTradesTab accountId={1} />);

    await waitFor(() => {
      expect(screen.getByText('000001')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /批准/ }));

    await waitFor(() => {
      expect(mockApprove).toHaveBeenCalledWith(101, undefined);
    });
  });

  // ── 操作：拒绝 ──

  it('点击拒绝按钮调用 rejectPendingTrade', async () => {
    mockGetPending
      .mockResolvedValueOnce({
        items: [sampleTrade],
        total: 1,
        page: 1,
        pageSize: 20,
      })
      .mockResolvedValueOnce({
        items: [],
        total: 0,
        page: 1,
        pageSize: 20,
      });

    mockReject.mockResolvedValueOnce(undefined);

    render(<PendingTradesTab accountId={1} />);

    await waitFor(() => {
      expect(screen.getByText('000001')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /拒绝/ }));

    await waitFor(() => {
      expect(mockReject).toHaveBeenCalledWith(101, undefined);
    });
  });

  // ── 错误处理 ──

  it('API 获取失败显示错误提示', async () => {
    mockGetPending.mockRejectedValueOnce(new Error('网络错误'));

    render(<PendingTradesTab accountId={1} />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('网络错误');
    });
  });

  it('批准操作失败显示错误提示', async () => {
    mockGetPending.mockResolvedValueOnce({
      items: [sampleTrade],
      total: 1,
      page: 1,
      pageSize: 20,
    });

    mockApprove.mockRejectedValueOnce(new Error('批准操作失败'));

    render(<PendingTradesTab accountId={1} />);

    await waitFor(() => {
      expect(screen.getByText('000001')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /批准/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('批准操作失败');
    });
  });
});
