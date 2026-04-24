# 持仓交易编辑 + 模拟交易审批开关 技术设计文档

> **文档编号**：SPEC-2026-0424-01
> **创建日期**：2026-04-24
> **状态**：已批准
> **关联文档**：[`docs/sim-trading.md`](../sim-trading.md)、[`docs/plan-portfolio-analysis-integration.md`](../plan-portfolio-analysis-integration.md)

---

## 1. 概述

### 1.1 目标

本设计引入两个相对独立的持仓模块增强功能：

1. **交易流水编辑**：支持编辑已有 `PortfolioTrade` 记录的 `quantity` / `price` / `fee` / `tax` / `note` 字段，编辑后自动重新回放事件流校验后续交易的 oversell 约束。
2. **模拟交易审批开关**：在 `--schedule` 自动模式下，为模拟交易增加手动审批环节。开启后，LLM 产出的交易决策不再自动执行，而是写入 `PendingSimTrade` 待审批表，由用户手动批准或拒绝后再执行入库。

### 1.2 范围

#### IN SCOPE

- 交易流水编辑（`PUT` API + 前端 Modal）
- 编辑后 oversell 重新校验
- `PendingSimTrade` 新表（ORM 模型 + Repository CRUD）
- 审批 API（approve / reject / list pending / delete pending）
- 审批开关配置项 `SIM_TRADING_APPROVAL_REQUIRED`
- `SimTradingService` 改造（分支到 pending 或直接执行）
- 前端变更：编辑弹窗、待审批 Tab、审批开关 Toggle

#### OUT OF SCOPE

- 编辑 `symbol` / `side` / `trade_date` 字段（涉及交易身份标识，影响面过大）
- API / bot 触发的模拟交易审批（当前仅约束 schedule 模式）
- 审批时使用实时价格（始终使用 LLM 原始建议价格）
- 通知渠道推送审批提醒
- 批量审批

---

## 2. 数据层

### 2.1 PortfolioTrade 表

现有 `PortfolioTrade` ORM 模型（`src/storage.py`）无结构变更。当前表定义：

```python
class PortfolioTrade(Base):
    __tablename__ = 'portfolio_trades'

    id         = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey('portfolio_accounts.id'), nullable=False, index=True)
    trade_uid  = Column(String(128))
    symbol     = Column(String(16), nullable=False, index=True)
    market     = Column(String(8), nullable=False, default='cn')
    currency   = Column(String(8), nullable=False, default='CNY')
    trade_date = Column(Date, nullable=False, index=True)
    side       = Column(String(8), nullable=False)  # buy/sell
    quantity   = Column(Float, nullable=False)
    price      = Column(Float, nullable=False)
    fee        = Column(Float, default=0.0)
    tax        = Column(Float, default=0.0)
    note       = Column(String(255))
    dedup_hash = Column(String(64), index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
```

本次变更仅在 Repository 层（`src/repositories/portfolio_repo.py`）新增 `update_trade()` 方法，不修改 ORM 模型。

### 2.2 新增 PendingSimTrade 表

在 `src/storage.py` 中新增 ORM 模型：

```python
class PendingSimTrade(Base):
    """待审批的模拟交易记录。

    当 SIM_TRADING_APPROVAL_REQUIRED=true 时，schedule 模式产出的
    LLM 交易决策不直接执行，而是暂存于此表等待用户审批。
    """
    __tablename__ = "pending_sim_trades"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    account_id    = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    symbol        = Column(String(16), nullable=False, index=True)
    side          = Column(String(8), nullable=False)   # buy / sell
    quantity      = Column(Float, nullable=False)
    price         = Column(Float, nullable=False)
    fee           = Column(Float, default=0.0)
    tax           = Column(Float, default=0.0)
    note          = Column(Text, default="")
    llm_reasoning = Column(Text, default="")            # LLM 决策原因
    status        = Column(String(16), default="pending", index=True)  # pending / approved / rejected
    created_at    = Column(DateTime, default=func.now())
    reviewed_at   = Column(DateTime, nullable=True)
    reviewer_note = Column(Text, default="")            # 审批备注

    __table_args__ = (
        Index("ix_pending_sim_trade_account_status", "account_id", "status"),
    )
```

**状态流转**：

```
pending ──approve──▶ approved（触发 execute_pending_trade → record_trade）
   │
   └──reject───▶ rejected（标记拒绝，不执行）
```

### 2.3 新增配置项

在 `src/config.py` 的 `Config` dataclass 中新增字段：

```python
sim_trading_approval_required: bool = False
```

初始化时读取环境变量：

```python
self.sim_trading_approval_required = parse_env_bool(
    "SIM_TRADING_APPROVAL_REQUIRED", default=False
)
```

同步更新 `.env.example`：

```env
# 模拟交易审批开关（默认 false，仅影响 schedule 模式）
SIM_TRADING_APPROVAL_REQUIRED=false
```

**向后兼容说明**：默认值为 `false`，不配置时行为与改造前完全一致。

---

## 3. 后端 API

### 3.1 交易编辑端点

```
PUT /api/v1/portfolio/trades/{trade_id}
```

**文件**：`api/v1/endpoints/portfolio.py`

#### 请求体

```json
{
  "quantity": 100,
  "price": 25.50,
  "fee": 5.0,
  "tax": 1.0,
  "note": "修正备注"
}
```

所有字段均为可选（Partial Update 模式），仅传入的字段会被更新。**不允许**修改 `symbol` / `side` / `trade_date` / `account_id`。

#### 响应体

成功（200）：

```json
{
  "trade": {
    "id": 42,
    "account_id": 1,
    "symbol": "600519",
    "market": "cn",
    "currency": "CNY",
    "trade_date": "2026-04-20",
    "side": "buy",
    "quantity": 100,
    "price": 25.50,
    "fee": 5.0,
    "tax": 1.0,
    "note": "修正备注",
    "created_at": "2026-04-20T10:00:00"
  }
}
```

Oversell 硬阻断（400）**[Pass 2 修订]**：

```json
{
  "error": "oversell",
  "message": "Oversell detected: 交易 #3（2026-04-20 卖出 AAPL 200股）导致 oversell（可用 100股）",
  "violations": [
    "交易 #3（2026-04-20 卖出 AAPL 200股）导致 oversell（可用 100股）"
  ]
}
```

#### 核心逻辑

1. 查询目标 `trade_id`，不存在则 404
2. 仅更新请求体中非 `null` 的字段
3. 编辑后调用 `portfolio_service.update_trade_event()` 重新回放该 `account_id` 下的全部事件流
4. 在回放过程中检测后续交易是否出现 oversell
5. **[Pass 2 修订]** 如有 oversell（持仓为负），返回 400 错误 + 违规详情，**拒绝保存**（硬阻断）
6. 无 oversell 时保存并触发持仓快照重算

#### Schema 定义

在 `api/v1/schemas/portfolio.py` 新增：

```python
class PortfolioTradeUpdateRequest(BaseModel):
    quantity: Optional[float] = Field(None, gt=0)
    price: Optional[float] = Field(None, gt=0)
    fee: Optional[float] = Field(None, ge=0)
    tax: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=255)

class PortfolioTradeUpdateResponse(BaseModel):
    """[Pass 2] 移除 validation_warnings，oversell 改为 400 硬阻断。"""
    trade: PortfolioTradeListItem
```

### 3.2 审批相关端点

**文件**：`api/v1/endpoints/portfolio.py`

#### 3.2.1 待审批列表

```
GET /api/v1/portfolio/sim-trades/pending
```

**查询参数**：
- `account_id`（可选）：过滤指定账户
- `page`（可选，默认 1）
- `page_size`（可选，默认 20）

**响应体**：

```json
{
  "items": [
    {
      "id": 1,
      "account_id": 1,
      "symbol": "600519",
      "side": "buy",
      "quantity": 100,
      "price": 1850.0,
      "fee": 5.0,
      "tax": 0.0,
      "note": "",
      "llm_reasoning": "贵州茅台近期量价配合良好，MACD 金叉...",
      "status": "pending",
      "created_at": "2026-04-24T09:30:00",
      "reviewed_at": null,
      "reviewer_note": ""
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

#### 3.2.2 批准

```
POST /api/v1/portfolio/sim-trades/{id}/approve
```

**请求体（可选）**：

```json
{
  "reviewer_note": "同意，符合策略"
}
```

**处理流程**：

1. 查询 `PendingSimTrade`，状态必须为 `pending`
2. 调用 `SimTradingService.execute_pending_trade()` → 复用 `validate_and_execute()` 的 oversell / 现金 / 手数校验
3. 校验通过 → `PortfolioService.record_trade()` 入库
4. 更新 `PendingSimTrade.status = "approved"`，写入 `reviewed_at` 和 `reviewer_note`
5. 返回执行结果

#### 3.2.3 拒绝

```
POST /api/v1/portfolio/sim-trades/{id}/reject
```

**请求体（可选）**：

```json
{
  "reviewer_note": "风险过高，不执行"
}
```

**处理流程**：

1. 查询 `PendingSimTrade`，状态必须为 `pending`
2. 更新 `status = "rejected"`，写入 `reviewed_at` 和 `reviewer_note`
3. 不执行任何交易

#### 3.2.4 删除

```
DELETE /api/v1/portfolio/sim-trades/{id}
```

直接删除 `PendingSimTrade` 记录（不限状态）。

#### Schema 定义

```python
class PendingSimTradeItem(BaseModel):
    id: int
    account_id: int
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    tax: float
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
```

### 3.3 配置端点

```
GET  /api/v1/portfolio/sim-trading/config
PUT  /api/v1/portfolio/sim-trading/config
```

#### GET 响应

```json
{
  "approval_required": false,
  "sim_trading_enabled": true,
  "sim_trading_account_id": 1
}
```

#### PUT 请求体

```json
{
  "approval_required": true
}
```

**处理逻辑**：

- 更新内存中的 `Config.sim_trading_approval_required` 值
- **[Pass 2 修订]** 使用 `ConfigManager.apply_updates()` 原子写入 `.env` 文件，服务重启后仍生效
- 返回更新后的完整配置

#### Schema 定义

```python
class SimTradingConfigResponse(BaseModel):
    approval_required: bool
    sim_trading_enabled: bool
    sim_trading_account_id: Optional[int] = None

class SimTradingConfigUpdateRequest(BaseModel):
    approval_required: bool
```

---

## 4. SimTradingService 改造

### 4.1 当前流程

```
分析结果
  → SimTradingService.run(analysis_results, is_scheduled=True)
    → _build_portfolio_context()   # 构建持仓快照
    → _call_llm()                  # LLM 组合审查
    → _parse_actions()             # 解析交易动作
    → validate_and_execute()       # 逐笔校验 + 执行
      → PortfolioService.record_trade()  # 写入 PortfolioTrade
    → 完成
```

### 4.2 改造后流程

```
分析结果
  → SimTradingService.run(analysis_results, is_scheduled=True)
    → _build_portfolio_context()
    → _call_llm()
    → _parse_actions()
    → check_approval_required()
      ├─ 审批关闭 → validate_and_execute() → record_trade() → 完成
      └─ 审批开启 → save_pending_trades() → 返回 pending 状态

                  [用户操作]
                  ├─ approve → execute_pending_trade()
                  │              → validate_and_execute() → record_trade()
                  └─ reject  → mark_rejected()
```

### 4.3 新增 / 变更方法

#### `check_approval_required() → bool`

```python
def check_approval_required(self) -> bool:
    """检查是否需要审批。仅读取 Config.sim_trading_approval_required。"""
    return self.config.sim_trading_approval_required
```

#### `save_pending_trades(actions, account_id) → List[int]`

```python
def save_pending_trades(
    self,
    actions: List[SimTradeAction],
    account_id: int,
) -> List[int]:
    """将 LLM 交易决策批量写入 PendingSimTrade 表。
    返回新建记录的 id 列表。
    """
```

- 逐条创建 `PendingSimTrade` 记录
- `status = "pending"`
- `llm_reasoning` 取自 `SimTradeAction.reason`
- `fee` / `tax` 使用默认佣金配置 `SIM_TRADING_DEFAULT_COMMISSION`

#### `execute_pending_trade(pending_id, reviewer_note) → Dict`

```python
def execute_pending_trade(
    self,
    pending_id: int,
    reviewer_note: str = "",
) -> Dict[str, Any]:
    """审批通过后执行单笔 pending trade。

    1. 查询 PendingSimTrade 记录
    2. 构造 SimTradeAction
    3. 获取最新持仓快照
    4. 调用 validate_and_execute() 复用全部校验逻辑
    5. 更新 PendingSimTrade 状态
    """
```

- 使用 LLM **原始建议价格**（`PendingSimTrade.price`），不查询实时行情
- 复用 `validate_and_execute()` 的 oversell / 现金余额 / A 股手数校验
- 校验通过 → `record_trade()` → `status = "approved"`
- 校验不通过 → 返回具体错误，状态保持 `pending`（不自动拒绝）

#### `run()` 方法改造

在 `run()` 方法的 `validate_and_execute()` 调用前插入分支：

```python
# 原来：
# result = self.validate_and_execute(actions, account_id, portfolio_ctx)

# 改造后：
if self.check_approval_required() and is_scheduled:
    pending_ids = self.save_pending_trades(actions, account_id)
    logger.info(f"审批模式：{len(pending_ids)} 笔交易已保存至待审批队列")
    return {
        "status": "pending_approval",
        "pending_count": len(pending_ids),
        "pending_ids": pending_ids,
    }
else:
    result = self.validate_and_execute(actions, account_id, portfolio_ctx)
```

### 4.4 关键行为约束

| 约束 | 说明 |
|------|------|
| 仅 schedule 模式受审批控制 | `is_scheduled=True` 时才检查审批开关；API / bot 触发的模拟交易不受影响 |
| 审批价格不变 | 使用 LLM 原始建议价格，不查询实时行情 |
| **[Pass 2]** 审批 trade_date 使用 created_at | `execute_pending_trade()` 使用 `PendingSimTrade.created_at` 日期，不使用审批时的 `date.today()` |
| 保留 LLM 推理 | `llm_reasoning` 字段完整保存 LLM 的决策理由，供审批参考 |
| 复用校验逻辑 | approve 时调用 `validate_and_execute()`，不跳过任何安全护栏 |
| **[Pass 2]** 幂等含 pending 表 | `_has_executed_today()` 同时查询 PendingSimTrade 表，当日有 pending/approved 记录也视为已执行 |
| **[Pass 2]** approve 事务一致性 | `record_trade` + `update_status` 在同一 `portfolio_write_session` 内完成 |

---

## 5. 前端变更

### 5.1 页面布局

当前 `PortfolioPage.tsx` 采用 section 分区布局（非 Tab 组件），各区块包括：持仓概览（KPI 卡片）、持仓明细表、风险集中度、交易建议、录入表单、交易/资金/公司行动流水表。

本次改造在现有布局中集成以下新组件，保持现有分区结构不变：

| 区域 | 变更 |
|------|------|
| **交易流水表** | 每行新增「编辑」操作按钮 |
| **新增待审批交易区块** | 在交易建议区块下方新增 section，仅在审批开关开启时渲染 |
| **页面设置区域** | 新增审批开关 Toggle 组件 |

### 5.2 交易编辑功能

#### TradeEditModal 组件

**文件**：`apps/dsa-web/src/components/portfolio/TradeEditModal.tsx`（新增）

- 点击交易流水表的「编辑」按钮（铅笔图标）触发
- Modal 表单字段：`quantity`、`price`、`fee`、`tax`、`note`
- 仅展示可编辑字段，`symbol` / `side` / `trade_date` 以只读文本显示提供上下文
- 调用 `PUT /api/v1/portfolio/trades/{trade_id}`
- 保存成功后：
  - 刷新交易流水列表 + 持仓快照
- **[Pass 2 修订]** oversell 硬阻断（400 错误）时使用 `InlineAlert` 组件展示违规详情，不使用 `alert()`

### 5.3 待审批交易区块

#### PendingTradesTab 组件

**文件**：`apps/dsa-web/src/components/portfolio/PendingTradesTab.tsx`（新增）

- 仅在 `approval_required === true` 时渲染
- 卡片 / 列表展示 `status === "pending"` 的记录
- 每项展示：
  - 股票代码、买卖方向、数量、价格
  - LLM 决策理由（`llm_reasoning`，Markdown 渲染或折叠展示）
  - 创建时间
- 操作按钮：✅ 批准 / ❌ 拒绝
  - 批准 / 拒绝可带 `reviewer_note`（可选输入框）
  - 操作后刷新列表
- Section 标题带 badge 显示待审批数量

### 5.4 审批开关

#### SimTradingToggle 组件

**文件**：`apps/dsa-web/src/components/portfolio/SimTradingToggle.tsx`（新增）

- 位置：持仓页面设置区域（账户选择器附近或页面顶部操作栏）
- UI：Toggle 开关 + 标签「模拟交易需要手动审批」
- 初始值从 `GET /api/v1/portfolio/sim-trading/config` 获取
- 切换时调用 `PUT /api/v1/portfolio/sim-trading/config`
- 关闭时隐藏待审批交易区块

### 5.5 API 调用层

**文件**：`apps/dsa-web/src/api/portfolio.ts`

新增方法：

```typescript
// 交易编辑
updateTrade(tradeId: number, data: TradeUpdateRequest): Promise<TradeUpdateResponse>

// 待审批交易
getPendingSimTrades(params?: PendingTradeListParams): Promise<PendingTradeListResponse>
approvePendingTrade(id: number, note?: string): Promise<void>
rejectPendingTrade(id: number, note?: string): Promise<void>
deletePendingTrade(id: number): Promise<void>

// 审批配置
getSimTradingConfig(): Promise<SimTradingConfigResponse>
updateSimTradingConfig(data: SimTradingConfigUpdateRequest): Promise<SimTradingConfigResponse>
```

### 5.6 类型定义

**文件**：`apps/dsa-web/src/types/portfolio.ts`

新增：

```typescript
interface TradeUpdateRequest {
  quantity?: number;
  price?: number;
  fee?: number;
  tax?: number;
  note?: string;
}

interface TradeUpdateResponse {
  trade: PortfolioTrade;
}

interface PendingSimTrade {
  id: number;
  accountId: number;
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  fee: number;
  tax: number;
  note: string;
  llmReasoning: string;
  status: 'pending' | 'approved' | 'rejected';
  createdAt: string;
  reviewedAt: string | null;
  reviewerNote: string;
}

interface SimTradingConfig {
  approvalRequired: boolean;
  simTradingEnabled: boolean;
  simTradingAccountId: number | null;
}
```

---

## 6. 文件影响清单

### 6.1 后端

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/storage.py` | **新增模型** | 新增 `PendingSimTrade` ORM 模型 |
| `src/config.py` | **新增字段** | 新增 `sim_trading_approval_required: bool` |
| `src/repositories/portfolio_repo.py` | **新增方法** | `update_trade()`；pending_sim_trade 的 CRUD 方法 |
| `src/services/portfolio_service.py` | **新增方法** | `update_trade_event()`（含 oversell 重校验逻辑） |
| `src/services/sim_trading_service.py` | **改造** | `run()` 分支；新增 `check_approval_required()` / `save_pending_trades()` / `execute_pending_trade()` |
| `api/v1/endpoints/portfolio.py` | **新增端点** | PUT trades / pending 审批端点 / config 端点 |
| `api/v1/schemas/portfolio.py` | **新增 schema** | `PortfolioTradeUpdateRequest/Response`、`PendingSimTrade*`、`SimTradingConfig*` |
| `.env.example` | **新增行** | `SIM_TRADING_APPROVAL_REQUIRED=false` |

### 6.2 前端

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `apps/dsa-web/src/types/portfolio.ts` | **新增类型** | `PendingSimTrade`、`TradeUpdateRequest/Response`、`SimTradingConfig` |
| `apps/dsa-web/src/api/portfolio.ts` | **新增方法** | `updateTrade`、pending API、config API |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | **调整** | 集成 `TradeEditModal`、`PendingTradesTab`、`SimTradingToggle` |
| `apps/dsa-web/src/components/portfolio/TradeEditModal.tsx` | **新增** | 交易编辑弹窗组件 |
| `apps/dsa-web/src/components/portfolio/PendingTradesTab.tsx` | **新增** | 待审批交易 section 组件 |
| `apps/dsa-web/src/components/portfolio/SimTradingToggle.tsx` | **新增** | 审批开关 Toggle 组件 |

### 6.3 文档与配置

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `docs/sim-trading.md` | **更新** | 新增审批开关配置项说明和审批模式文档 |
| `docs/CHANGELOG.md` | **更新** | `[Unreleased]` 追加条目 |

---

## 7. 设计决策记录

| # | 决策点 | 选择 | 备选 | 理由 |
|---|--------|------|------|------|
| D1 | 可编辑字段范围 | 仅 `quantity` / `price` / `fee` / `tax` / `note` | 全字段可编辑 | 不修改交易身份标识字段（`symbol` / `side` / `trade_date`），避免引入复杂的去重哈希重算和事件流重排 |
| D2 | 编辑后校验策略 | 重新回放 oversell 检查，**硬阻断**（返回 400） | warn-only / 忽略校验 | **[Pass 2 修订]** 防止数据不一致，编辑后出现 oversell 时拒绝保存并返回违规详情 |
| D3 | 审批开关作用范围 | 仅 `--schedule` 自动模拟交易 | 全部模拟交易 | 最小影响面，API / bot 触发的模拟交易不受影响，避免功能回退 |
| D4 | Pending 存储方案 | 新建 `PendingSimTrade` 独立表 | 在 `PortfolioTrade` 加 `status` 字段 | 职责分离，不污染已有交易流水表；pending 记录生命周期与正式交易不同 |
| D5 | 审批时使用的价格 | LLM 原始建议价格 | 审批时实时价格 | 简化实现，避免实时行情依赖；用户在审批时已能看到建议价格，可自行判断 |
| D6 | 前端集成方案 | 集成式（在现有 PortfolioPage 内新增 section） | 独立页面 | 改动最小，UX 连贯，与现有布局一致 |
| D7 | 配置持久化 | PUT config 端点写入 `.env` 文件（`ConfigManager.apply_updates()`） | 仅内存更新 | **[Pass 2 新增]** 服务重启后审批开关仍生效，复用现有原子写入机制 |
| D8 | Pending 写操作事务模式 | `portfolio_write_session()`（BEGIN IMMEDIATE） | 普通 session | **[Pass 2 新增]** 与现有 trade/cash_ledger 写入模式一致，防止并发写入冲突 |
| D9 | 审批执行 trade_date | 使用 `PendingSimTrade.created_at` 日期 | 使用审批时 `date.today()` | **[Pass 2 新增]** 保留 LLM 决策时的日期语义，避免审批延迟导致日期偏移 |
| D10 | 幂等检查范围 | 同时查询 PortfolioTrade + PendingSimTrade | 仅查询 PortfolioTrade | **[Pass 2 新增]** 审批模式下防止重复生成 pending 记录 |
| D11 | approve + update_status 事务 | 在同一 `portfolio_write_session` 内完成 | 分别独立事务 | **[Pass 2 新增]** 防止 record_trade 成功但 update_status 失败导致状态不一致 |

---

## 8. 序列图

### 8.1 交易编辑流程

```
用户            前端                     API                    Service                 Repository
 │               │                       │                       │                        │
 │──点击编辑────▶│                       │                       │                        │
 │               │──PUT /trades/{id}───▶│                       │                        │
 │               │                       │──update_trade_event()▶│                        │
 │               │                       │                       │──update_trade()───────▶│
 │               │                       │                       │◀─────────ok────────────│
 │               │                       │                       │──replay_events()──────▶│
 │               │                       │                       │◀─warnings/ok──────────│
 │               │◀──trade + warnings────│◀──────────────────────│                        │
 │◀──展示结果────│                       │                       │                        │
```

### 8.2 模拟交易审批流程

```
Schedule        SimTradingService        Repository             用户          API            Service
 │                    │                      │                    │             │                │
 │──run()───────────▶│                      │                    │             │                │
 │                    │──check_approval()──▶│                    │             │                │
 │                    │◀─true───────────────│                    │             │                │
 │                    │──save_pending()────▶│                    │             │                │
 │                    │◀─pending_ids────────│                    │             │                │
 │◀─pending_approval─│                      │                    │             │                │
 │                    │                      │                    │             │                │
 │                    │                      │    查看待审批列表──▶│             │                │
 │                    │                      │                    │──GET pending▶│                │
 │                    │                      │                    │◀─list────────│                │
 │                    │                      │                    │             │                │
 │                    │                      │    批准交易────────▶│             │                │
 │                    │                      │                    │──POST approve▶│               │
 │                    │                      │                    │              │──execute_pending()▶│
 │                    │                      │                    │              │   ──validate_and_execute()
 │                    │                      │                    │              │   ──record_trade()
 │                    │                      │                    │◀─result──────│◀──────────────│
 │                    │                      │                    │             │                │
```

---

## 9. 测试策略

### 9.1 后端单元测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_portfolio_trade_edit.py` | `update_trade()` 基本更新 / 部分字段更新 / 不存在的 trade_id / 非法值校验 |
| `tests/test_portfolio_trade_edit_oversell.py` | 编辑后 oversell 回放校验：正常 / 触发 warning / 多笔连锁影响 |
| `tests/test_sim_trading_approval.py` | `check_approval_required` / `save_pending_trades` / `execute_pending_trade` / 开关 on/off 分支 |
| `tests/test_pending_sim_trade_api.py` | 审批 API 端点：list / approve / reject / delete / 非法状态转换 |
| `tests/test_sim_trading_config_api.py` | 配置端点：GET / PUT / 值持久化 |

### 9.2 前端测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `TradeEditModal.test.tsx` | 表单渲染 / 字段校验 / 提交调用 / oversell 400 错误展示 InlineAlert / 无变更直接关闭 |
| `PendingTradesTab.test.tsx` | 列表渲染 / 批准操作 / 拒绝操作 / 空状态 / 加载失败错误展示 |
| `SimTradingToggle.test.tsx` | 开关初始状态 / 切换调用 / 更新失败错误展示 |

### 9.3 测试 Marker

- 所有后端新增测试使用 `@pytest.mark.unit`
- 不涉及网络调用，可在 `pytest -m "not network"` 下执行

---

## 10. 回滚方案

| 变更 | 回滚方式 |
|------|---------|
| `PendingSimTrade` 新表 | 删除表 `DROP TABLE IF EXISTS pending_sim_trades`（SQLAlchemy `Base.metadata` 不会自动删除未引用的表） |
| 配置项 `SIM_TRADING_APPROVAL_REQUIRED` | 删除环境变量或设为 `false`，服务重启后行为恢复原样 |
| `SimTradingService.run()` 分支 | 审批开关默认 `false`，不配置时走原路径，无需代码回滚 |
| 前端新组件 | 审批开关关闭时，待审批 section 不渲染；编辑按钮可通过 feature flag 控制 |
| API 新端点 | 新增端点不影响已有端点，无需回滚；如需移除可删除路由注册 |

---

## 11. 开放问题

> 以下问题在当前设计中已做出明确选择，但在实施过程中可能需要重新评估。

| # | 问题 | 当前决策 | 备注 |
|---|------|---------|------|
| Q1 | 审批过期策略 | 无过期，pending 永久保留 | 后续可考虑加 TTL 自动拒绝 |
| Q2 | 审批通过后价格偏离 | 不处理，使用原始价格 | 后续可增加价格偏离阈值告警 |
| Q3 | 多用户审批权限 | 不区分，任何用户均可审批 | 后续可结合 RBAC 扩展 |
| Q4 | 批量审批 | OUT OF SCOPE | 待审批量大时可作为后续迭代 |
