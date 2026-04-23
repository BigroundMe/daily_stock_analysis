# 持仓-分析集成实施计划

> 目标：让「模拟持仓」和「股票分析」两个功能深度联动，而非各自独立。分析应感知用户当前持仓状态，分析完成后应为持仓操作提供依据。

## 一、当前现状

| 功能 | 状态 | 关键文件 |
|---|---|---|
| 分析流水线 | 不感知任何持仓信息，LLM prompt 中无持仓上下文 | `src/core/pipeline.py`, `src/analyzer.py` |
| 分析结果 | 已输出 `position_advice.has_position/no_position`，但基于通用信号，不读取实际持仓 | `src/analyzer.py` (AnalysisResult) |
| 持仓服务 | 不引用分析结果，持仓页面不展示分析评分/建议 | `src/services/portfolio_service.py` |
| PortfolioAgent | 已定义但未被编排器注册启用 | `src/agent/agents/portfolio_agent.py` |
| 前端持仓页 | 仅展示 持仓数量/均价/现价/市值/盈亏，无分析评分列，无"触发分析"入口 | `apps/dsa-web/src/pages/PortfolioPage.tsx` |

## 二、实施阶段

### Phase 1：持仓页面展示分析评分（后端 + 前端，低复杂度）

#### 1.1 后端：新增「持仓增强」API 端点

**文件**：`api/v1/endpoints/portfolio.py`

新增 `GET /api/v1/portfolio/snapshot/enriched`：
- 调用现有 `portfolio_service.get_portfolio_snapshot()` 获取持仓快照
- 对每个 position 的 symbol，查询 `analysis_repo.get_list(code=symbol, limit=1)` 获取最近一次分析记录
- 将 `sentiment_score`、`operation_advice`、`trend_prediction`、`ideal_buy`、`stop_loss`、`take_profit`、`created_at` 附到 position 数据中
- 若无分析记录，对应字段为 null

**返回结构扩展**（每个 position 新增字段）：
```python
{
    # ... 原有字段 ...
    "symbol": "600519",
    "quantity": 100,
    "avg_cost": 1800.0,
    "last_price": 1850.0,
    # ... 新增字段 ...
    "latest_analysis": {
        "sentiment_score": 72,         # 0-100 评分
        "operation_advice": "持有",     # 操作建议
        "trend_prediction": "看多",     # 趋势预测
        "ideal_buy": 1750.0,           # 理想买入价
        "stop_loss": 1680.0,           # 止损价
        "take_profit": 2000.0,         # 止盈价
        "analyzed_at": "2026-04-23T10:30:00"  # 分析时间
    }
}
```

**涉及文件**：
- `api/v1/endpoints/portfolio.py` — 新增端点
- `api/v1/schemas/portfolio.py` — 新增 enriched 响应 schema
- `src/services/portfolio_service.py` — 新增 `enrich_positions_with_analysis()` 方法
- `src/repositories/analysis_repo.py` — 确认 `get_list(code, limit=1)` 可用

#### 1.2 前端：持仓表格增加分析列

**文件**：`apps/dsa-web/src/pages/PortfolioPage.tsx`

- 调用新增 API `GET /api/v1/portfolio/snapshot/enriched` 替代原有 `getSnapshot()`
- 持仓表格新增列：
  - **评分** — `sentiment_score`（0-100，带颜色编码：≥70 绿色、40-69 黄色、<40 红色）
  - **建议** — `operation_advice`（买入/加仓/持有/减仓/卖出/观望）
  - **分析时间** — `analyzed_at`（相对时间，如 "2小时前"）
  - **操作** — "分析" 按钮，点击触发对该股票的分析

**涉及文件**：
- `apps/dsa-web/src/api/portfolio.ts` — 新增 `getEnrichedSnapshot()` 方法
- `apps/dsa-web/src/types/portfolio.ts` — 扩展 `PortfolioPositionItem` 类型
- `apps/dsa-web/src/pages/PortfolioPage.tsx` — 表格列扩展 + 分析按钮

#### 1.3 前端：持仓页面"一键分析全部持仓"

- 在持仓页面头部增加「分析全部持仓」按钮
- 点击后提取所有 position 的 symbol，调用 `analysisApi.analyzeAsync({ stockCodes: [...] })`
- 复用现有 TaskPanel 展示分析进度
- 分析完成后自动刷新持仓快照（展示最新评分）

**涉及文件**：
- `apps/dsa-web/src/pages/PortfolioPage.tsx` — 新增按钮和逻辑

---

### Phase 2：分析时注入持仓上下文（后端核心，中复杂度）

#### 2.1 Pipeline 注入持仓信息

**文件**：`src/core/pipeline.py`

在 `analyze_stock()` 方法的 Step 6-7 之间（获取 DB 上下文之后、增强上下文之前），新增：

```python
# 查询用户是否持有该股票
portfolio_context = self._get_portfolio_context(code)
```

新增方法 `_get_portfolio_context(code: str) -> Optional[Dict]`：
- 调用 `PortfolioService.get_positions_by_symbol(code)` 获取所有账户中该股票的持仓
- 若无持仓返回 None
- 有持仓返回：
  ```python
  {
      "is_holding": True,
      "total_quantity": 200,           # 跨账户汇总
      "avg_cost": 1800.0,             # 加权平均成本
      "total_cost": 360000.0,
      "current_market_value": 370000.0,
      "unrealized_pnl": 10000.0,
      "unrealized_pnl_pct": 2.78,     # 盈亏百分比
      "holding_accounts": [            # 每个账户明细
          {"account_name": "主账户", "quantity": 100, "avg_cost": 1780.0},
          {"account_name": "备用", "quantity": 100, "avg_cost": 1820.0}
      ]
  }
  ```

#### 2.2 增强上下文添加持仓键

**文件**：`src/core/pipeline.py`

修改 `_enhance_context()` 签名，新增 `portfolio_context` 参数：
```python
def _enhance_context(self, context, realtime_quote, chip_data, trend_result, stock_name="",
                     fundamental_context=None, portfolio_context=None) -> Dict[str, Any]:
```

在方法内新增：
```python
if portfolio_context:
    context['portfolio'] = portfolio_context
```

#### 2.3 Prompt 组装添加持仓段落

**文件**：`src/analyzer.py`

修改 `_format_prompt()` 方法，在新闻上下文之前新增：

```python
# 持仓信息（如果用户持有该股票）
portfolio = context.get('portfolio')
if portfolio and portfolio.get('is_holding'):
    prompt_parts.append(f"\n## 当前持仓信息\n")
    prompt_parts.append(f"- 持仓数量: {portfolio['total_quantity']} 股\n")
    prompt_parts.append(f"- 持仓均价: {portfolio['avg_cost']:.2f}\n")
    prompt_parts.append(f"- 持仓市值: {portfolio['current_market_value']:.2f}\n")
    prompt_parts.append(f"- 未实现盈亏: {portfolio['unrealized_pnl']:.2f} ({portfolio['unrealized_pnl_pct']:+.2f}%)\n")
    prompt_parts.append(f"\n请在分析中考虑用户当前持仓成本和盈亏状况，给出针对性的操作建议。\n")
```

这样 LLM 就能基于用户实际持仓成本给出个性化建议（如"当前已浮盈 2.78%，建议在 XX 价位部分止盈"）。

#### 2.4 PortfolioService 新增查询方法

**文件**：`src/services/portfolio_service.py`

新增：
```python
def get_positions_by_symbol(self, symbol: str) -> List[Dict]:
    """查询所有活跃账户中指定股票的持仓"""
```

**涉及文件**：
- `src/repositories/portfolio_repo.py` — 可能需要新增 `get_positions_by_symbol()` 查询

---

### Phase 3：分析完成后生成持仓操作建议（后端 + 前端，中复杂度）

#### 3.1 后端：分析结果生成交易建议

**文件**：`src/services/portfolio_service.py`

新增 `generate_trade_suggestions(analysis_result: AnalysisResult, positions: List[Dict]) -> List[Dict]`：

基于分析结果和当前持仓，生成具体交易建议：

| 分析结论 + 持仓状态 | 建议操作 |
|---|---|
| operation_advice="买入" + 未持仓 | 建议买入，参考 ideal_buy 价位 |
| operation_advice="加仓" + 已持仓 | 建议加仓，参考 secondary_buy 价位 |
| operation_advice="减仓" + 已持仓 | 建议减仓 N%（基于 position_strategy） |
| operation_advice="卖出" + 已持仓 | 建议全部卖出 |
| operation_advice="持有" + 已持仓 | 无操作建议，但展示止损/止盈提醒 |
| operation_advice="观望" + 未持仓 | 无操作建议 |

返回结构：
```python
[
    {
        "symbol": "600519",
        "action": "reduce",           # buy/add/hold/reduce/sell/watch
        "quantity_suggestion": 50,     # 建议数量（整手）
        "price_reference": 1850.0,    # 参考价位
        "reason": "评分 72，趋势看多但接近阻力位，建议减仓 50% 锁定利润",
        "stop_loss": 1680.0,
        "take_profit": 2000.0,
        "confidence": "中",
        "is_actionable": True,        # 是否可直接操作（vs 仅建议）
    }
]
```

#### 3.2 后端：新增建议 API

**文件**：`api/v1/endpoints/portfolio.py`

新增 `GET /api/v1/portfolio/trade-suggestions?account_id=X`：
- 获取持仓快照
- 查询每个 position 的最近分析结果
- 调用 `generate_trade_suggestions()` 生成建议
- 返回建议列表

#### 3.3 前端：交易建议面板

**文件**：`apps/dsa-web/src/pages/PortfolioPage.tsx`

- 在持仓页面新增「交易建议」Tab 或侧边面板
- 展示每条建议：股票、操作、数量、价位、理由
- 每条建议提供「执行」按钮 → 预填充到现有的"记录交易"弹窗
- 「执行」只是预填充表单，用户仍需确认提交（不自动执行交易）

---

### Phase 4：启用 PortfolioAgent + AI 风险叠加（可选增强，高复杂度）

#### 4.1 启用 PortfolioAgent

**文件**：
- `src/agent/orchestrator.py` — 在标准流程末尾增加 portfolio 阶段
- `src/agent/factory.py` — 注册 PortfolioAgent
- `src/agent/agents/portfolio_agent.py` — 增加 `get_portfolio_snapshot` 工具调用

#### 4.2 风险服务叠加 AI 分析

**文件**：`src/services/portfolio_risk_service.py`

在 `get_risk_report()` 中：
- 查询每个 position 最近分析的 `risk_alerts`
- 将 AI 风险警报作为新维度加入风险报告
- 前端风险面板展示 AI 风险叠加结果

---

## 三、依赖关系与推荐顺序

```
Phase 1（展示层联通）→ Phase 2（分析层联通）→ Phase 3（建议生成）→ Phase 4（Agent 增强）
  │                      │                       │
  └─ 可独立交付           └─ 依赖 Phase 1          └─ 依赖 Phase 1+2
```

- **Phase 1** 和 **Phase 2** 可以并行开发（后端部分独立，前端 Phase 2 不涉及 UI 改动）
- **Phase 3** 依赖 Phase 1 的 enriched API 和 Phase 2 的持仓感知分析
- **Phase 4** 独立性较强，但建议在 Phase 1-3 稳定后再进行

## 四、影响评估

### 兼容性

- **API 兼容**：Phase 1 新增端点，不修改现有端点，完全向后兼容
- **Pipeline 兼容**：Phase 2 中 `portfolio_context` 参数默认 None，无持仓时行为不变
- **Prompt 兼容**：持仓信息段落仅在有持仓时注入，不影响无持仓用户
- **前端兼容**：新功能为增量 UI，不改动现有交互

### 数据模型

- **不新增 ORM 表**：通过 `stock_code` 天然关联现有 `analysis_history` 和 `portfolio_positions` 表
- **不修改现有表结构**

### 性能考虑

- Phase 1 enriched API：每个 position 查一次 analysis_repo，可用 batch 查询优化（`WHERE code IN (...)` 单次查询）
- Phase 2 Pipeline：每次分析新增一次 portfolio 查询，开销极小

### 风险点

| 风险 | 说明 | 缓解 |
|---|---|---|
| stock_code 标准化不一致 | portfolio 使用 canonical 码，analysis 使用原始码 | 使用 `canonical_stock_code()` 统一标准化 |
| 多账户持仓汇总逻辑 | 跨账户、跨币种的持仓需要合理汇总 | 使用已有的 `PortfolioService` 汇率转换逻辑 |
| LLM prompt 膨胀 | 持仓信息增加 token 消耗 | 控制注入信息量，仅注入关键字段 |
| Phase 3 自动建议准确性 | AI 建议仅供参考，不应被视为投资建议 | 前端明确标注"仅供参考"，操作需用户确认 |

## 五、验证计划

| Phase | 验证方式 |
|---|---|
| Phase 1 后端 | `python -m py_compile` + 新增 pytest 测试（mock portfolio_service + analysis_repo） |
| Phase 1 前端 | `npm run lint && npm run build` + 手动验证持仓页面新列展示 |
| Phase 2 | `./scripts/ci_gate.sh` + 验证有/无持仓两种场景的分析结果差异 |
| Phase 3 | 新增 pytest 测试验证 `generate_trade_suggestions()` 各种组合场景 |
| 全局 | `python scripts/check_ai_assets.py` |

## 六、文件变更清单

### Phase 1（约 8 个文件）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/services/portfolio_service.py` | 修改 | 新增 `enrich_positions_with_analysis()` |
| `api/v1/endpoints/portfolio.py` | 修改 | 新增 enriched 端点 |
| `api/v1/schemas/portfolio.py` | 修改 | 新增 enriched 响应 schema |
| `apps/dsa-web/src/api/portfolio.ts` | 修改 | 新增 `getEnrichedSnapshot()` |
| `apps/dsa-web/src/types/portfolio.ts` | 修改 | 扩展 position 类型 |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | 修改 | 表格列扩展 + 分析按钮 + 一键分析 |
| `tests/test_portfolio_enrichment.py` | 新增 | enrichment 逻辑测试 |
| `docs/CHANGELOG.md` | 修改 | 新增 changelog 条目 |

### Phase 2（约 5 个文件）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/core/pipeline.py` | 修改 | `analyze_stock()` + `_enhance_context()` 注入持仓 |
| `src/analyzer.py` | 修改 | `_format_prompt()` 新增持仓段落 |
| `src/services/portfolio_service.py` | 修改 | 新增 `get_positions_by_symbol()` |
| `src/repositories/portfolio_repo.py` | 修改 | 新增按 symbol 查询 |
| `tests/test_pipeline_portfolio.py` | 新增 | 持仓上下文注入测试 |

### Phase 3（约 6 个文件）

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/services/portfolio_service.py` | 修改 | 新增 `generate_trade_suggestions()` |
| `api/v1/endpoints/portfolio.py` | 修改 | 新增 trade-suggestions 端点 |
| `api/v1/schemas/portfolio.py` | 修改 | 新增建议 schema |
| `apps/dsa-web/src/api/portfolio.ts` | 修改 | 新增 `getTradeSuggestions()` |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | 修改 | 交易建议面板 |
| `tests/test_trade_suggestions.py` | 新增 | 建议生成测试 |
