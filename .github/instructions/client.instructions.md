---
description: "Use when working on Web frontend (React/Vite/Tailwind) or Electron desktop app. Covers component patterns, state management, API layer, and build pipeline."
applyTo: "apps/dsa-web/**,apps/dsa-desktop/**,scripts/run-desktop.ps1,scripts/build-desktop*.ps1,scripts/build-*.sh,docs/desktop-package.md"
---

# Client Instructions

## 核心原则

- 保持现有 Vite + React Web 结构和 Electron 桌面端运行时假设，复用当前 API/状态模式。
- 修改 API 字段、认证状态、路由行为、Markdown/图表渲染、本地后端启动或报告载荷时，需评估 Web 和 Desktop 双端兼容性。
- Web 验证：`cd apps/dsa-web && npm ci && npm run lint && npm run build`
- Desktop 验证：先构建 Web，再构建 Desktop；若平台限制无法完整验证 Electron，明确说明风险。

## Web 前端架构 (apps/dsa-web/)

### 技术栈

React 19 + TypeScript strict + Vite 7 + Tailwind CSS 4 + Zustand + React Router v7 + Recharts + React Markdown

### 目录组织

```
src/
├── api/           # axios 实例 + 按资源拆分（analysis.ts, auth.ts, stocks.ts 等）
├── components/    # 按功能分目录
│   ├── common/    # 通用原子 UI 组件（Button, Card, Input, Loading 等）
│   ├── dashboard/ # 仪表盘组件
│   ├── history/   # 历史记录组件
│   ├── layout/    # Shell, ShellHeader, SidebarNav
│   ├── report/    # 报告展示组件
│   ├── settings/  # 设置页组件
│   └── theme/     # 主题切换
├── contexts/      # AuthContext（认证状态）
├── hooks/         # 自定义 hooks
├── pages/         # 页面组件（XxxPage.tsx）
├── stores/        # Zustand store（analysisStore, agentChatStore, stockPoolStore）
├── types/         # TypeScript 类型定义
└── utils/         # 工具函数（cn.ts, format.ts, markdown.ts 等）
```

### 编码约定

- **TypeScript**：strict 模式，`noUnusedLocals` / `noUnusedParameters` / `noFallthroughCasesInSwitch`
- **组件命名**：PascalCase 文件名（`HomePage.tsx`），函数组件，`export default`
- **样式**：Tailwind CSS 4 + `cn()` 工具函数（`clsx` + `tailwind-merge`），支持亮/暗色主题
- **Props 类型**：接口定义（如 `ButtonProps extends React.ButtonHTMLAttributes`），使用 `import type`
- **状态管理**：Zustand（`stores/xxxStore.ts`），认证用 React Context（`AuthContext`）
- **路由**：React Router v7，`App.tsx` 集中定义，`Shell` 作为布局包裹组件

### API 层

- 基于 `axios` 实例，`baseURL` 读取 `VITE_API_URL`（默认同源），`withCredentials: true`
- 全局 401 拦截自动跳转登录页
- 按后端资源拆分文件（`api/analysis.ts` 对应后端 `/api/v1/analysis`）
- 使用 `camelcase-keys` 做 snake → camel 自动转换
- 流式请求使用原生 `fetch` + SSE

### Vite 配置

- 开发代理：`/api` → `http://127.0.0.1:8000`
- 构建产物输出到项目根 `static/` 目录
- 启用 React Compiler（`babel-plugin-react-compiler`）

## Electron 桌面端 (apps/dsa-desktop/)

### 架构

```
apps/dsa-desktop/
├── main.js         # 主进程：后端启动、端口发现、窗口管理
├── preload.js      # 预加载桥接（window.dsaDesktop.version）
├── installer.nsh   # NSIS 自定义安装脚本
└── renderer/
    └── loading.html  # 后端启动时的加载页
```

### 核心流程

1. **后端自动启动**：`child_process.spawn` 启动打包的 Python 后端
2. **端口发现**：`findAvailablePort(8000, 8100)` 自动扫描可用端口
3. **健康检查**：轮询 `/health`，超时 60s，间隔 250ms
4. **窗口管理**：加载 Web 前端，支持亮/暗色动态背景

### 打包配置

- `electron-builder`：Windows NSIS / macOS DMG
- `extraResources`：打包后端二进制（`dist/backend/stock_analysis`）和 `.env.example`
- 桌面端版本与 Web 前端独立维护
