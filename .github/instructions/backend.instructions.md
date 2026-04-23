---
description: "Use when working on Python backend code. Covers pipeline, data providers, API, bot, notifications, Agent framework, and coding conventions."
applyTo: "main.py,server.py,analyzer_service.py,webui.py,src/**/*.py,data_provider/**/*.py,api/**/*.py,bot/**/*.py,tests/**/*.py,patch/**/*.py"
---

# Backend Instructions

## 核心原则

- 保持现有流水线边界，复用已有的 services、repositories、schemas 和 fallback 逻辑，不新增平行实现。
- 修改 config、CLI 参数、调度语义、API 行为、认证或报告载荷时，必须同步 `.env.example` 并评估 Web/Desktop 兼容性。
- 单一数据源、通知渠道或可选集成的失败，不得中断主分析流程，除非需求明确要求 fail-fast。
- 验证优先使用 `./scripts/ci_gate.sh`；最低要求 `python -m py_compile` + 最近的确定性测试。

## 分层架构

```
入口 (main.py / server.py / bot/)
  → 编排层 (src/core/pipeline.py)
    → AI 分析层 (src/analyzer.py, src/stock_analyzer.py)
    → 搜索/舆情 (src/search_service.py, src/services/social_sentiment_service.py)
  → 服务层 (src/services/)
    → 仓库层 (src/repositories/)
      → 存储层 (src/storage.py) + 数据源层 (data_provider/)
  → 配置层 (src/config.py)
```

修改时遵循依赖方向，上层可依赖下层，下层不反向依赖上层。

## Python 编码约定

### 模块头部

```python
# -*- coding: utf-8 -*-
"""
===================================
模块标题（中文）
===================================

职责：
1. ...
2. ...
"""
```

### 命名

- 函数/变量：`snake_case`（如 `parse_env_bool`、`normalize_stock_code`）
- 类：`PascalCase`（如 `BaseFetcher`、`StockAnalysisPipeline`）
- 常量：`UPPER_SNAKE_CASE`（如 `STANDARD_COLUMNS`、`ETF_PREFIXES`）
- 私有：`_prefix`（如 `_is_us_market`、`_safe_float`）

### 日志

```python
import logging
logger = logging.getLogger(__name__)
```

禁止 `print()`。日志文案以清晰准确为准，中文为主。

### 类型注解

广泛使用 `typing` 模块（`Optional`、`Dict`、`List`、`Tuple`、`Callable`），函数签名带参数和返回类型注解。

### 导入顺序

三段式：标准库 → 第三方库 → 项目模块。项目模块已在 isort `known_first_party` 中声明。

### 错误处理

- 自定义异常层次：`DataFetchError` → `RateLimitError` / `DataSourceUnavailableError`
- `try/except` + `logger.warning` 降级，避免单点失败拖垮主流程
- 防御性解析：使用 `parse_env_bool()`/`parse_env_int()`/`parse_env_float()` 带 fallback

## 数据源 (data_provider/)

- 所有 fetcher 继承 `BaseFetcher`，实现 `_fetch_raw_data()` + `_normalize_data()`
- 输出列名统一使用 `STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']`
- 实时行情统一使用 `UnifiedRealtimeQuote` 数据结构
- 优先级数字越小越优先，`DataFetcherManager` 按优先级自动 fallback
- 内置 tenacity 指数退避 + 随机 Jitter + 熔断器
- 修改 fetcher 时必须保持：优先级顺序、标准化行为、超时/重试策略、优雅降级

## API (api/)

- 版本前缀 `/api/v1/`，资源名小写复数（`/analysis`、`/stocks`、`/history`）
- 每个 endpoint 独立文件（`api/v1/endpoints/xxx.py`），对应 schema 文件（`api/v1/schemas/xxx.py`）
- Schema 继承 `pydantic.BaseModel`，请求 `{Action}Request`，响应 `{Resource}Response`
- 每个 `Field()` 带 `description` 中文说明
- 通过 `router.include_router()` 聚合，`tags` 用于 OpenAPI 分组

## 通知发送器 (src/notification_sender/)

- 类名 `{Platform}Sender`，构造函数 `__init__(self, config: Config)`
- 主方法 `send_to_{platform}(self, content: str) -> bool`
- 配置检查 `_is_{platform}_configured() -> bool`
- 长内容使用 `chunk_content_by_max_bytes`/`chunk_content_by_max_words` 分批
- 配置不完整时 warning 并 return False，不抛异常

## Bot (bot/)

- Webhook 平台继承 `BotPlatform` ABC（`bot/platforms/base.py`），实现 `verify_request` / `parse_message` / `format_response`
- Stream 平台（钉钉 Stream / 飞书 Stream）有独立架构，不走 Webhook 分发
- 命令继承 `bot/commands/base.py` 基类

## Agent 子系统 (src/agent/)

- Agent 类型：TechnicalAgent、IntelAgent、RiskAgent、DecisionAgent、PortfolioAgent
- 工具注册通过 `tools/registry.py`
- 技能路由通过 `skills/router.py`
- 策略路由通过 `strategies/router.py`

## Config 字段规范 (src/config.py)

- 字段名 `snake_case`，与环境变量 `UPPER_SNAKE_CASE` 一一对应
- 可选单值 `Optional[str] = None`，列表 `List[str] = field(default_factory=list)`
- 新增字段必须同步 `.env.example` 对应条目
- 分组用 `# === 分组名 ===` 注释标识
