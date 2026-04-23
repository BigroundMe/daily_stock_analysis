---
description: "Use when writing or modifying tests. Covers pytest conventions, Vitest patterns, mock strategies, test markers, and E2E testing."
applyTo: "tests/**/*.py,apps/dsa-web/src/**/*.test.*,apps/dsa-web/e2e/**"
---

# Testing Instructions

## Python 测试 (pytest)

### 配置

- 框架：pytest，配置在 `setup.cfg`
- 默认参数：`-v --tb=short`
- 文件命名：`test_*.py`，函数命名：`test_*`

### Marker 分层

| Marker | 含义 | 运行方式 |
|---|---|---|
| `unit` | 快速离线，无外部依赖 | `pytest -m unit` |
| `integration` | 服务级，无网络依赖 | `pytest -m integration` |
| `network` | 需外部网络/三方服务 | `pytest -m network`（CI 默认跳过） |

离线验证：`pytest -m "not network"`

### Mock 策略

- 统一使用 `unittest.mock`（`patch`、`MagicMock`、`mock.patch` 装饰器）
- 临时文件：`tempfile.TemporaryDirectory` + `setUp/tearDown` 清理
- 环境变量隔离：`os.environ` 的 patch/restore
- 可选依赖缺失：`sys.modules[name] = mock.MagicMock()` 占位
- HTTP 响应模拟：自定义 `_make_response` 工厂函数
- Pipeline 测试：`__new__` 跳过 `__init__` + 手动设置 mock 属性

### 测试数据

- 测试内联构造数据（如 `_make_config(**overrides)`）
- `.env` 文件通过 `tempfile` 临时创建
- 测试自包含，无全局 conftest.py fixture

### 典型测试类型

- 单元测试：验证 config 解析、数据标准化、工具函数
- 回归测试：验证异常传播路径（如 `test_pipeline_fetch_error.py`）
- 安全测试：验证密码哈希、会话、速率限制（如 `test_auth.py`）
- 集成测试：模拟 HTTP 请求验证通知发送

## Web 前端测试 (Vitest)

### 配置

- 框架：Vitest（Jest 兼容），配置在 `apps/dsa-web/vitest.config.ts`
- DOM 环境：jsdom
- 全局 API：`globals: true`
- Setup 文件：`./src/setupTests.ts`

### 测试组织

- 页面级测试：`pages/__tests__/XxxPage.test.tsx`
- 组件级测试：`components/{module}/__tests__/`
- Hook 测试：`hooks/__tests__/`
- Store 测试：`stores/__tests__/`
- 工具测试：`utils/__tests__/`

### Mock 策略

- 模块 mock：`vi.mock('../../api/analysis')`
- 函数 mock：`vi.fn()`
- 路由依赖：`MemoryRouter` 包装组件
- 使用 `@testing-library/react` 的 `render` / `screen` / `fireEvent`

### 运行命令

```bash
cd apps/dsa-web
npm run test           # Vitest 单元测试
npm run test:smoke     # Playwright E2E 测试
```

## E2E 测试 (Playwright)

- 配置在 `apps/dsa-web/playwright.config.ts`
- 测试文件在 `apps/dsa-web/e2e/`
- 排除在 Vitest 配置之外，独立运行
