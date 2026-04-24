# 模拟交易模块

## 概述

批量 LLM 组合审查 + 模拟交易执行模块。在一轮股票分析完成后，收集所有分析结果和当前持仓快照，通过一次 LLM 调用从组合角度做出交易决策，然后自动在持仓系统中执行模拟买卖。

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SIM_TRADING_ENABLED` | `false` | 是否启用模拟交易 |
| `SIM_TRADING_ACCOUNT_ID` | 无 | 使用的持仓账户 ID（必须配置） |
| `SIM_TRADING_MAX_SINGLE_AMOUNT` | `100000` | 单笔交易最大金额（元） |
| `SIM_TRADING_DEFAULT_COMMISSION` | `5.0` | 默认交易佣金（元） |
| `SIM_TRADING_APPROVAL_REQUIRED` | `false` | 模拟交易审批开关。设为 `true` 时，`--schedule` 模式下的模拟交易不再自动执行，而是进入待审批队列，需要用户在 Web 前端手动批准或拒绝。仅影响 schedule 模式，API/bot 触发不受影响。 |

## 触发条件

- 仅在 `--schedule` 模式下自动触发
- 需要 `SIM_TRADING_ENABLED=true` 且 `SIM_TRADING_ACCOUNT_ID` 已配置
- 每日仅执行一次（幂等机制）

## 数据流

```
个股分析完成 (pipeline.run())
  → 收集 List[AnalysisResult]
  → 获取持仓快照 (positions + cash + recent_trades)
  → 构造 LLM 组合审查 prompt
  → 调用 LLM（单次调用，复用项目 LiteLLM 配置）
  → 解析 JSON 交易决策
  → 逐笔校验（现金/持仓/上限/A股手数）
  → 执行 record_trade() 写入持仓系统
```

## 安全护栏

- **幂等**：每日仅执行一次，通过 trade note 前缀 `[sim-trading]` + 日期去重
- **校验**：买入检查现金余额、卖出检查持仓数量、单笔金额上限
- **A 股手数**：买卖数量必须是 100 的整数倍
- **容错**：单笔交易失败不阻断后续执行，整个模块异常不影响分析主流程
- **LLM 输出校验**：json_repair 修复 + 逐字段校验双重防护

## 使用方法

1. 在 `.env` 中配置：
   ```
   SIM_TRADING_ENABLED=true
   SIM_TRADING_ACCOUNT_ID=1
   ```

2. 启动 schedule 模式：
   ```bash
   python main.py --schedule
   ```

3. 分析完成后自动执行模拟交易，结果记录在日志和持仓系统中。

### 审批开关

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `SIM_TRADING_APPROVAL_REQUIRED` | `false` | 是否开启模拟交易手动审批。开启后，schedule 模式下 LLM 产出的交易决策不再自动执行，而是保存到待审批队列（`pending_sim_trades` 表），需要用户在 Web 前端手动批准或拒绝后才执行入库。API 和 bot 触发的模拟交易不受此开关影响。 |

**审批流程**：

1. `--schedule` 模式执行分析后，LLM 产出交易建议
2. 如果 `SIM_TRADING_APPROVAL_REQUIRED=true`，交易建议写入 `pending_sim_trades` 表
3. 用户在 Web 前端的「待审批交易」区块查看决策详情和 LLM 推理
4. 点击「批准」执行交易（使用 LLM 原始建议价格），或点击「拒绝」放弃
5. 配置可通过 API 或前端 Toggle 热切换，变更持久化到 `.env` 文件

**注意**：审批通过时使用 LLM 决策时的建议价格，不重新获取实时行情。交易日期使用 LLM 决策生成时的日期。
