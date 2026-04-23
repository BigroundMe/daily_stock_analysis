# Daily Stock Analysis — Copilot 项目指令

Canonical source: [`AGENTS.md`](../AGENTS.md).
If any instruction in this file conflicts with `AGENTS.md`, follow `AGENTS.md`.

## 项目概览

基于 AI 大模型的 A 股/港股/美股智能分析系统。核心链路：**数据抓取 → 技术分析/新闻/舆情 → LLM 分析 → 报告生成 → 多渠道通知**。

部署形态：GitHub Actions 定时任务 / Docker Compose / CLI / FastAPI Web 服务 / Electron 桌面端。

## 技术栈

| 端 | 语言 | 框架与核心依赖 |
|---|---|---|
| **后端** | Python 3.10+ | FastAPI, SQLAlchemy+SQLite, LiteLLM（多模型路由）, pandas/numpy, schedule |
| **数据源** | Python | efinance, akshare, tushare, pytdx, baostock, yfinance, longbridge（策略模式 fallback） |
| **搜索** | Python | tavily-python, google-search-results (SerpAPI), Bocha, Brave, SearXNG |
| **通知** | Python | 企业微信/飞书/Telegram/邮件/钉钉/Discord/Slack/Pushover/PushPlus/自定义Webhook/AstrBot |
| **Web 前端** | TypeScript | React 19, Vite 7, Tailwind CSS 4, Zustand, React Router v7, Recharts, React Markdown |
| **桌面端** | JavaScript | Electron 31, electron-builder（Win NSIS / macOS DMG） |
| **工具链** | — | Black+isort+flake8+Bandit (Python), ESLint+TypeScript strict (Web), Vitest+Playwright (Web 测试) |

## 目录结构与职责

| 目录 | 职责 |
|---|---|
| `main.py` | CLI 主入口（`--schedule` / `--serve` / `--market-review` / `--backtest` / `--stocks` / `--debug` / `--dry-run`） |
| `server.py` | FastAPI 服务入口，暴露给 uvicorn |
| `src/core/` | 核心编排层：`pipeline.py`（分析流水线）、`market_review.py`、`trading_calendar.py`、`backtest_engine.py` |
| `src/analyzer.py` | AI 分析层（LiteLLM 统一调用，内容完整性检查，占位符填充） |
| `src/stock_analyzer.py` | 趋势技术分析器（均线/量价/MACD/RSI，纯 pandas/numpy 计算） |
| `src/market_analyzer.py` | 大盘复盘分析器（指数行情、市场统计） |
| `src/services/` | 业务服务层（analysis_service, task_service, portfolio_service, backtest_service 等） |
| `src/repositories/` | 数据访问层（Repository 模式，封装 SQLAlchemy ORM） |
| `src/storage.py` | ORM 模型定义 + DatabaseManager 单例 |
| `src/config.py` | 全局配置 dataclass 单例（150+ 字段，从 `.env` 加载） |
| `src/notification.py` + `src/notification_sender/` | 多渠道通知（Mixin 多继承 11 个 Sender） |
| `src/agent/` | AI Agent 子系统（Technical/Intel/Risk/Decision/Portfolio Agent，多 Agent 协作） |
| `src/schemas/` | 数据结构 / JSON Schema 定义 |
| `data_provider/` | 数据源适配层（BaseFetcher 基类 + DataFetcherManager 策略管理器，8+ fetcher） |
| `api/` | FastAPI API 层（`/api/v1/`，10 个 endpoint 模块 + 对应 schema） |
| `bot/` | 机器人接入层（命令分发器 + 5 个平台适配器：钉钉/钉钉Stream/Discord/飞书Stream） |
| `apps/dsa-web/` | React SPA 前端（Vite 构建，组件按功能分目录，Zustand 状态管理） |
| `apps/dsa-desktop/` | Electron 桌面端（自动启动后端、端口发现、健康检查） |
| `strategies/` | YAML 策略文件（11 种内置策略，元数据 + Markdown instructions 混合格式） |
| `scripts/` | 本地脚本（ci_gate.sh, build 脚本, check_ai_assets.py） |
| `docker/` | Docker 部署（多阶段构建，双服务模式 analyzer+server） |
| `tests/` | pytest 测试（100+ 文件，unit/integration/network 三级 marker） |

## 架构要点

### 核心数据流

```
CLI/API/Bot 入口
    → StockAnalysisPipeline（ThreadPoolExecutor 并发）
        → DataFetcherManager（按优先级 fallback）→ 保存 SQLite
        → StockTrendAnalyzer（技术分析）
        → SearchService（新闻搜索）+ SocialSentimentService（舆情）
        → GeminiAnalyzer（LiteLLM → 多模型）→ AnalysisResult
    → 报告生成（Markdown/图片/飞书文档）
    → NotificationService（多渠道推送）
```

### 关键抽象

- **`BaseFetcher`**（`data_provider/base.py`）：数据源基类，模板方法 `_fetch_raw_data()` → `_normalize_data()`，标准列名 `STANDARD_COLUMNS`
- **`DataFetcherManager`**：策略管理器，per-fetcher `RLock` 线程安全，延迟初始化，自动故障切换
- **`Config`** dataclass：全局单例，`get_config()` 获取，热重载支持
- **`DatabaseManager`**：SQLAlchemy + SQLite 单例，断点续传（`has_today_data()`）
- **`NotificationService`**：Mixin 多继承组合 11 个 `{Platform}Sender`
- **`BotPlatform`** ABC（`bot/platforms/base.py`）：Webhook 平台统一接口
- **Agent 框架**（`src/agent/`）：Agent 工厂 + 编排器 + 工具注册表 + 技能路由

### 容错策略

- 数据源：按优先级自动 fallback，单源失败不阻断，内置 tenacity 指数退避 + 熔断器
- 通知：单通道失败 warning 并跳过，不中断主流程
- 搜索/舆情：初始化失败静默降级，不阻断分析主链路
- 配置：`parse_env_bool()`/`parse_env_int()`/`parse_env_float()` 带 fallback 默认值

## 编码规范

### Python

- **格式化**：Black（行长 120，目标 py310-312），isort（profile=black）
- **Lint**：flake8（行长 120，忽略 E501/W503/E203/E402）
- **安全**：Bandit（排除 tests，跳过 B101）
- **命名**：函数/变量 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`，私有 `_prefix`
- **模块头部**：`# -*- coding: utf-8 -*-` + 三引号 docstring（中文标题 + 「职责」列表）
- **日志**：`logger = logging.getLogger(__name__)`，禁止 `print()`
- **类型注解**：广泛使用 `typing` 模块，函数签名几乎全部带类型注解
- **Docstring**：Google 风格（`Args:` / `Returns:`），中英文混合
- **导入**：三段式（标准库 → 第三方 → 项目模块），项目模块在 isort `known_first_party` 中声明
- **Commit**：消息使用英文，不添加 `Co-Authored-By`

### TypeScript/React

- **TypeScript**：strict 模式，`noUnusedLocals`/`noUnusedParameters`
- **ESLint**：flat config (v9)，`react-hooks` + `react-refresh` 插件
- **组件**：PascalCase 文件名（`XxxPage.tsx`），函数组件，`export default`
- **样式**：Tailwind CSS 4 + `cn()` 工具函数（`clsx` + `tailwind-merge`）
- **状态**：Zustand store（`xxxStore.ts`），认证用 React Context（`AuthContext`）
- **API**：axios 实例 + `withCredentials: true`，按资源拆分（`api/analysis.ts` 等），snake→camel 自动转换
- **路由**：React Router v7，`App.tsx` 集中定义

## 测试约定

- **后端**：pytest，marker `unit`/`integration`/`network`，运行 `pytest -m "not network"` 跳过网络测试
- **前端**：Vitest（jsdom 环境）+ @testing-library/react，页面级测试在 `pages/__tests__/`
- **E2E**：Playwright（`npm run test:smoke`）
- **Mock**：Python 用 `unittest.mock`（patch/MagicMock），TypeScript 用 `vi.mock()`/`vi.fn()`
- **命名**：Python `test_*.py` / `test_*`，TypeScript `*.test.tsx`

## 构建与验证

```bash
# 后端验证（优先）
./scripts/ci_gate.sh                    # 四阶段门控：syntax → flake8 → deterministic → offline-tests
python -m py_compile <changed_files>    # 最低要求

# Web 前端验证
cd apps/dsa-web && npm ci && npm run lint && npm run build

# 桌面端验证
cd apps/dsa-desktop && npm install && npm run build   # 需先构建 Web

# AI 治理资产验证
python scripts/check_ai_assets.py

# 运行应用
python main.py                          # 默认分析
python main.py --serve                  # FastAPI 服务
python main.py --schedule               # 定时任务
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

## 模块指令索引

| 文件 | 覆盖范围 | 说明 |
|---|---|---|
| `.github/instructions/backend.instructions.md` | `main.py`, `server.py`, `src/**/*.py`, `data_provider/**/*.py`, `api/**/*.py`, `bot/**/*.py`, `tests/**/*.py`, `patch/**/*.py` | 后端编码规范、数据源、API、通知、Agent |
| `.github/instructions/client.instructions.md` | `apps/dsa-web/**`, `apps/dsa-desktop/**`, 桌面端脚本 | Web 前端架构、Electron 桌面端 |
| `.github/instructions/governance.instructions.md` | `README.md`, `docs/**`, `AGENTS.md`, `.github/**`, `scripts/**`, `docker/**` | CI/CD、发布、治理、Docker |
| `.github/instructions/testing.instructions.md` | `tests/**/*.py`, `apps/dsa-web/src/**/*.test.*`, `apps/dsa-web/e2e/**` | 测试策略、Mock 模式、覆盖层次 |
