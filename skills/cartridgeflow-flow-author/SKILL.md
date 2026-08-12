---
name: cartridgeflow-flow-author
description: 创建、扩展、修复并验证可编辑的 CartridgeFlow 开发卡带与 Root Flow。用户要求把业务目标实现为 Flow、增加类型化流程节点、绑定模型或 MCP/DLC 工具、配置卡带公开 UI，或让卡带通过当前产品锁定协议与真实运行验收时使用。
---

# CartridgeFlow Flow 创作

在目标 CartridgeFlow 产品仓中创建满足业务目标的最小可执行 Flow。用户可见标题、说明和设置优先使用中文；代码标识、协议值、字段键、路径和外部工具参数保持原值。

## 开始前

1. 读取本技能的 [创作检查清单](references/authoring-checklist.md)。
2. 读取目标产品的 `README.md`、`config/protocol/protocol-registry.lock.json` 和 `config/base/BASE_IMPLEMENTATION.json`。
3. 通过产品 `core.protocol` API 读取锁定协议，不猜测版本，也不直接修改 SQLite。
4. 所有产品修改放在独立 Git worktree，审核后再合并正式工作副本。

## 工作流程

1. 先运行工作台仿真，失败时修复平台问题，不把已知平台错误转化为用户操作。
2. 判断任务是新建卡带、修改已有 Flow，还是增加资源节点。新卡带必须走工作台或公开 API 创建，不手写包骨架。
3. 用 `states` 和 `execution_plan.edges` 表达业务步骤；不要创建旧式 `next`、`control_edges` 或仅供画布显示的执行边。
4. 为每个 `process` 节点声明类型化输入、输出和必要的 `failure` 边；主链保持连续，起点和终点保持锁定。
5. 人工审核节点必须绑定上游已声明的文本输出。驳回重写使用 `answer_routes`、`resume_target_node`、`copy_answer_to` 与 `loop` 边，不直接改 Store。
6. 并行流程的 fork/join 必须声明稳定的 fork/join ID、分支名、完整分支集合和模式对应能力。
7. 工具只通过 manifest 中的工具 ID 绑定。MCP/DLC 内部实现不得伪装成用户业务节点。
8. `llm_prompt` 使用 `kind=decision`、`executor=llm`、`effect=none` 和明确的 `decision_envelope.v1` 消费合同；文件写入交给确定性节点。
9. 推理模型生成长内容时，让模型只输出紧凑核心，再用 `render_template` 确定性组装。为模型节点声明足够预算、超时和重试策略。
10. 每次有意义的修改后运行结构预检；最终写入后运行交付验证。
11. 分别验证一个有效、无破坏性的输入和一个安全的无效输入。无效输入必须走声明的失败路径或产生稳定错误信封。
12. 只有认证报告通过后，才通过认证 API 添加协议认证标签。

## 命令

以下命令从治理仓运行，并显式指定产品仓与卡带路径：

```powershell
$product = Resolve-Path ..\CartridgeFlow
$skill = Resolve-Path .\skills\cartridgeflow-flow-author

powershell -ExecutionPolicy Bypass -File "$skill\scripts\simulate_authoring.ps1"
python "$skill\scripts\preflight_flow.py" --repo "$product" --package "$product\.data\user\dev_cartridges\<cartridge-id>"
python "$skill\scripts\validate_authored_cartridge.py" --repo "$product" --package "$product\.data\user\dev_cartridges\<cartridge-id>" --run-id <run-id> --api-url http://127.0.0.1:8765
```

目标产品不依赖本技能。技能脚本只作为外部开发工具读取产品公开实现与本地卡带。

## 完成标准

交付时报告：创建或修改的业务节点、声明的资源、结构预检结果、真实运行与交付结果、无效输入结果，以及仍需用户提供的外部配置。图结构通过但没有真实 Delivery，不算完成。
