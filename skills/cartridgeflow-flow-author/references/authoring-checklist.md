# CartridgeFlow Flow 创作检查清单

本清单用于实际修改卡带，不用于只讨论设计的任务。

## 1. 目标与边界

- 明确用户输入、主要交付物、外部能力和失败行为。
- 能用现有能力组合时不新增 Base 分支；领域逻辑归卡带或 DLC。
- 用户可见文字写成具体业务语言，不使用“处理数据”一类占位说明。
- 不在意图层泄露执行拓扑、模型参数或工具配置。

## 2. Root Flow 基线

- 顶层包含 `start`、`protocol`、`cartridge_id` 和 `states`。
- `start` 指向真实 `control` 起点；不能让缺失入口的空 Flow 被误判成功。
- 所有可执行节点从起点可达，并能到达成功或失败终点。
- 非终点节点具有适当的非失败后继；可失败节点具有明确 `failure` 边。

## 3. 类型化输入输出

- 每个输入声明 `required`，并且只使用一个 `schema` 或 `schema_ref`。
- 每个输出声明 schema 与 `target`；目标类型只使用产品支持的 `store` 或 `artifact`。
- 下游绑定指向真实上游输出或已写入 Store 的键。
- 不把 artifact ID 当作待审核正文；审核界面应绑定可读文本。

## 4. 人工交互与循环

- `confirm_checkpoint` 的问题、字段和 Store 键完整声明。
- 驳回路径使用 `resume_target_node` 回到修订节点。
- 清除会阻止下一次暂停的审批键，并用 `copy_answer_to` 保存反馈。
- `loop.continue_when` 与 `exit_to` 明确，不依赖隐式真值转换。
- 验证时通过 pending-interaction API 回答，不直接编辑 Store。

## 5. fork / join

- 同一 fork 的边共享 `fork.id` 和来源，每条边有独立 `fork.branch`。
- 同一 join 的边共享 `join.id`、目标、模式和完整 `join.branches`。
- 每条 join 边还声明自身 `join.branch`。
- manifest 同时声明模式能力和 `execution_plan_join_runtime`。

## 6. 工具与资源

- 工具 ID 来自 manifest `allowed_tools`，参数满足工具 schema。
- 工具输出按工具自己的 `output` 名写入 Store；下游绑定这些真实键。
- 资源目录中的 provider、权限和绑定在运行前可用。
- 外部能力缺失时封闭失败，不伪造成功输出。
- DLC 源码和描述属于卡带，不进入 Base 的供应商分支。

## 7. 模型节点

- 声明 `kind=decision`、`executor=llm`、`effect=none`。
- 输出使用 `decision_envelope.v1`，并声明明确 `decision_contract.consume`。
- mock 路径需要与消费路径匹配的 `offline_decision`。
- 长代码或长文档只让模型生成紧凑核心，外壳由 `render_template` 组装。
- 为模型节点设置 `retry_policy`、足够的 `max_tokens` 和 `timeout_seconds`。
- 提示词要求只输出目标 JSON 或代码，不混入分析文本。

## 8. 错误合同

节点失败统一产生 `runtime_error_envelope.v1`。按稳定 `code` 和 `recovery_actions` 处理，不匹配原始 `message`。

常见类别：

- 输入与数据：`INPUT_REQUIRED`、`ARTIFACT_MISSING`、`DELIVERY_OUTPUT_MISSING`、`DECISION_CONSUME_FAILED`。
- Provider：`PROVIDER_TIMEOUT`、`PROVIDER_RATE_LIMITED`、`PROVIDER_EMPTY_RESPONSE`、`PROVIDER_AUTH_FAILED`。
- 工具与远程资源：`TOOL_TIMEOUT`、`TOOL_EXECUTION_FAILED`、`DEPENDENCY_UNAVAILABLE`。
- 卡带合同：`FLOW_CONTRACT_INVALID`、`ACTION_EXECUTOR_MISSING`、`NODE_EXECUTION_FAILED`。

多个步骤可以共享一个失败终点，精确错误仍由运行错误信封保存。

## 9. 结构预检

从治理仓运行：

```powershell
$product = Resolve-Path ..\CartridgeFlow
$skill = Resolve-Path .\skills\cartridgeflow-flow-author
python "$skill\scripts\preflight_flow.py" --repo "$product" --package "$product\.data\user\dev_cartridges\<cartridge-id>"
```

修复源文件或节点合同，不降低验证器要求。

## 10. 真实运行

- 先用 test/mock 模式低成本验证拓扑，再运行一次真实路径。
- 服务端修改后重启无 `--reload` 的 uvicorn，避免旧进程制造假失败。
- 轮询运行状态；暂停时通过公开 API 回答。
- 成功必须同时满足 `status=completed`、`delivery.status=delivered` 和主要输出身份匹配。
- artifact 交付还要检查底层文件存在、非空且 URL 可读。

## 11. 最终验证

```powershell
python "$skill\scripts\validate_authored_cartridge.py" --repo "$product" --package "$product\.data\user\dev_cartridges\<cartridge-id>" --run-id <run-id> --api-url http://127.0.0.1:8765
```

验证器会检查 UTF-8、占位乱码、可见文字、Flow blocker、资源与模型绑定、主要输出、artifact、Delivery 和数据链。

## 12. 交付前

- 有效输入与安全无效输入均已验证。
- 卡带包预检与产品相关 conformance 已通过。
- 未把本地绝对路径、凭据或运行数据写入卡带。
- 认证标签来自通过的认证 API，而不是手写元数据。
- 报告外部配置，不把尚未配置的能力描述成已可运行。
