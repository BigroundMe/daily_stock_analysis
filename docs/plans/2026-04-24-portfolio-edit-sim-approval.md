# 持仓交易编辑 + 模拟交易审批开关 实施计划

> **给 AI 智能体工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实施此计划。步骤使用复选框（`- [ ]`）语法进行跟踪。

**目标：** 为持仓模块新增交易流水编辑功能（含 oversell 硬阻断校验），以及模拟交易审批开关（schedule 模式下 LLM 交易决策进入待审批队列）。

**架构：** 遵循现有分层架构（ORM → Repository → Service → API → Frontend），两个功能在数据层和配置层独立，在 Service/API/前端层有交叉集成点。采用 TDD 红-绿-重构模式逐层构建。

**技术栈：** Python 3.10+ / SQLAlchemy / FastAPI / Pydantic / React 19 / TypeScript / Tailwind CSS 4 / Vitest

**Spec 文档：** [`docs/specs/2026-04-24-portfolio-edit-sim-approval-design.md`](../specs/2026-04-24-portfolio-edit-sim-approval-design.md)

**planning_pass：** 2（修订版，基于审查和批评反馈）

### 修订摘要（Pass 2）

| # | 类别 | 修订内容 |
|---|------|---------|
| B1 | 阻断 | 配置持久化：PUT config 端点同时写入 `.env` 文件，使用现有 `ConfigManager.apply_updates()` |
| B2 | 阻断 | Oversell 硬阻断：`_replay_oversell_check` 检测到负持仓时 raise `OversellError`，API 返回 400 |
| I3 | 重要 | 新增 Wave 5 前端测试任务（TradeEditModal / PendingTradesTab / SimTradingToggle） |
| I4 | 重要 | PendingSimTrade 写操作使用 `portfolio_write_session()` 获取 BEGIN IMMEDIATE 锁 |
| I5 | 重要 | `execute_pending_trade()` 的 trade_date 使用 `PendingSimTrade.created_at` 日期 |
| I6 | 重要 | `_has_executed_today()` 同时查询 PendingSimTrade 表 |
| I7 | 重要 | 前端 oversell 错误使用 `InlineAlert` 组件，不使用 `alert()` |
| I8 | 重要 | PendingTradesTab / SimTradingToggle 的 catch 块设置 error state |
| I9 | 重要 | Wave 6 文档更新增加 `docs/sim-trading.md` |
| I10 | 重要 | approve/reject 端点统一在 API 层先查询并检查 status == 'pending' |
| I11 | 重要 | `execute_pending_trade` 中 record_trade + update_status 在同一事务内完成 |

---

## 文件结构

### 创建的文件

| 文件 | 职责 |
|------|------|
| `tests/test_portfolio_trade_edit.py` | 交易编辑 Repository + Service 层单元测试 |
| `tests/test_sim_trading_approval.py` | 模拟交易审批 Service 层单元测试 |
| `tests/test_pending_sim_trade_api.py` | 审批 API 端点测试 |
| `tests/test_sim_trading_config_api.py` | 审批配置 API 测试 |
| `apps/dsa-web/src/components/portfolio/TradeEditModal.tsx` | 交易编辑弹窗组件 |
| `apps/dsa-web/src/components/portfolio/TradeEditModal.test.tsx` | 交易编辑弹窗组件测试 |
| `apps/dsa-web/src/components/portfolio/PendingTradesTab.tsx` | 待审批交易 section |
| `apps/dsa-web/src/components/portfolio/PendingTradesTab.test.tsx` | 待审批交易组件测试 |
| `apps/dsa-web/src/components/portfolio/SimTradingToggle.tsx` | 审批开关 Toggle |
| `apps/dsa-web/src/components/portfolio/SimTradingToggle.test.tsx` | 审批开关组件测试 |

### 修改的文件

| 文件 | 变更 |
|------|------|
| `src/storage.py` | 新增 `PendingSimTrade` ORM 模型 |
| `src/config.py` | 新增 `sim_trading_approval_required` 字段 |
| `.env.example` | 新增 `SIM_TRADING_APPROVAL_REQUIRED=false` |
| `src/repositories/portfolio_repo.py` | 新增 `update_trade_in_session()` + PendingSimTrade CRUD（写操作使用 `portfolio_write_session()`） |
| `src/services/portfolio_service.py` | 新增 `update_trade_event()`（oversell 硬阻断） |
| `src/services/sim_trading_service.py` | 改造 `run()` + 新增 `check_approval_required()` / `save_pending_trades()` / `execute_pending_trade()`；改造 `_has_executed_today()` 包含 pending 表查询 |
| `api/v1/endpoints/portfolio.py` | 新增 PUT trades + 审批端点 + 配置端点（持久化至 `.env`） |
| `api/v1/schemas/portfolio.py` | 新增 Schema 类 |
| `apps/dsa-web/src/types/portfolio.ts` | 新增 TypeScript 类型 |
| `apps/dsa-web/src/api/portfolio.ts` | 新增 API 调用方法 |
| `apps/dsa-web/src/pages/PortfolioPage.tsx` | 集成新组件（使用 InlineAlert 展示 oversell 错误） |
| `docs/CHANGELOG.md` | `[Unreleased]` 追加条目 |
| `docs/sim-trading.md` | 新增审批开关配置项说明 |

---

## Wave 1：数据层 + 配置（无依赖，可并行）

### 任务 1：新增 PendingSimTrade ORM 模型

**agent: gem-implementer**

**文件：**
- 修改：`src/storage.py`
- 测试：（通过任务 2 集成验证）

- [ ] **步骤 1：在 `src/storage.py` 中新增 `PendingSimTrade` ORM 模型**

在 `src/storage.py` 文件中，找到最后一个 ORM 模型定义后面（`PortfolioCorporateAction` 类之后），新增以下模型：

```python
class PendingSimTrade(Base):
    """待审批的模拟交易记录。

    当 SIM_TRADING_APPROVAL_REQUIRED=true 时，schedule 模式产出的
    LLM 交易决策不直接执行，而是暂存于此表等待用户审批。
    """
    __tablename__ = "pending_sim_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("portfolio_accounts.id"), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    side = Column(String(8), nullable=False)   # buy / sell
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    fee = Column(Float, default=0.0)
    tax = Column(Float, default=0.0)
    note = Column(Text, default="")
    llm_reasoning = Column(Text, default="")
    status = Column(String(16), default="pending", index=True)  # pending / approved / rejected
    created_at = Column(DateTime, default=func.now())
    reviewed_at = Column(DateTime, nullable=True)
    reviewer_note = Column(Text, default="")

    __table_args__ = (
        Index("ix_pending_sim_trade_account_status", "account_id", "status"),
    )
```

- [ ] **步骤 2：验证语法**

运行：`python -m py_compile src/storage.py`
预期：无输出（编译成功）

- [ ] **步骤 3：提交**

```bash
git add src/storage.py
git commit -m "feat(storage): add PendingSimTrade ORM model for sim-trading approval queue"
```

---

### 任务 2：新增配置项 `sim_trading_approval_required`

**agent: gem-implementer**

**文件：**
- 修改：`src/config.py`
- 修改：`.env.example`

- [ ] **步骤 1：在 `src/config.py` 的 `Config` dataclass 中新增字段**

找到 `sim_trading_fallback_models` 字段（约第 659 行），在其后添加：

```python
    sim_trading_approval_required: bool = False
```

- [ ] **步骤 2：在 `Config.__init__` 的初始化参数中新增解析**

找到 `sim_trading_fallback_models` 的初始化行（约第 1348 行），在其后添加：

```python
            sim_trading_approval_required=parse_env_bool(
                os.getenv('SIM_TRADING_APPROVAL_REQUIRED'), False
            ),
```

- [ ] **步骤 3：在 `.env.example` 中新增配置项**

在 `.env.example` 文件的模拟交易相关配置区域追加：

```env
# 模拟交易审批开关（默认 false，仅影响 schedule 模式）
SIM_TRADING_APPROVAL_REQUIRED=false
```

- [ ] **步骤 4：验证语法**

运行：`python -m py_compile src/config.py`
预期：无输出（编译成功）

- [ ] **步骤 5：提交**

```bash
git add src/config.py .env.example
git commit -m "feat(config): add SIM_TRADING_APPROVAL_REQUIRED config field"
```

---

## Wave 2：Repository 层（依赖 Wave 1 的 ORM 模型）

### 任务 3：Repository 层 — `update_trade_in_session()` 方法

**agent: gem-implementer**

**文件：**
- 创建：`tests/test_portfolio_trade_edit.py`
- 修改：`src/repositories/portfolio_repo.py`

- [ ] **步骤 1：编写 `update_trade_in_session` 的失败测试**

创建 `tests/test_portfolio_trade_edit.py`：

```python
# -*- coding: utf-8 -*-
"""交易流水编辑功能单元测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.config import Config
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager


class TradeEditRepositoryTestCase(unittest.TestCase):
    """Repository 层 update_trade 测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "edit_test.db"
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\nDATABASE_PATH={self.db_path}\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.service = PortfolioService(self.repo)
        # 创建测试账户
        self.account = self.repo.create_account(
            name="test", broker=None, market="cn", base_currency="CNY"
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key in ("ENV_FILE", "DATABASE_PATH"):
            os.environ.pop(key, None)
        self.temp_dir.cleanup()

    def _add_trade(self, symbol="600519", side="buy", quantity=100.0, price=1800.0):
        return self.service.record_trade(
            account_id=self.account.id,
            symbol=symbol,
            trade_date=date(2026, 4, 20),
            side=side,
            quantity=quantity,
            price=price,
            fee=5.0,
            tax=0.0,
        )

    def test_update_trade_full_fields(self):
        """更新全部可编辑字段。"""
        result = self._add_trade()
        trade_id = result["id"]

        with self.repo.portfolio_write_session() as session:
            updated = self.repo.update_trade_in_session(
                session=session,
                trade_id=trade_id,
                fields={"quantity": 200.0, "price": 1900.0, "fee": 10.0, "tax": 2.0, "note": "修正"},
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated.quantity, 200.0)
            self.assertEqual(updated.price, 1900.0)
            self.assertEqual(updated.fee, 10.0)
            self.assertEqual(updated.tax, 2.0)
            self.assertEqual(updated.note, "修正")

    def test_update_trade_partial_fields(self):
        """仅更新部分字段，其他字段不变。"""
        result = self._add_trade()
        trade_id = result["id"]

        with self.repo.portfolio_write_session() as session:
            updated = self.repo.update_trade_in_session(
                session=session,
                trade_id=trade_id,
                fields={"price": 2000.0},
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated.price, 2000.0)
            self.assertEqual(updated.quantity, 100.0)  # 未修改
            self.assertEqual(updated.fee, 5.0)  # 未修改

    def test_update_trade_not_found(self):
        """不存在的 trade_id 返回 None。"""
        with self.repo.portfolio_write_session() as session:
            updated = self.repo.update_trade_in_session(
                session=session,
                trade_id=99999,
                fields={"price": 2000.0},
            )
            self.assertIsNone(updated)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_portfolio_trade_edit.py -v -x`
预期：FAIL，`AttributeError: 'PortfolioRepository' object has no attribute 'update_trade_in_session'`

- [ ] **步骤 3：在 `src/repositories/portfolio_repo.py` 中实现 `update_trade_in_session()`**

在 `PortfolioRepository` 类中，找到 `delete_trade_in_session` 方法附近，添加：

```python
    TRADE_EDITABLE_FIELDS = {"quantity", "price", "fee", "tax", "note"}

    def update_trade_in_session(
        self,
        *,
        session: Any,
        trade_id: int,
        fields: Dict[str, Any],
    ) -> Optional[PortfolioTrade]:
        """更新交易记录的可编辑字段。

        Args:
            session: SQLAlchemy session（需在 portfolio_write_session 内）
            trade_id: 要更新的交易 ID
            fields: 要更新的字段字典，仅允许 quantity/price/fee/tax/note

        Returns:
            更新后的 PortfolioTrade 对象，不存在时返回 None
        """
        row = session.execute(
            select(PortfolioTrade).where(PortfolioTrade.id == trade_id).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None

        for key, value in fields.items():
            if key in self.TRADE_EDITABLE_FIELDS:
                setattr(row, key, value)

        self._invalidate_account_cache_in_session(
            session=session,
            account_id=row.account_id,
            from_date=row.trade_date,
        )
        session.flush()
        session.refresh(row)
        return row
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_portfolio_trade_edit.py -v -x`
预期：3 passed

- [ ] **步骤 5：提交**

```bash
git add src/repositories/portfolio_repo.py tests/test_portfolio_trade_edit.py
git commit -m "feat(repo): add update_trade_in_session for trade editing"
```

---

### 任务 4：Repository 层 — PendingSimTrade CRUD

**agent: gem-implementer**

**文件：**
- 修改：`src/repositories/portfolio_repo.py`
- 创建：`tests/test_sim_trading_approval.py`（仅 repo 部分）

- [ ] **步骤 1：编写 PendingSimTrade CRUD 的失败测试**

创建 `tests/test_sim_trading_approval.py`：

```python
# -*- coding: utf-8 -*-
"""模拟交易审批功能单元测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.config import Config
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager, PendingSimTrade


class PendingSimTradeRepoTestCase(unittest.TestCase):
    """PendingSimTrade Repository CRUD 测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "approval_test.db"
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\nDATABASE_PATH={self.db_path}\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.account = self.repo.create_account(
            name="test", broker=None, market="cn", base_currency="CNY"
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key in ("ENV_FILE", "DATABASE_PATH"):
            os.environ.pop(key, None)
        self.temp_dir.cleanup()

    def test_add_pending_sim_trade(self):
        """创建一条 pending 记录。"""
        row = self.repo.add_pending_sim_trade(
            account_id=self.account.id,
            symbol="600519",
            side="buy",
            quantity=100.0,
            price=1850.0,
            fee=5.0,
            tax=0.0,
            note="test",
            llm_reasoning="MACD 金叉",
        )
        self.assertIsNotNone(row.id)
        self.assertEqual(row.status, "pending")
        self.assertEqual(row.symbol, "600519")

    def test_list_pending_sim_trades(self):
        """查询 pending 列表，支持按 account_id 和 status 过滤。"""
        self.repo.add_pending_sim_trade(
            account_id=self.account.id, symbol="600519", side="buy",
            quantity=100.0, price=1850.0,
        )
        self.repo.add_pending_sim_trade(
            account_id=self.account.id, symbol="000001", side="sell",
            quantity=200.0, price=15.0,
        )
        rows, total = self.repo.list_pending_sim_trades(
            account_id=self.account.id, status="pending", page=1, page_size=20
        )
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)

    def test_get_pending_sim_trade(self):
        """按 ID 获取单条记录。"""
        row = self.repo.add_pending_sim_trade(
            account_id=self.account.id, symbol="600519", side="buy",
            quantity=100.0, price=1850.0,
        )
        found = self.repo.get_pending_sim_trade(row.id)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, row.id)

    def test_update_pending_sim_trade_status(self):
        """更新 pending 记录状态。"""
        row = self.repo.add_pending_sim_trade(
            account_id=self.account.id, symbol="600519", side="buy",
            quantity=100.0, price=1850.0,
        )
        updated = self.repo.update_pending_sim_trade_status(
            pending_id=row.id, status="approved", reviewer_note="ok"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, "approved")
        self.assertIsNotNone(updated.reviewed_at)
        self.assertEqual(updated.reviewer_note, "ok")

    def test_delete_pending_sim_trade(self):
        """删除 pending 记录。"""
        row = self.repo.add_pending_sim_trade(
            account_id=self.account.id, symbol="600519", side="buy",
            quantity=100.0, price=1850.0,
        )
        self.assertTrue(self.repo.delete_pending_sim_trade(row.id))
        self.assertIsNone(self.repo.get_pending_sim_trade(row.id))

    def test_delete_nonexistent_pending_sim_trade(self):
        """删除不存在的记录返回 False。"""
        self.assertFalse(self.repo.delete_pending_sim_trade(99999))
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_sim_trading_approval.py::PendingSimTradeRepoTestCase -v -x`
预期：FAIL，`AttributeError: 'PortfolioRepository' object has no attribute 'add_pending_sim_trade'`

- [ ] **步骤 3：在 `src/repositories/portfolio_repo.py` 中实现 PendingSimTrade CRUD**

> **[Pass 2 修订 I4]** 写操作（add/update_status/delete）使用 `portfolio_write_session()` 获取 BEGIN IMMEDIATE 事务锁，与现有 trade/cash_ledger 写入模式一致。读操作保持 `get_session()` 即可。

首先在文件顶部的 imports 中添加 `PendingSimTrade`：

```python
from src.storage import (
    DatabaseManager,
    PendingSimTrade,
    PortfolioAccount,
    # ... 其他已有的 imports
)
```

然后在 `PortfolioRepository` 类中添加方法：

```python
    # ------------------------------------------------------------------
    # PendingSimTrade CRUD
    # ------------------------------------------------------------------
    def add_pending_sim_trade(
        self,
        *,
        account_id: int,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        tax: float = 0.0,
        note: str = "",
        llm_reasoning: str = "",
    ) -> PendingSimTrade:
        with self.portfolio_write_session() as session:
            row = PendingSimTrade(
                account_id=account_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                fee=fee,
                tax=tax,
                note=note,
                llm_reasoning=llm_reasoning,
                status="pending",
            )
            session.add(row)
            session.flush()
            session.refresh(row)
            return row

    def get_pending_sim_trade(self, pending_id: int) -> Optional[PendingSimTrade]:
        with self.db.get_session() as session:
            return session.execute(
                select(PendingSimTrade).where(PendingSimTrade.id == pending_id).limit(1)
            ).scalar_one_or_none()

    def list_pending_sim_trades(
        self,
        *,
        account_id: Optional[int] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[PendingSimTrade], int]:
        with self.db.get_session() as session:
            conditions = []
            if account_id is not None:
                conditions.append(PendingSimTrade.account_id == account_id)
            if status is not None:
                conditions.append(PendingSimTrade.status == status)

            base_query = select(PendingSimTrade)
            if conditions:
                base_query = base_query.where(and_(*conditions))

            total = session.execute(
                select(func.count()).select_from(base_query.subquery())
            ).scalar() or 0

            rows = session.execute(
                base_query.order_by(desc(PendingSimTrade.created_at))
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars().all()

            return list(rows), int(total)

    def update_pending_sim_trade_status(
        self,
        *,
        pending_id: int,
        status: str,
        reviewer_note: str = "",
    ) -> Optional[PendingSimTrade]:
        with self.portfolio_write_session() as session:
            row = session.execute(
                select(PendingSimTrade).where(PendingSimTrade.id == pending_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = status
            row.reviewed_at = datetime.now()
            row.reviewer_note = reviewer_note
            session.flush()
            session.refresh(row)
            return row

    def update_pending_sim_trade_status_in_session(
        self,
        *,
        session: Any,
        pending_id: int,
        status: str,
        reviewer_note: str = "",
    ) -> Optional[PendingSimTrade]:
        """在已有的 write session 中更新 pending 状态（用于事务组合）。"""
        row = session.execute(
            select(PendingSimTrade).where(PendingSimTrade.id == pending_id).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = status
        row.reviewed_at = datetime.now()
        row.reviewer_note = reviewer_note
        session.flush()
        return row

    def delete_pending_sim_trade(self, pending_id: int) -> bool:
        with self.portfolio_write_session() as session:
            row = session.execute(
                select(PendingSimTrade).where(PendingSimTrade.id == pending_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return False
            session.delete(row)
            return True

    def has_pending_or_approved_today(self, account_id: int, target_date: date) -> bool:
        """检查指定日期是否已有 pending 或 approved 的模拟交易记录。"""
        with self.db.get_session() as session:
            from sqlalchemy import cast, Date as SADate
            count = session.execute(
                select(func.count())
                .select_from(PendingSimTrade)
                .where(
                    PendingSimTrade.account_id == account_id,
                    PendingSimTrade.status.in_(["pending", "approved"]),
                    cast(PendingSimTrade.created_at, SADate) == target_date,
                )
            ).scalar() or 0
            return count > 0
```

注意：
- 需要在文件顶部确保 `datetime`、`date` 已导入（已有）。
- `update_pending_sim_trade_status_in_session` 是新增的 session 内变体，供 `execute_pending_trade` 事务组合使用（**[I11]**）。
- `has_pending_or_approved_today` 是新增方法，供 `_has_executed_today` 幂等检查使用（**[I6]**）。

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_sim_trading_approval.py::PendingSimTradeRepoTestCase -v -x`
预期：6 passed

- [ ] **步骤 5：提交**

```bash
git add src/repositories/portfolio_repo.py tests/test_sim_trading_approval.py
git commit -m "feat(repo): add PendingSimTrade CRUD methods"
```

---

## Wave 3：Service 层（依赖 Wave 2 的 Repository）

### 任务 5：Service 层 — `update_trade_event()` 含 oversell 硬阻断

> **[Pass 2 修订 B2]** `_replay_oversell_check` 检测到负持仓时 raise `OversellError`，不再使用 warn-only 策略。API 层捕获该异常返回 400。

**agent: gem-implementer**

**文件：**
- 修改：`tests/test_portfolio_trade_edit.py`（追加 Service 层测试）
- 修改：`src/services/portfolio_service.py`

- [ ] **步骤 1：在 `tests/test_portfolio_trade_edit.py` 中追加 Service 层测试**

在文件末尾追加：

```python
class TradeEditServiceTestCase(unittest.TestCase):
    """Service 层 update_trade_event 测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "edit_svc_test.db"
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\nDATABASE_PATH={self.db_path}\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.service = PortfolioService(self.repo)
        self.account = self.repo.create_account(
            name="test", broker=None, market="cn", base_currency="CNY"
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key in ("ENV_FILE", "DATABASE_PATH"):
            os.environ.pop(key, None)
        self.temp_dir.cleanup()

    def test_update_trade_event_basic(self):
        """基本编辑：更新 price 后返回更新后的 trade 字典。"""
        trade = self.service.record_trade(
            account_id=self.account.id, symbol="600519",
            trade_date=date(2026, 4, 20), side="buy",
            quantity=100.0, price=1800.0, fee=5.0,
        )
        result = self.service.update_trade_event(
            trade_id=trade["id"], fields={"price": 1900.0}
        )
        self.assertEqual(result["trade"]["price"], 1900.0)

    def test_update_trade_event_not_found(self):
        """trade_id 不存在时抛出 ValueError。"""
        with self.assertRaises(ValueError):
            self.service.update_trade_event(trade_id=99999, fields={"price": 100.0})

    def test_update_trade_event_invalid_field(self):
        """试图更新不可编辑字段（symbol）时被忽略。"""
        trade = self.service.record_trade(
            account_id=self.account.id, symbol="600519",
            trade_date=date(2026, 4, 20), side="buy",
            quantity=100.0, price=1800.0,
        )
        result = self.service.update_trade_event(
            trade_id=trade["id"], fields={"symbol": "000001", "price": 2000.0}
        )
        # symbol 不应被改变
        self.assertEqual(result["trade"]["symbol"], "600519")
        self.assertEqual(result["trade"]["price"], 2000.0)

    def test_update_trade_event_oversell_warning(self):
        """编辑买入数量减少后，后续卖出 oversell 时抛出 OversellError。"""
        # 买入 200 股
        buy_trade = self.service.record_trade(
            account_id=self.account.id, symbol="600519",
            trade_date=date(2026, 4, 20), side="buy",
            quantity=200.0, price=1800.0,
        )
        # 卖出 150 股
        self.service.record_trade(
            account_id=self.account.id, symbol="600519",
            trade_date=date(2026, 4, 21), side="sell",
            quantity=150.0, price=1900.0,
        )
        # 将买入数量从 200 减到 100，后续 150 的卖出会 oversell → 硬阻断
        from src.services.portfolio_service import OversellError
        with self.assertRaises(OversellError):
            self.service.update_trade_event(
                trade_id=buy_trade["id"], fields={"quantity": 100.0}
            )

    def test_update_trade_event_no_empty_fields(self):
        """空 fields 字典时抛出 ValueError。"""
        trade = self.service.record_trade(
            account_id=self.account.id, symbol="600519",
            trade_date=date(2026, 4, 20), side="buy",
            quantity=100.0, price=1800.0,
        )
        with self.assertRaises(ValueError):
            self.service.update_trade_event(trade_id=trade["id"], fields={})
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_portfolio_trade_edit.py::TradeEditServiceTestCase -v -x`
预期：FAIL，`AttributeError: 'PortfolioService' object has no attribute 'update_trade_event'`

- [ ] **步骤 3：在 `src/services/portfolio_service.py` 中实现 `update_trade_event()`**

> **[Pass 2 修订 B2]** 新增 `OversellError` 异常类。`_replay_oversell_check` 检测到负持仓时 raise `OversellError`（硬阻断），不保存变更。

首先在文件顶部新增异常类：

```python
class OversellError(ValueError):
    """编辑交易后重新回放检测到 oversell 时抛出。
    
    Attributes:
        violations: 违规详情列表
    """
    def __init__(self, violations: List[str]):
        self.violations = violations
        super().__init__(f"Oversell detected: {'; '.join(violations)}")
```

在 `PortfolioService` 类的 `delete_trade_event` 方法后面添加：

```python
    def update_trade_event(
        self,
        *,
        trade_id: int,
        fields: Dict[str, Any],
    ) -> Dict[str, Any]:
        """编辑交易流水并重新校验 oversell（硬阻断）。

        Args:
            trade_id: 交易记录 ID
            fields: 要更新的字段（仅允许 quantity/price/fee/tax/note）

        Returns:
            {"trade": {...}}

        Raises:
            ValueError: trade_id 不存在或 fields 为空
            OversellError: 编辑后检测到后续交易会 oversell（变更被回滚）
        """
        editable = {k: v for k, v in fields.items() if k in {"quantity", "price", "fee", "tax", "note"}}
        if not editable:
            raise ValueError("No editable fields provided")

        with self.repo.portfolio_write_session() as session:
            updated = self.repo.update_trade_in_session(
                session=session,
                trade_id=trade_id,
                fields=editable,
            )
            if updated is None:
                raise ValueError(f"Trade not found: {trade_id}")

            # 重新回放该 account 下全部交易，检测 oversell（硬阻断）
            violations = self._replay_oversell_check(
                session=session,
                account_id=updated.account_id,
            )
            if violations:
                raise OversellError(violations)

            trade_dict = self._trade_row_to_dict(updated)

        return {"trade": trade_dict}

    def _replay_oversell_check(
        self,
        *,
        session: Any,
        account_id: int,
    ) -> List[str]:
        """重新回放全部交易事件流，检测 oversell。

        Returns:
            违规消息列表（为空表示无 oversell）。
            非空时调用方应 raise OversellError 阻断保存。
        """
        from sqlalchemy import select as sa_select
        from src.storage import PortfolioTrade

        trades = session.execute(
            sa_select(PortfolioTrade)
            .where(PortfolioTrade.account_id == account_id)
            .order_by(PortfolioTrade.trade_date.asc(), PortfolioTrade.id.asc())
        ).scalars().all()

        violations: List[str] = []
        # {symbol: cumulative_quantity}
        position_map: Dict[str, float] = {}

        for t in trades:
            symbol = t.symbol
            qty = float(t.quantity)
            if t.side == "buy":
                position_map[symbol] = position_map.get(symbol, 0.0) + qty
            elif t.side == "sell":
                available = position_map.get(symbol, 0.0)
                if qty > available + EPS:
                    trade_date_str = t.trade_date.isoformat() if t.trade_date else "unknown"
                    violations.append(
                        f"交易 #{t.id}（{trade_date_str} 卖出 {symbol} {qty:.0f}股）"
                        f"导致 oversell（可用 {available:.0f}股）"
                    )
                position_map[symbol] = available - qty

        return violations
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m pytest tests/test_portfolio_trade_edit.py -v -x`
预期：8 passed（Repository 3 + Service 5）

- [ ] **步骤 5：提交**

```bash
git add src/services/portfolio_service.py tests/test_portfolio_trade_edit.py
git commit -m "feat(service): add update_trade_event with oversell replay validation"
```

---

### 任务 6：SimTradingService 改造 — 审批分支 + pending 方法

**agent: gem-implementer**

**文件：**
- 修改：`tests/test_sim_trading_approval.py`（追加 Service 层测试）
- 修改：`src/services/sim_trading_service.py`

- [ ] **步骤 1：在 `tests/test_sim_trading_approval.py` 中追加 SimTradingService 测试**

在文件末尾追加：

```python
class SimTradingApprovalServiceTestCase(unittest.TestCase):
    """SimTradingService 审批流程测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sim_approval_test.db"
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n"
            f"DATABASE_PATH={self.db_path}\nSIM_TRADING_ENABLED=true\nSIM_TRADING_ACCOUNT_ID=1\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.portfolio_service = PortfolioService(self.repo)
        self.account = self.repo.create_account(
            name="sim-test", broker=None, market="cn", base_currency="CNY"
        )
        # 注入现金
        self.portfolio_service.record_cash_ledger(
            account_id=self.account.id,
            event_date=date(2026, 4, 1),
            direction="in",
            amount=1000000.0,
        )

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key in ("ENV_FILE", "DATABASE_PATH", "SIM_TRADING_ENABLED", "SIM_TRADING_ACCOUNT_ID"):
            os.environ.pop(key, None)
        self.temp_dir.cleanup()

    def test_check_approval_required_default_false(self):
        """默认 approval_required 为 False。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=self.portfolio_service)
        self.assertFalse(svc.check_approval_required())

    def test_check_approval_required_true(self):
        """设置 approval_required 为 True 后返回 True。"""
        from src.services.sim_trading_service import SimTradingService
        config = Config.get_instance()
        config.sim_trading_approval_required = True
        svc = SimTradingService(portfolio_service=self.portfolio_service)
        self.assertTrue(svc.check_approval_required())

    def test_save_pending_trades(self):
        """save_pending_trades 写入 PendingSimTrade 记录。"""
        from src.services.sim_trading_service import SimTradingService, SimTradeAction
        svc = SimTradingService(portfolio_service=self.portfolio_service)
        actions = [
            SimTradeAction(stock_code="600519", action="buy", quantity=100, price=1850.0, reason="test reason"),
        ]
        pending_ids = svc.save_pending_trades(actions, self.account.id)
        self.assertEqual(len(pending_ids), 1)

        row = self.repo.get_pending_sim_trade(pending_ids[0])
        self.assertIsNotNone(row)
        self.assertEqual(row.symbol, "600519")
        self.assertEqual(row.status, "pending")
        self.assertEqual(row.llm_reasoning, "test reason")

    def test_execute_pending_trade_success(self):
        """审批通过后执行 pending trade。"""
        from src.services.sim_trading_service import SimTradingService, SimTradeAction
        svc = SimTradingService(portfolio_service=self.portfolio_service)
        actions = [
            SimTradeAction(stock_code="600519", action="buy", quantity=100, price=1850.0, reason="test"),
        ]
        pending_ids = svc.save_pending_trades(actions, self.account.id)

        result = svc.execute_pending_trade(pending_ids[0], reviewer_note="approved")
        self.assertEqual(result["status"], "executed")
        self.assertIn("trade_id", result)

        row = self.repo.get_pending_sim_trade(pending_ids[0])
        self.assertEqual(row.status, "approved")

    def test_execute_pending_trade_not_found(self):
        """pending_id 不存在时返回错误。"""
        from src.services.sim_trading_service import SimTradingService
        svc = SimTradingService(portfolio_service=self.portfolio_service)
        result = svc.execute_pending_trade(99999)
        self.assertEqual(result["status"], "error")

    def test_execute_pending_trade_not_pending(self):
        """非 pending 状态的记录不允许执行。"""
        from src.services.sim_trading_service import SimTradingService, SimTradeAction
        svc = SimTradingService(portfolio_service=self.portfolio_service)
        actions = [
            SimTradeAction(stock_code="600519", action="buy", quantity=100, price=1850.0, reason="test"),
        ]
        pending_ids = svc.save_pending_trades(actions, self.account.id)
        # 先拒绝
        self.repo.update_pending_sim_trade_status(pending_id=pending_ids[0], status="rejected")
        # 再尝试执行
        result = svc.execute_pending_trade(pending_ids[0])
        self.assertEqual(result["status"], "error")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_sim_trading_approval.py::SimTradingApprovalServiceTestCase -v -x`
预期：FAIL，`AttributeError: 'SimTradingService' object has no attribute 'check_approval_required'`

- [ ] **步骤 3：在 `src/services/sim_trading_service.py` 中实现新方法**

首先在文件顶部的 imports 中添加：

```python
from src.repositories.portfolio_repo import PortfolioRepository
```

在 `SimTradingService` 类中添加以下方法：

```python
    def check_approval_required(self) -> bool:
        """检查是否需要审批。仅读取 Config.sim_trading_approval_required。"""
        return self.config.sim_trading_approval_required

    def save_pending_trades(
        self,
        actions: List[SimTradeAction],
        account_id: int,
    ) -> List[int]:
        """将 LLM 交易决策批量写入 PendingSimTrade 表。

        Returns:
            新建记录的 id 列表
        """
        repo = self.portfolio_service.repo
        commission = self.config.sim_trading_default_commission
        pending_ids: List[int] = []
        for action in actions:
            row = repo.add_pending_sim_trade(
                account_id=account_id,
                symbol=action.stock_code,
                side=action.action,
                quantity=float(action.quantity),
                price=action.price,
                fee=commission,
                tax=0.0,
                note=f"[sim-trading] {action.reason[:200]}",
                llm_reasoning=action.reason,
            )
            pending_ids.append(row.id)
        return pending_ids

    def execute_pending_trade(
        self,
        pending_id: int,
        reviewer_note: str = "",
    ) -> Dict[str, Any]:
        """审批通过后执行单笔 pending trade。

        1. 查询 PendingSimTrade 记录
        2. 校验状态必须为 pending
        3. 在同一事务内调用 record_trade() 入库并更新状态（[Pass 2 I11]）
        4. trade_date 使用 created_at 日期（[Pass 2 I5]）
        """
        repo = self.portfolio_service.repo
        row = repo.get_pending_sim_trade(pending_id)
        if row is None:
            return {"status": "error", "message": f"Pending trade not found: {pending_id}"}

        if row.status != "pending":
            return {"status": "error", "message": f"Pending trade {pending_id} is not in pending status: {row.status}"}

        # [I5] 使用 LLM 决策时的日期（created_at），不使用审批时的 date.today()
        trade_date = row.created_at.date() if row.created_at else date.today()

        try:
            # [I11] record_trade + update_status 在同一 write session 中完成，
            # 避免 record_trade 成功但 update_status 失败导致状态不一致。
            with repo.portfolio_write_session() as session:
                trade_result = self.portfolio_service.record_trade_in_session(
                    session=session,
                    account_id=row.account_id,
                    symbol=row.symbol,
                    trade_date=trade_date,
                    side=row.side,
                    quantity=float(row.quantity),
                    price=float(row.price),
                    fee=float(row.fee),
                    tax=float(row.tax),
                    note=row.note or "",
                )
                repo.update_pending_sim_trade_status_in_session(
                    session=session,
                    pending_id=pending_id,
                    status="approved",
                    reviewer_note=reviewer_note,
                )

            return {
                "status": "executed",
                "trade_id": trade_result.get("id"),
                "pending_id": pending_id,
            }
        except Exception as exc:
            logger.warning("执行 pending trade %s 失败: %s", pending_id, exc, exc_info=True)
            return {"status": "error", "message": str(exc)}
```

> **注意 [Pass 2 I11]**：此方法需要 `portfolio_service` 提供 `record_trade_in_session()` 变体。如果 `PortfolioService` 中尚无此方法，需要同步新增一个在已有 session 中执行 record_trade 核心逻辑的方法。实现者应参考 `record_trade()` 的现有实现，将核心写入逻辑提取为接受 session 参数的内部方法。

- [ ] **步骤 4：改造 `run()` 方法，在 `validate_and_execute()` 前插入审批分支**

在 `run()` 方法中，找到第 6 步 `# 6. 校验并执行` 的代码块，将其替换为：

```python
        # 6. 审批检查 + 校验执行
        if self.check_approval_required() and is_scheduled:
            pending_ids = self.save_pending_trades(actions, account_id)
            logger.info("审批模式：%d 笔交易已保存至待审批队列", len(pending_ids))
            return {
                "status": "pending_approval",
                "pending_count": len(pending_ids),
                "pending_ids": pending_ids,
            }

        logger.info("开始执行 %d 笔模拟交易...", len(actions))
        result = self.validate_and_execute(actions, account_id, portfolio_ctx)
```

- [ ] **步骤 5：改造 `_has_executed_today()` 增加 pending 表查询**

> **[Pass 2 修订 I6]** 审批模式下 `_has_executed_today()` 应同时查询 PendingSimTrade 表，如果当日已有 pending 或 approved 记录，也视为已执行，避免重复生成 pending。

找到 `_has_executed_today()` 方法，改造为：

```python
    def _has_executed_today(self, account_id: int) -> bool:
        """检查今日是否已执行过模拟交易（幂等检查）。

        [Pass 2 I6] 同时查询 PendingSimTrade 表：如果当日已有
        pending 或 approved 记录，也视为已执行。
        """
        try:
            today = date.today()

            # 检查已执行的交易
            trades, _ = self.portfolio_service.repo.query_trades(
                account_id=account_id,
                date_from=today,
                date_to=today,
                symbol=None,
                side=None,
                page=1,
                page_size=1000,
            )
            if any((t.note or "").startswith("[sim-trading]") for t in trades):
                return True

            # [I6] 检查 pending 表：当日是否已有 pending / approved 记录
            if self.portfolio_service.repo.has_pending_or_approved_today(account_id, today):
                return True

            return False
        except Exception:
            logger.error("幂等检查查询失败，account_id=%s，安全跳过本次执行", account_id, exc_info=True)
            return True
```

- [ ] **步骤 6：运行测试验证通过**

运行：`python -m pytest tests/test_sim_trading_approval.py -v -x`
预期：全部通过（Repo 7 + Service 6 = 13）

- [ ] **步骤 7：提交**

```bash
git add src/services/sim_trading_service.py tests/test_sim_trading_approval.py
git commit -m "feat(sim-trading): add approval branch, save_pending_trades, execute_pending_trade with txn consistency"
```

---

## Wave 4：API 层（依赖 Wave 3 的 Service）

### 任务 7：API Schema 新增

**agent: gem-implementer**

**文件：**
- 修改：`api/v1/schemas/portfolio.py`

- [ ] **步骤 1：在 `api/v1/schemas/portfolio.py` 末尾新增 Schema 类**

```python
# --- 交易编辑 ---

class PortfolioTradeUpdateRequest(BaseModel):
    quantity: Optional[float] = Field(None, gt=0)
    price: Optional[float] = Field(None, gt=0)
    fee: Optional[float] = Field(None, ge=0)
    tax: Optional[float] = Field(None, ge=0)
    note: Optional[str] = Field(None, max_length=255)


class PortfolioTradeUpdateResponse(BaseModel):
    """[Pass 2 B2] 移除 validation_warnings，oversell 改为 400 硬阻断。"""
    trade: PortfolioTradeListItem


# --- 模拟交易审批 ---

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


# --- 模拟交易配置 ---

class SimTradingConfigResponse(BaseModel):
    approval_required: bool
    sim_trading_enabled: bool
    sim_trading_account_id: Optional[int] = None


class SimTradingConfigUpdateRequest(BaseModel):
    approval_required: bool
```

- [ ] **步骤 2：验证语法**

运行：`python -m py_compile api/v1/schemas/portfolio.py`
预期：无输出（编译成功）

- [ ] **步骤 3：提交**

```bash
git add api/v1/schemas/portfolio.py
git commit -m "feat(schema): add trade edit + sim-trading approval + config schemas"
```

---

### 任务 8：API 端点 — PUT trades + 审批端点 + 配置端点

**agent: gem-implementer**

**文件：**
- 修改：`api/v1/endpoints/portfolio.py`
- 创建：`tests/test_pending_sim_trade_api.py`
- 创建：`tests/test_sim_trading_config_api.py`

- [ ] **步骤 1：在 `api/v1/endpoints/portfolio.py` 顶部更新 imports**

在已有的 schema imports 中追加新的 Schema：

```python
from api.v1.schemas.portfolio import (
    # ... 已有的 imports ...
    PendingSimTradeItem,
    PendingSimTradeListResponse,
    PendingSimTradeReviewRequest,
    PortfolioTradeUpdateRequest,
    PortfolioTradeUpdateResponse,
    SimTradingConfigResponse,
    SimTradingConfigUpdateRequest,
)
```

同时追加 Service imports：

```python
from src.services.sim_trading_service import SimTradingService
from src.config import get_config
```

- [ ] **步骤 2：新增 PUT /trades/{trade_id} 端点**

> **[Pass 2 修订 B2]** 捕获 `OversellError` 返回 400，不再返回 `validation_warnings`。

在现有 trade 相关端点附近添加：

```python
@router.put(
    "/trades/{trade_id}",
    response_model=PortfolioTradeUpdateResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Update a trade event (partial update)",
)
def update_trade(trade_id: int, request: PortfolioTradeUpdateRequest) -> PortfolioTradeUpdateResponse:
    from src.services.portfolio_service import OversellError
    service = PortfolioService()
    fields = {}
    if request.quantity is not None:
        fields["quantity"] = request.quantity
    if request.price is not None:
        fields["price"] = request.price
    if request.fee is not None:
        fields["fee"] = request.fee
    if request.tax is not None:
        fields["tax"] = request.tax
    if request.note is not None:
        fields["note"] = request.note
    if not fields:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": "No fields provided"})
    try:
        result = service.update_trade_event(trade_id=trade_id, fields=fields)
        return PortfolioTradeUpdateResponse(**result)
    except OversellError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "oversell", "message": str(exc), "violations": exc.violations},
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)})
        raise _bad_request(exc)
    except PortfolioBusyError as exc:
        raise _conflict_error(error="portfolio_busy", message=str(exc))
    except Exception as exc:
        raise _internal_error("Update trade failed", exc)
```

- [ ] **步骤 3：新增审批端点**

```python
def _pending_row_to_item(row) -> PendingSimTradeItem:
    return PendingSimTradeItem(
        id=row.id,
        account_id=row.account_id,
        symbol=row.symbol,
        side=row.side,
        quantity=row.quantity,
        price=row.price,
        fee=row.fee or 0.0,
        tax=row.tax or 0.0,
        note=row.note,
        llm_reasoning=row.llm_reasoning,
        status=row.status,
        created_at=row.created_at.isoformat() if row.created_at else None,
        reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
        reviewer_note=row.reviewer_note,
    )


@router.get(
    "/sim-trades/pending",
    response_model=PendingSimTradeListResponse,
    summary="List pending sim trades",
)
def list_pending_sim_trades(
    account_id: Optional[int] = Query(None),
    status: Optional[str] = Query("pending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PendingSimTradeListResponse:
    from src.repositories.portfolio_repo import PortfolioRepository
    repo = PortfolioRepository()
    try:
        rows, total = repo.list_pending_sim_trades(
            account_id=account_id, status=status, page=page, page_size=page_size
        )
        return PendingSimTradeListResponse(
            items=[_pending_row_to_item(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _internal_error("List pending sim trades failed", exc)


@router.post(
    "/sim-trades/{pending_id}/approve",
    response_model=dict,
    summary="Approve a pending sim trade",
)
def approve_pending_sim_trade(
    pending_id: int,
    request: Optional[PendingSimTradeReviewRequest] = None,
) -> dict:
    # [Pass 2 I10] approve 和 reject 统一在 API 层先查询并检查 status
    from src.repositories.portfolio_repo import PortfolioRepository
    repo = PortfolioRepository()
    row = repo.get_pending_sim_trade(pending_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"Pending trade {pending_id} not found"})
    if row.status != "pending":
        raise HTTPException(status_code=400, detail={"error": "invalid_status", "message": f"Cannot approve: current status is {row.status}"})

    svc = SimTradingService(portfolio_service=PortfolioService())
    reviewer_note = request.reviewer_note if request and request.reviewer_note else ""
    result = svc.execute_pending_trade(pending_id, reviewer_note=reviewer_note)
    if result.get("status") == "error":
        msg = result.get("message", "Unknown error")
        raise HTTPException(status_code=400, detail={"error": "approval_failed", "message": msg})
    return result


@router.post(
    "/sim-trades/{pending_id}/reject",
    response_model=dict,
    summary="Reject a pending sim trade",
)
def reject_pending_sim_trade(
    pending_id: int,
    request: Optional[PendingSimTradeReviewRequest] = None,
) -> dict:
    from src.repositories.portfolio_repo import PortfolioRepository
    repo = PortfolioRepository()
    reviewer_note = request.reviewer_note if request and request.reviewer_note else ""
    row = repo.get_pending_sim_trade(pending_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"Pending trade {pending_id} not found"})
    if row.status != "pending":
        raise HTTPException(status_code=400, detail={"error": "invalid_status", "message": f"Cannot reject: current status is {row.status}"})
    repo.update_pending_sim_trade_status(pending_id=pending_id, status="rejected", reviewer_note=reviewer_note)
    return {"status": "rejected", "pending_id": pending_id}


@router.delete(
    "/sim-trades/{pending_id}",
    response_model=PortfolioDeleteResponse,
    summary="Delete a pending sim trade",
)
def delete_pending_sim_trade(pending_id: int) -> PortfolioDeleteResponse:
    from src.repositories.portfolio_repo import PortfolioRepository
    repo = PortfolioRepository()
    deleted = repo.delete_pending_sim_trade(pending_id)
    return PortfolioDeleteResponse(deleted=1 if deleted else 0)
```

- [ ] **步骤 4：新增配置端点**

> **[Pass 2 修订 B1]** PUT 端点同时更新内存 Config 和 `.env` 文件，使用现有 `ConfigManager.apply_updates()` 原子写入。

```python
@router.get(
    "/sim-trading/config",
    response_model=SimTradingConfigResponse,
    summary="Get sim trading config",
)
def get_sim_trading_config() -> SimTradingConfigResponse:
    config = get_config()
    return SimTradingConfigResponse(
        approval_required=config.sim_trading_approval_required,
        sim_trading_enabled=config.sim_trading_enabled,
        sim_trading_account_id=config.sim_trading_account_id,
    )


@router.put(
    "/sim-trading/config",
    response_model=SimTradingConfigResponse,
    summary="Update sim trading config",
)
def update_sim_trading_config(request: SimTradingConfigUpdateRequest) -> SimTradingConfigResponse:
    from src.core.config_manager import ConfigManager
    config = get_config()

    # 1. 更新内存中的 Config 对象
    config.sim_trading_approval_required = request.approval_required

    # 2. [B1] 持久化到 .env 文件，服务重启后仍生效
    try:
        config_mgr = ConfigManager()
        env_value = "true" if request.approval_required else "false"
        config_mgr.apply_updates(
            updates=[("SIM_TRADING_APPROVAL_REQUIRED", env_value)],
            sensitive_keys=set(),
            mask_token="",
        )
    except Exception as exc:
        logger.warning("写入 .env 文件失败: %s", exc, exc_info=True)
        # 内存已更新，.env 写入失败仅 warning，不阻断响应

    return SimTradingConfigResponse(
        approval_required=config.sim_trading_approval_required,
        sim_trading_enabled=config.sim_trading_enabled,
        sim_trading_account_id=config.sim_trading_account_id,
    )
```

- [ ] **步骤 5：验证语法**

运行：`python -m py_compile api/v1/endpoints/portfolio.py`
预期：无输出

- [ ] **步骤 6：编写 API 端点测试**

创建 `tests/test_pending_sim_trade_api.py`：

```python
# -*- coding: utf-8 -*-
"""审批 API 端点测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from src.config import Config
from src.storage import DatabaseManager


class PendingSimTradeApiTestCase(unittest.TestCase):
    """审批相关 API 端点测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "api_test.db"
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n"
            f"DATABASE_PATH={self.db_path}\nSIM_TRADING_ENABLED=true\nSIM_TRADING_ACCOUNT_ID=1\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        from api.app import create_app
        app = create_app()
        self.client = TestClient(app)

        # 创建测试账户
        resp = self.client.post("/api/v1/portfolio/accounts", json={
            "name": "test", "market": "cn", "base_currency": "CNY"
        })
        self.account_id = resp.json()["id"]

        # 注入现金
        self.client.post("/api/v1/portfolio/cash-ledger", json={
            "account_id": self.account_id, "event_date": "2026-04-01",
            "direction": "in", "amount": 1000000.0,
        })

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key in ("ENV_FILE", "DATABASE_PATH"):
            os.environ.pop(key, None)
        self.temp_dir.cleanup()

    def _create_pending(self):
        from src.repositories.portfolio_repo import PortfolioRepository
        repo = PortfolioRepository()
        row = repo.add_pending_sim_trade(
            account_id=self.account_id, symbol="600519", side="buy",
            quantity=100.0, price=1850.0, fee=5.0, llm_reasoning="test reason",
        )
        return row.id

    def test_list_pending(self):
        pid = self._create_pending()
        resp = self.client.get("/api/v1/portfolio/sim-trades/pending")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["id"], pid)

    def test_approve(self):
        pid = self._create_pending()
        resp = self.client.post(f"/api/v1/portfolio/sim-trades/{pid}/approve", json={"reviewer_note": "ok"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "executed")

    def test_reject(self):
        pid = self._create_pending()
        resp = self.client.post(f"/api/v1/portfolio/sim-trades/{pid}/reject", json={"reviewer_note": "no"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "rejected")

    def test_delete(self):
        pid = self._create_pending()
        resp = self.client.delete(f"/api/v1/portfolio/sim-trades/{pid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["deleted"], 1)

    def test_approve_not_found(self):
        resp = self.client.post("/api/v1/portfolio/sim-trades/99999/approve")
        self.assertEqual(resp.status_code, 404)
```

创建 `tests/test_sim_trading_config_api.py`：

```python
# -*- coding: utf-8 -*-
"""模拟交易配置 API 端点测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.config import Config
from src.storage import DatabaseManager


class SimTradingConfigApiTestCase(unittest.TestCase):
    """配置端点 GET / PUT 测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "config_api_test.db"
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            f"STOCK_LIST=600519\nGEMINI_API_KEY=test\nADMIN_AUTH_ENABLED=false\n"
            f"DATABASE_PATH={self.db_path}\nSIM_TRADING_ENABLED=true\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        from api.app import create_app
        app = create_app()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key in ("ENV_FILE", "DATABASE_PATH"):
            os.environ.pop(key, None)
        self.temp_dir.cleanup()

    def test_get_config(self):
        resp = self.client.get("/api/v1/portfolio/sim-trading/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("approval_required", data)
        self.assertFalse(data["approval_required"])

    def test_put_config(self):
        resp = self.client.put("/api/v1/portfolio/sim-trading/config", json={"approval_required": True})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["approval_required"])

        # 验证 GET 读取到更新后的值
        resp2 = self.client.get("/api/v1/portfolio/sim-trading/config")
        self.assertTrue(resp2.json()["approval_required"])
```

- [ ] **步骤 7：运行 API 测试**

运行：`python -m pytest tests/test_pending_sim_trade_api.py tests/test_sim_trading_config_api.py -v -x`
预期：全部通过

- [ ] **步骤 8：提交**

```bash
git add api/v1/endpoints/portfolio.py api/v1/schemas/portfolio.py tests/test_pending_sim_trade_api.py tests/test_sim_trading_config_api.py
git commit -m "feat(api): add trade edit, sim-trading approval, and config endpoints"
```

---

## Wave 5：前端（依赖 Wave 4 的 API）

### 任务 9：前端类型定义

**agent: gem-implementer**

**文件：**
- 修改：`apps/dsa-web/src/types/portfolio.ts`

- [ ] **步骤 1：在 `apps/dsa-web/src/types/portfolio.ts` 末尾追加类型**

```typescript
// --- 交易编辑 ---

export interface TradeUpdateRequest {
  quantity?: number;
  price?: number;
  fee?: number;
  tax?: number;
  note?: string;
}

export interface TradeUpdateResponse {
  trade: PortfolioTradeListItem;
}

// --- 模拟交易审批 ---

export interface PendingSimTrade {
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

export interface PendingSimTradeListResponse {
  items: PendingSimTrade[];
  total: number;
  page: number;
  pageSize: number;
}

// --- 模拟交易配置 ---

export interface SimTradingConfig {
  approvalRequired: boolean;
  simTradingEnabled: boolean;
  simTradingAccountId: number | null;
}

export interface SimTradingConfigUpdateRequest {
  approvalRequired: boolean;
}
```

- [ ] **步骤 2：提交**

```bash
cd apps/dsa-web
git add src/types/portfolio.ts
git commit -m "feat(web/types): add trade edit + sim-trading approval types"
```

---

### 任务 10：前端 API 调用方法

**agent: gem-implementer**

**文件：**
- 修改：`apps/dsa-web/src/api/portfolio.ts`

- [ ] **步骤 1：在 `apps/dsa-web/src/api/portfolio.ts` 的 imports 中追加新类型**

```typescript
import type {
  // ... 已有 imports ...
  TradeUpdateRequest,
  TradeUpdateResponse,
  PendingSimTradeListResponse,
  SimTradingConfig,
  SimTradingConfigUpdateRequest,
} from '../types/portfolio';
```

- [ ] **步骤 2：在 `portfolioApi` 对象末尾追加方法**

```typescript
  async updateTrade(tradeId: number, data: TradeUpdateRequest): Promise<TradeUpdateResponse> {
    const response = await apiClient.put<Record<string, unknown>>(`/api/v1/portfolio/trades/${tradeId}`, {
      quantity: data.quantity,
      price: data.price,
      fee: data.fee,
      tax: data.tax,
      note: data.note,
    });
    return toCamelCase<TradeUpdateResponse>(response.data);
  },

  async getPendingSimTrades(params?: {
    accountId?: number;
    status?: string;
    page?: number;
    pageSize?: number;
  }): Promise<PendingSimTradeListResponse> {
    const queryParams: Record<string, string | number> = {};
    if (params?.accountId != null) queryParams.account_id = params.accountId;
    if (params?.status) queryParams.status = params.status;
    if (params?.page != null) queryParams.page = params.page;
    if (params?.pageSize != null) queryParams.page_size = params.pageSize;
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/portfolio/sim-trades/pending', {
      params: queryParams,
    });
    return toCamelCase<PendingSimTradeListResponse>(response.data);
  },

  async approvePendingTrade(id: number, note?: string): Promise<Record<string, unknown>> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/portfolio/sim-trades/${id}/approve`,
      note ? { reviewer_note: note } : undefined,
    );
    return toCamelCase<Record<string, unknown>>(response.data);
  },

  async rejectPendingTrade(id: number, note?: string): Promise<Record<string, unknown>> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/portfolio/sim-trades/${id}/reject`,
      note ? { reviewer_note: note } : undefined,
    );
    return toCamelCase<Record<string, unknown>>(response.data);
  },

  async deletePendingTrade(id: number): Promise<PortfolioDeleteResponse> {
    const response = await apiClient.delete<Record<string, unknown>>(`/api/v1/portfolio/sim-trades/${id}`);
    return toCamelCase<PortfolioDeleteResponse>(response.data);
  },

  async getSimTradingConfig(): Promise<SimTradingConfig> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/portfolio/sim-trading/config');
    return toCamelCase<SimTradingConfig>(response.data);
  },

  async updateSimTradingConfig(data: SimTradingConfigUpdateRequest): Promise<SimTradingConfig> {
    const response = await apiClient.put<Record<string, unknown>>('/api/v1/portfolio/sim-trading/config', {
      approval_required: data.approvalRequired,
    });
    return toCamelCase<SimTradingConfig>(response.data);
  },
```

- [ ] **步骤 3：验证构建**

运行：`cd apps/dsa-web && npm run lint`
预期：无错误

- [ ] **步骤 4：提交**

```bash
git add src/api/portfolio.ts
git commit -m "feat(web/api): add trade edit + sim-trading approval API methods"
```

---

### 任务 11：前端 — TradeEditModal 组件

> **[Pass 2 修订 I7/B2]** oversell 现在是 400 硬阻断，前端展示 API 错误信息使用 `InlineAlert` 组件，不使用 `alert()`。

**agent: gem-implementer**

**文件：**
- 创建：`apps/dsa-web/src/components/portfolio/TradeEditModal.tsx`

- [ ] **步骤 1：创建 `TradeEditModal.tsx`**

```tsx
import type React from 'react';
import { useState } from 'react';
import { portfolioApi } from '../../api/portfolio';
import type { PortfolioTradeListItem, TradeUpdateRequest } from '../../types/portfolio';
import { InlineAlert } from '../common';

interface TradeEditModalProps {
  trade: PortfolioTradeListItem;
  onClose: () => void;
  onSaved: () => void;
}

const INPUT_CLASS =
  'input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 text-sm transition-all focus:outline-none';

export default function TradeEditModal({ trade, onClose, onSaved }: TradeEditModalProps) {
  const [quantity, setQuantity] = useState(String(trade.quantity));
  const [price, setPrice] = useState(String(trade.price));
  const [fee, setFee] = useState(String(trade.fee));
  const [tax, setTax] = useState(String(trade.tax));
  const [note, setNote] = useState(trade.note ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const data: TradeUpdateRequest = {};
      const qtyVal = parseFloat(quantity);
      const priceVal = parseFloat(price);
      const feeVal = parseFloat(fee);
      const taxVal = parseFloat(tax);
      if (!isNaN(qtyVal) && qtyVal !== trade.quantity) data.quantity = qtyVal;
      if (!isNaN(priceVal) && priceVal !== trade.price) data.price = priceVal;
      if (!isNaN(feeVal) && feeVal !== trade.fee) data.fee = feeVal;
      if (!isNaN(taxVal) && taxVal !== trade.tax) data.tax = taxVal;
      if (note !== (trade.note ?? '')) data.note = note;

      if (Object.keys(data).length === 0) {
        onClose();
        return;
      }
      await portfolioApi.updateTrade(trade.id, data);
      onSaved();
    } catch (err: unknown) {
      // [I7] 使用 InlineAlert 展示错误，包括 oversell 400 错误
      const apiError = err as { response?: { data?: { message?: string; violations?: string[] } } };
      const violations = apiError?.response?.data?.violations;
      if (violations && violations.length > 0) {
        setError(violations.join('；'));
      } else {
        setError(apiError?.response?.data?.message ?? (err instanceof Error ? err.message : '保存失败'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="card-surface w-full max-w-md rounded-2xl p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-4 text-lg font-semibold">编辑交易</h3>
        <div className="mb-4 text-sm text-[var(--color-text-secondary)]">
          {trade.symbol} · {trade.side === 'buy' ? '买入' : '卖出'} · {trade.tradeDate}
        </div>
        {error && (
          <InlineAlert variant="danger" message={error} className="mb-3" />
        )}
        <form onSubmit={handleSubmit} className="space-y-3">
          <label className="block text-sm">
            数量
            <input type="number" step="1" min="1" value={quantity} onChange={(e) => setQuantity(e.target.value)} className={INPUT_CLASS} />
          </label>
          <label className="block text-sm">
            价格
            <input type="number" step="0.01" min="0.01" value={price} onChange={(e) => setPrice(e.target.value)} className={INPUT_CLASS} />
          </label>
          <label className="block text-sm">
            手续费
            <input type="number" step="0.01" min="0" value={fee} onChange={(e) => setFee(e.target.value)} className={INPUT_CLASS} />
          </label>
          <label className="block text-sm">
            税费
            <input type="number" step="0.01" min="0" value={tax} onChange={(e) => setTax(e.target.value)} className={INPUT_CLASS} />
          </label>
          <label className="block text-sm">
            备注
            <input type="text" maxLength={255} value={note} onChange={(e) => setNote(e.target.value)} className={INPUT_CLASS} />
          </label>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary rounded-lg px-4 py-2 text-sm">
              取消
            </button>
            <button type="submit" disabled={saving} className="btn-primary rounded-lg px-4 py-2 text-sm">
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
```

- [ ] **步骤 2：提交**

```bash
git add src/components/portfolio/TradeEditModal.tsx
git commit -m "feat(web): add TradeEditModal component"
```

---

### 任务 12：前端 — PendingTradesTab 组件

> **[Pass 2 修订 I8]** catch 块设置 error state 并渲染 InlineAlert 错误提示，不静默吞掉错误。

**agent: gem-implementer**

**文件：**
- 创建：`apps/dsa-web/src/components/portfolio/PendingTradesTab.tsx`

- [ ] **步骤 1：创建 `PendingTradesTab.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react';
import { portfolioApi } from '../../api/portfolio';
import type { PendingSimTrade } from '../../types/portfolio';
import { Badge, Card, InlineAlert } from '../common';

interface PendingTradesTabProps {
  accountId?: number;
}

export default function PendingTradesTab({ accountId }: PendingTradesTabProps) {
  const [items, setItems] = useState<PendingSimTrade[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionInProgress, setActionInProgress] = useState<number | null>(null);

  const fetchPending = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await portfolioApi.getPendingSimTrades({
        accountId: accountId,
        status: 'pending',
        page: 1,
        pageSize: 50,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载待审批列表失败');
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    fetchPending();
  }, [fetchPending]);

  const handleApprove = async (id: number) => {
    setActionInProgress(id);
    setError(null);
    try {
      await portfolioApi.approvePendingTrade(id);
      await fetchPending();
    } catch (err) {
      setError(err instanceof Error ? err.message : '批准操作失败');
    } finally {
      setActionInProgress(null);
    }
  };

  const handleReject = async (id: number) => {
    setActionInProgress(id);
    setError(null);
    try {
      await portfolioApi.rejectPendingTrade(id);
      await fetchPending();
    } catch (err) {
      setError(err instanceof Error ? err.message : '拒绝操作失败');
    } finally {
      setActionInProgress(null);
    }
  };

  if (total === 0 && !loading && !error) return null;

  return (
    <Card>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-base font-semibold">待审批模拟交易</h3>
        {total > 0 && <Badge variant="warning">{total}</Badge>}
      </div>
      {error && <InlineAlert variant="danger" message={error} className="mb-3" />}
      {loading ? (
        <div className="py-4 text-center text-sm text-[var(--color-text-secondary)]">加载中...</div>
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <div key={item.id} className="card-surface flex items-start justify-between rounded-xl p-4">
              <div className="flex-1">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <span>{item.symbol}</span>
                  <Badge variant={item.side === 'buy' ? 'success' : 'danger'}>
                    {item.side === 'buy' ? '买入' : '卖出'}
                  </Badge>
                  <span>{item.quantity}股 @ ¥{item.price.toFixed(2)}</span>
                </div>
                {item.llmReasoning && (
                  <p className="mt-1 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
                    {item.llmReasoning}
                  </p>
                )}
                <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
                  {item.createdAt}
                </div>
              </div>
              <div className="ml-4 flex gap-2">
                <button
                  onClick={() => handleApprove(item.id)}
                  disabled={actionInProgress === item.id}
                  className="btn-primary rounded-lg px-3 py-1.5 text-xs"
                >
                  ✅ 批准
                </button>
                <button
                  onClick={() => handleReject(item.id)}
                  disabled={actionInProgress === item.id}
                  className="btn-secondary rounded-lg px-3 py-1.5 text-xs"
                >
                  ❌ 拒绝
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
```

- [ ] **步骤 2：提交**

```bash
git add src/components/portfolio/PendingTradesTab.tsx
git commit -m "feat(web): add PendingTradesTab component"
```

---

### 任务 13：前端 — SimTradingToggle 组件

> **[Pass 2 修订 I8]** catch 块设置 error state 并渲染错误提示，不静默吞掉错误。

**agent: gem-implementer**

**文件：**
- 创建：`apps/dsa-web/src/components/portfolio/SimTradingToggle.tsx`

- [ ] **步骤 1：创建 `SimTradingToggle.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react';
import { portfolioApi } from '../../api/portfolio';
import { InlineAlert } from '../common';

interface SimTradingToggleProps {
  onChange?: (approvalRequired: boolean) => void;
}

export default function SimTradingToggle({ onChange }: SimTradingToggleProps) {
  const [approvalRequired, setApprovalRequired] = useState(false);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    setError(null);
    try {
      const config = await portfolioApi.getSimTradingConfig();
      setApprovalRequired(config.approvalRequired);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载配置失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleToggle = async () => {
    const newValue = !approvalRequired;
    setUpdating(true);
    setError(null);
    try {
      const config = await portfolioApi.updateSimTradingConfig({ approvalRequired: newValue });
      setApprovalRequired(config.approvalRequired);
      onChange?.(config.approvalRequired);
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新配置失败');
    } finally {
      setUpdating(false);
    }
  };

  if (loading) return null;

  return (
    <div className="flex flex-col gap-1">
      <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
        <span className="text-[var(--color-text-secondary)]">模拟交易需要手动审批</span>
        <button
          type="button"
          role="switch"
          aria-checked={approvalRequired}
          onClick={handleToggle}
          disabled={updating}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
            approvalRequired ? 'bg-[var(--color-accent)]' : 'bg-[var(--color-border)]'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              approvalRequired ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      </label>
      {error && <InlineAlert variant="danger" message={error} className="mt-1" />}
    </div>
  );
}
```

- [ ] **步骤 2：提交**

```bash
git add src/components/portfolio/SimTradingToggle.tsx
git commit -m "feat(web): add SimTradingToggle component"
```

---

### 任务 14：前端 — 集成到 PortfolioPage

**agent: gem-implementer**

**文件：**
- 修改：`apps/dsa-web/src/pages/PortfolioPage.tsx`

- [ ] **步骤 1：在 PortfolioPage.tsx 顶部新增 imports**

```typescript
import TradeEditModal from '../components/portfolio/TradeEditModal';
import PendingTradesTab from '../components/portfolio/PendingTradesTab';
import SimTradingToggle from '../components/portfolio/SimTradingToggle';
```

- [ ] **步骤 2：在组件内新增状态**

在组件函数内部已有状态声明附近添加：

```typescript
const [editingTrade, setEditingTrade] = useState<PortfolioTradeListItem | null>(null);
const [approvalRequired, setApprovalRequired] = useState(false);
```

- [ ] **步骤 3：在页面设置区域（账户选择器附近）添加 `SimTradingToggle`**

在账户选择器/操作栏区域添加：

```tsx
<SimTradingToggle onChange={setApprovalRequired} />
```

- [ ] **步骤 4：在交易流水表每行添加编辑按钮**

在交易流水表的操作列（已有删除按钮处）添加编辑按钮：

```tsx
<button
  type="button"
  title="编辑"
  onClick={() => setEditingTrade(trade)}
  className="text-[var(--color-text-secondary)] hover:text-[var(--color-accent)] transition-colors"
>
  ✏️
</button>
```

- [ ] **步骤 5：在交易建议区块下方添加 `PendingTradesTab`**

```tsx
{approvalRequired && (
  <PendingTradesTab accountId={typeof selectedAccount === 'number' ? selectedAccount : undefined} />
)}
```

- [ ] **步骤 6：在页面末尾（return 之前的 `</div>` 之前）添加 Modal**

> **[Pass 2 修订 I7]** `onSaved` 回调不再接收 warnings 参数（oversell 现在是 400 硬阻断，在 Modal 内部展示错误）。

```tsx
{editingTrade && (
  <TradeEditModal
    trade={editingTrade}
    onClose={() => setEditingTrade(null)}
    onSaved={() => {
      setEditingTrade(null);
      // 刷新交易列表和持仓快照
      fetchTrades();
      fetchSnapshot();
    }}
  />
)}
```

- [ ] **步骤 7：验证前端构建**

运行：`cd apps/dsa-web && npm run lint && npm run build`
预期：无错误

- [ ] **步骤 8：提交**

```bash
git add src/pages/PortfolioPage.tsx
git commit -m "feat(web): integrate TradeEditModal, PendingTradesTab, SimTradingToggle into PortfolioPage"
```

---

## Wave 6：前端测试（依赖 Wave 5 的组件）

### 任务 15：前端组件测试

> **[Pass 2 新增 I3]** 使用 Vitest + @testing-library/react 覆盖三个新增前端组件。

**agent: gem-implementer**

**文件：**
- 创建：`apps/dsa-web/src/components/portfolio/TradeEditModal.test.tsx`
- 创建：`apps/dsa-web/src/components/portfolio/PendingTradesTab.test.tsx`
- 创建：`apps/dsa-web/src/components/portfolio/SimTradingToggle.test.tsx`

- [ ] **步骤 1：创建 `TradeEditModal.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import TradeEditModal from './TradeEditModal';
import { portfolioApi } from '../../api/portfolio';
import type { PortfolioTradeListItem } from '../../types/portfolio';

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    updateTrade: vi.fn(),
  },
}));

const mockTrade: PortfolioTradeListItem = {
  id: 1,
  accountId: 1,
  symbol: '600519',
  market: 'cn',
  currency: 'CNY',
  tradeDate: '2026-04-20',
  side: 'buy',
  quantity: 100,
  price: 1800,
  fee: 5,
  tax: 0,
  note: '',
  createdAt: '2026-04-20T10:00:00',
};

describe('TradeEditModal', () => {
  const onClose = vi.fn();
  const onSaved = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('渲染所有可编辑字段', () => {
    render(<TradeEditModal trade={mockTrade} onClose={onClose} onSaved={onSaved} />);
    expect(screen.getByDisplayValue('100')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1800')).toBeInTheDocument();
    expect(screen.getByText('600519')).toBeInTheDocument();
  });

  it('展示只读上下文信息（symbol / side / date）', () => {
    render(<TradeEditModal trade={mockTrade} onClose={onClose} onSaved={onSaved} />);
    expect(screen.getByText(/600519/)).toBeInTheDocument();
    expect(screen.getByText(/买入/)).toBeInTheDocument();
    expect(screen.getByText(/2026-04-20/)).toBeInTheDocument();
  });

  it('提交时调用 updateTrade API', async () => {
    vi.mocked(portfolioApi.updateTrade).mockResolvedValue({ trade: mockTrade });
    render(<TradeEditModal trade={mockTrade} onClose={onClose} onSaved={onSaved} />);

    const priceInput = screen.getAllByRole('spinbutton')[1]; // price field
    fireEvent.change(priceInput, { target: { value: '1900' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => {
      expect(portfolioApi.updateTrade).toHaveBeenCalledWith(1, expect.objectContaining({ price: 1900 }));
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it('oversell 400 错误展示 InlineAlert', async () => {
    vi.mocked(portfolioApi.updateTrade).mockRejectedValue({
      response: { data: { error: 'oversell', message: 'Oversell detected', violations: ['卖出 600519 导致 oversell'] } },
    });
    render(<TradeEditModal trade={mockTrade} onClose={onClose} onSaved={onSaved} />);

    const qtyInput = screen.getAllByRole('spinbutton')[0];
    fireEvent.change(qtyInput, { target: { value: '50' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('无变更时直接关闭', async () => {
    render(<TradeEditModal trade={mockTrade} onClose={onClose} onSaved={onSaved} />);
    fireEvent.click(screen.getByText('保存'));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(portfolioApi.updateTrade).not.toHaveBeenCalled();
  });
});
```

- [ ] **步骤 2：创建 `PendingTradesTab.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PendingTradesTab from './PendingTradesTab';
import { portfolioApi } from '../../api/portfolio';

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    getPendingSimTrades: vi.fn(),
    approvePendingTrade: vi.fn(),
    rejectPendingTrade: vi.fn(),
  },
}));

const mockPendingItems = [
  {
    id: 1, accountId: 1, symbol: '600519', side: 'buy' as const,
    quantity: 100, price: 1850, fee: 5, tax: 0,
    note: '', llmReasoning: 'MACD 金叉', status: 'pending' as const,
    createdAt: '2026-04-24T09:30:00', reviewedAt: null, reviewerNote: '',
  },
];

describe('PendingTradesTab', () => {
  beforeEach(() => vi.clearAllMocks());

  it('渲染待审批列表', async () => {
    vi.mocked(portfolioApi.getPendingSimTrades).mockResolvedValue({
      items: mockPendingItems, total: 1, page: 1, pageSize: 50,
    });
    render(<PendingTradesTab accountId={1} />);
    await waitFor(() => expect(screen.getByText('600519')).toBeInTheDocument());
    expect(screen.getByText(/MACD 金叉/)).toBeInTheDocument();
  });

  it('批准操作调用 API 并刷新', async () => {
    vi.mocked(portfolioApi.getPendingSimTrades).mockResolvedValue({
      items: mockPendingItems, total: 1, page: 1, pageSize: 50,
    });
    vi.mocked(portfolioApi.approvePendingTrade).mockResolvedValue({});
    render(<PendingTradesTab accountId={1} />);
    await waitFor(() => screen.getByText('✅ 批准'));
    fireEvent.click(screen.getByText('✅ 批准'));
    await waitFor(() => expect(portfolioApi.approvePendingTrade).toHaveBeenCalledWith(1));
  });

  it('拒绝操作调用 API 并刷新', async () => {
    vi.mocked(portfolioApi.getPendingSimTrades).mockResolvedValue({
      items: mockPendingItems, total: 1, page: 1, pageSize: 50,
    });
    vi.mocked(portfolioApi.rejectPendingTrade).mockResolvedValue({});
    render(<PendingTradesTab accountId={1} />);
    await waitFor(() => screen.getByText('❌ 拒绝'));
    fireEvent.click(screen.getByText('❌ 拒绝'));
    await waitFor(() => expect(portfolioApi.rejectPendingTrade).toHaveBeenCalledWith(1));
  });

  it('空列表时不渲染', async () => {
    vi.mocked(portfolioApi.getPendingSimTrades).mockResolvedValue({
      items: [], total: 0, page: 1, pageSize: 50,
    });
    const { container } = render(<PendingTradesTab accountId={1} />);
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it('加载失败时展示错误', async () => {
    vi.mocked(portfolioApi.getPendingSimTrades).mockRejectedValue(new Error('Network Error'));
    render(<PendingTradesTab accountId={1} />);
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });
});
```

- [ ] **步骤 3：创建 `SimTradingToggle.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SimTradingToggle from './SimTradingToggle';
import { portfolioApi } from '../../api/portfolio';

vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    getSimTradingConfig: vi.fn(),
    updateSimTradingConfig: vi.fn(),
  },
}));

describe('SimTradingToggle', () => {
  const onChange = vi.fn();

  beforeEach(() => vi.clearAllMocks());

  it('初始状态从 API 获取', async () => {
    vi.mocked(portfolioApi.getSimTradingConfig).mockResolvedValue({
      approvalRequired: false, simTradingEnabled: true, simTradingAccountId: 1,
    });
    render(<SimTradingToggle onChange={onChange} />);
    await waitFor(() => expect(screen.getByRole('switch')).toBeInTheDocument());
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('切换时调用 updateSimTradingConfig', async () => {
    vi.mocked(portfolioApi.getSimTradingConfig).mockResolvedValue({
      approvalRequired: false, simTradingEnabled: true, simTradingAccountId: 1,
    });
    vi.mocked(portfolioApi.updateSimTradingConfig).mockResolvedValue({
      approvalRequired: true, simTradingEnabled: true, simTradingAccountId: 1,
    });
    render(<SimTradingToggle onChange={onChange} />);
    await waitFor(() => screen.getByRole('switch'));
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => {
      expect(portfolioApi.updateSimTradingConfig).toHaveBeenCalledWith({ approvalRequired: true });
      expect(onChange).toHaveBeenCalledWith(true);
    });
  });

  it('更新失败时展示错误', async () => {
    vi.mocked(portfolioApi.getSimTradingConfig).mockResolvedValue({
      approvalRequired: false, simTradingEnabled: true, simTradingAccountId: 1,
    });
    vi.mocked(portfolioApi.updateSimTradingConfig).mockRejectedValue(new Error('更新失败'));
    render(<SimTradingToggle onChange={onChange} />);
    await waitFor(() => screen.getByRole('switch'));
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });
});
```

- [ ] **步骤 4：运行前端测试**

运行：`cd apps/dsa-web && npx vitest run src/components/portfolio/`
预期：全部通过

- [ ] **步骤 5：提交**

```bash
cd apps/dsa-web
git add src/components/portfolio/*.test.tsx
git commit -m "test(web): add TradeEditModal, PendingTradesTab, SimTradingToggle component tests"
```

---

## Wave 7：文档与集成验证（依赖 Wave 1-6）

### 任务 16：更新文档

> **[Pass 2 修订 I9]** 新增 `docs/sim-trading.md` 更新步骤。

**agent: gem-implementer**

**文件：**
- 修改：`docs/CHANGELOG.md`
- 修改：`docs/sim-trading.md`

- [ ] **步骤 1：在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段追加条目**

```markdown
- [新功能] 支持编辑交易流水（quantity/price/fee/tax/note），编辑后自动重新校验 oversell（硬阻断）
- [新功能] 模拟交易审批开关：schedule 模式下 LLM 交易决策可进入待审批队列，支持手动批准/拒绝
- [新功能] 新增 PendingSimTrade 待审批表和相关 API（list/approve/reject/delete）
- [新功能] 新增 SIM_TRADING_APPROVAL_REQUIRED 配置项及运行时配置端点（持久化至 .env）
- [新功能] 前端新增交易编辑弹窗、待审批交易区块、审批开关 Toggle 组件
```

- [ ] **步骤 2：更新 `docs/sim-trading.md`**

在 `## 配置` 表格中追加审批开关配置项：

```markdown
| `SIM_TRADING_APPROVAL_REQUIRED` | `false` | 审批开关，开启后 schedule 模式下的交易决策进入待审批队列 |
```

在 `## 触发条件` 后新增 `## 审批模式` 段落：

```markdown
## 审批模式

当 `SIM_TRADING_APPROVAL_REQUIRED=true` 时，`--schedule` 模式下 LLM 产出的交易决策不会自动执行，而是写入 `PendingSimTrade` 待审批表。

- 用户可通过 Web 前端查看待审批交易、批准或拒绝
- 批准后按 LLM 原始建议价格执行，trade_date 使用 LLM 决策日期
- 审批开关仅影响 schedule 自动模式，API / bot 触发的模拟交易不受影响
- 配置可通过 `PUT /api/v1/portfolio/sim-trading/config` 运行时切换，变更自动持久化至 `.env`
```

- [ ] **步骤 3：提交**

```bash
git add docs/CHANGELOG.md docs/sim-trading.md
git commit -m "docs: update CHANGELOG and sim-trading.md with trade edit and approval features"
```

---

### 任务 17：全栈集成验证

**agent: gem-implementer**

**文件：** 无新增

- [ ] **步骤 1：运行后端全量门控**

运行：`./scripts/ci_gate.sh`
预期：所有阶段通过

- [ ] **步骤 2：运行后端单元测试**

运行：`python -m pytest tests/test_portfolio_trade_edit.py tests/test_sim_trading_approval.py tests/test_pending_sim_trade_api.py tests/test_sim_trading_config_api.py -v`
预期：全部通过

- [ ] **步骤 3：运行前端测试**

运行：`cd apps/dsa-web && npx vitest run src/components/portfolio/`
预期：全部通过

- [ ] **步骤 4：运行前端构建验证**

运行：`cd apps/dsa-web && npm run lint && npm run build`
预期：无错误

- [ ] **步骤 5：确认 `.env.example` 已更新**

运行：`grep SIM_TRADING_APPROVAL_REQUIRED .env.example`
预期：输出 `SIM_TRADING_APPROVAL_REQUIRED=false`

- [ ] **步骤 6：确认 `docs/sim-trading.md` 已更新**

运行：`grep SIM_TRADING_APPROVAL_REQUIRED docs/sim-trading.md`
预期：输出含审批开关配置项的行

---

## 依赖关系总结

```
Wave 1 (并行)
├── 任务 1: PendingSimTrade ORM
└── 任务 2: Config 配置项

Wave 2 (依赖 Wave 1, 并行)
├── 任务 3: update_trade_in_session (依赖: 无新 ORM)
└── 任务 4: PendingSimTrade CRUD — 使用 portfolio_write_session() (依赖: 任务 1)

Wave 3 (依赖 Wave 2, 并行)
├── 任务 5: update_trade_event — oversell 硬阻断 (依赖: 任务 3)
└── 任务 6: SimTradingService 改造 — 事务一致性 + 幂等含 pending (依赖: 任务 2, 4)

Wave 4 (依赖 Wave 3)
├── 任务 7: API Schema (依赖: 无，但逻辑关联 Wave 3)
└── 任务 8: API 端点 — 配置持久化 .env + approve/reject 统一校验 (依赖: 任务 5, 6, 7)

Wave 5 (依赖 Wave 4, 并行)
├── 任务 9: 前端类型 (依赖: 任务 7 的 Schema 设计)
├── 任务 10: 前端 API 方法 (依赖: 任务 9)
├── 任务 11: TradeEditModal — InlineAlert 错误展示 (依赖: 任务 10)
├── 任务 12: PendingTradesTab — error state (依赖: 任务 10)
├── 任务 13: SimTradingToggle — error state (依赖: 任务 10)
└── 任务 14: PortfolioPage 集成 (依赖: 任务 11, 12, 13)

Wave 6 (依赖 Wave 5)
└── 任务 15: 前端组件测试 (依赖: 任务 11, 12, 13)

Wave 7 (依赖 Wave 6)
├── 任务 16: 文档更新（CHANGELOG + sim-trading.md）
└── 任务 17: 全栈集成验证
```
