import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PortfolioTradeListItem } from '../../types/portfolio';

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    updateTrade: vi.fn(),
  },
}));

// createPortal 需要 mock，否则 jsdom 下无法正确渲染 portal
vi.mock('react-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-dom')>();
  return {
    ...actual,
    createPortal: (node: React.ReactNode) => node,
  };
});

import TradeEditModal from './TradeEditModal';
import { portfolioApi } from '../../api/portfolio';
import axios from 'axios';

const mockUpdateTrade = vi.mocked(portfolioApi.updateTrade);

const baseTrade: PortfolioTradeListItem = {
  id: 42,
  accountId: 1,
  symbol: '600519',
  market: 'cn',
  currency: 'CNY',
  tradeDate: '2026-04-20',
  side: 'buy',
  quantity: 100,
  price: 1800,
  fee: 5,
  tax: 2,
  note: '测试备注',
};

describe('TradeEditModal', () => {
  const onClose = vi.fn();
  const onSave = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderModal(overrides?: Partial<PortfolioTradeListItem>, isOpen = true) {
    const trade = { ...baseTrade, ...overrides };
    return render(
      <TradeEditModal trade={trade} isOpen={isOpen} onClose={onClose} onSave={onSave} />,
    );
  }

  // ── 渲染 ──

  it('不渲染 modal 当 isOpen=false', () => {
    const { container } = renderModal({}, false);
    expect(container).toBeEmptyDOMElement();
  });

  it('显示只读字段：标的、方向、日期', () => {
    renderModal();
    expect(screen.getByText('600519')).toBeInTheDocument();
    expect(screen.getByText('买入')).toBeInTheDocument();
    expect(screen.getByText('2026-04-20')).toBeInTheDocument();
  });

  it('显示可编辑字段并预填充 trade 数据', () => {
    renderModal();
    expect(screen.getByLabelText('数量')).toHaveValue(100);
    expect(screen.getByLabelText('价格')).toHaveValue(1800);
    expect(screen.getByLabelText('手续费')).toHaveValue(5);
    expect(screen.getByLabelText('税费')).toHaveValue(2);
    expect(screen.getByLabelText('备注')).toHaveValue('测试备注');
  });

  // ── 交互：修改字段 ──

  it('允许修改数量字段', () => {
    renderModal();
    const quantityInput = screen.getByLabelText('数量');
    fireEvent.change(quantityInput, { target: { value: '200' } });
    expect(quantityInput).toHaveValue(200);
  });

  // ── 成功提交 ──

  it('提交后调用 updateTrade 并触发 onSave', async () => {
    mockUpdateTrade.mockResolvedValueOnce({
      trade: { ...baseTrade, quantity: 200 },
      oversellViolations: [],
    });

    renderModal();
    fireEvent.change(screen.getByLabelText('数量'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => {
      expect(mockUpdateTrade).toHaveBeenCalledWith(42, expect.objectContaining({
        quantity: 200,
        price: 1800,
        fee: 5,
        tax: 2,
        note: '测试备注',
      }));
    });

    await waitFor(() => {
      expect(onSave).toHaveBeenCalled();
    });
  });

  // ── 客户端校验 ──

  it('数量为 0 时显示校验错误', async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText('数量'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('数量必须为正数');
    });
    expect(mockUpdateTrade).not.toHaveBeenCalled();
  });

  // ── 错误处理：400 oversell ──

  it('400 响应显示 oversell 错误信息', async () => {
    const axiosError = new axios.AxiosError(
      'Bad Request',
      '400',
      undefined,
      undefined,
      {
        status: 400,
        data: { detail: '修改后将导致卖出数量超出持仓，请检查数量' },
        statusText: 'Bad Request',
        headers: {},
        config: {} as import('axios').InternalAxiosRequestConfig,
      },
    );

    mockUpdateTrade.mockRejectedValueOnce(axiosError);

    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('修改后将导致卖出数量超出持仓');
    });
  });

  // ── 错误处理：409 数据库忙 ──

  it('409 响应显示忙碌提示', async () => {
    const axiosError = new axios.AxiosError(
      'Conflict',
      '409',
      undefined,
      undefined,
      {
        status: 409,
        data: { detail: 'Database is locked' },
        statusText: 'Conflict',
        headers: {},
        config: {} as import('axios').InternalAxiosRequestConfig,
      },
    );

    mockUpdateTrade.mockRejectedValueOnce(axiosError);

    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /保存/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('数据库忙，请稍后重试');
    });
  });

  // ── Escape 关闭 ──

  it('按 Escape 调用 onClose', () => {
    renderModal();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
