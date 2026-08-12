# 当前业务能力清单

> 这是新协议重建的输入清单，不是协议正文，也不是旧协议目录的整理。
>
> 本文只描述当前产品希望保留和正式约束的业务能力。协议 ID、数据合同 ID、规则 ID 和版本号在后续设计阶段重新确定。

## 1. 清单用途

这份清单用于回答：

1. 当前产品到底需要哪些稳定业务边界。
2. 哪些边界需要数据合同，哪些边界需要行为规则。
3. 哪些行为已经有代码和测试，哪些行为还需要设计决定。
4. 四层协议如何承载完整业务，而不是只写四篇总纲。

“代码现在能做什么”不自动等于“协议应该允许什么”。代码观察结果、产品目标和用户决定出现冲突时，必须先形成设计决定，再写入协议。

## 2. 产品总体模型

```text
用户意图
  -> 意图编排
  -> 能力候选与人工审核
  -> 能力实现与真实验证
  -> 不可变能力发布
  -> 多能力递归组合
  -> 完整应用发行
  -> 宿主协商与运行
  -> 结果交付
```

系统有两个用户认知层和一个独立运行验证边界：

- 意图编排层：表达用户想做什么，允许保留未实现的能力缺口。
- 能力实现层：表达具体怎么做，包含 Flow、工具、数据、资源和发布证据。
- 独立运行边界：不依赖创作会话，加载签名发行物并执行、恢复和交付。

## 3. 第一层：治理与基座

### 3.1 Base 能力声明

状态：`confirmed`，需要重新设计正式合同。

- Base 身份、实现版本和协议生成版本。
- Base 能够验证的合同集合。
- Base 能够托管的适配器和扩展点。
- Base 对跨能力运行安全、存储边界和扩展宿主的责任。
- Base 不拥有任何具体业务能力，只提供通用基础能力。

相关实现：

- `src/core/protocol/base_manifest.py`
- `config/base/BASE_IMPLEMENTATION.json`
- `src/core/protocol/release_catalog.py`

### 3.2 兼容性与能力证明

状态：`confirmed`，需要从登记式证明提升为规则式证明。

- 实现声明与协议合同是否一致。
- 宿主、发行物、Flow 和能力之间是否兼容。
- 兼容性报告的状态、发现项、严重级别和阻断行为。
- 正向能力证据、失败证据和测试证据。
- 缺少证据时必须 fail-closed。

相关实现：

- `src/core/protocol/compatibility.py`
- `src/core/protocol/certification.py`
- `src/core/conformance/reporting.py`

### 3.3 协议变更治理

状态：`needs_design`。

- 新合同和新规则如何发布。
- 何种变化需要递增主版本、次版本或修订版本。
- 已发布合同如何保持不可变。
- 新版本如何声明兼容性，而不携带旧体系历史。
- 如何阻止只改正文、不改版本的隐性变更。

## 4. 第二层：创作与数据

### 4.1 意图编排

状态：`confirmed`，需要定义完整语义合同。

- 用户目标、方向草稿和方案链路。
- 语义节点的身份、目标、可编辑业务字段和来源。
- 节点之间的目的关系，而不是具体执行器关系。
- AI 生成建议、用户确认、用户拒绝和再次提议。
- 未解析能力缺口的保留、编辑和持久化。
- 从意图节点进入能力实现层时携带完整上下文。
- 能力发布后对原节点进行重新解析，不重建用户意图。

相关实现：

- `src/core/studio/authoring_service.py`
- `src/core/studio/creator_runtime_bridge.py`
- `src/core/llm/creator_discovery.py`
- `src/intent-studio/`

### 4.2 能力定义与发布

状态：`confirmed`，需要拆分数据合同和行为规则。

- 能力身份、名称、版本、信任范围和来源。
- 能力内部 Flow 及其节点、边、入口和成功出口。
- 能力公开输入、公开输出和创作空间可编辑字段。
- 精确依赖、依赖版本和依赖闭包。
- 能力验证的成功路径、失败路径和真实输出。
- 能力发布后的不可变性。
- 当前工作区可信、组织可信和系统可信的边界。

相关实现：

- `src/core/protocol/capability_cartridges.py`
- `src/core/protocol/capability_registry.py`
- `src/core/studio/capability_cartridges.py`
- `src/core/studio/trusted_node_presets.py`
- `src/capability-workshop/`

### 4.3 Flow、节点和执行计划

状态：`confirmed`，需要统一结构和行为语义。

- Flow 身份、协议版本、入口和终止状态。
- 节点类型、动作、效果、参数和执行器绑定。
- 节点之间的有向边、条件、分支和汇合。
- 输入绑定、输出写入和跨节点数据流。
- 决策、交互等待、恢复和重新进入。
- 执行计划、顺序、依赖和确定性约束。
- 成功路径必须真实产生声明输出。
- 空 Flow、占位节点、未绑定工具和伪造输出必须被拒绝。

相关实现：

- `src/core/protocol/flow_contract.py`
- `src/core/lab/graph.py`
- `src/core/lab/flow_analyzer.py`
- `src/core/orchestration/execution_plan.py`
- `src/core/cartridge/root_flow.py`

### 4.4 数据、Binding、Store 和资源

状态：`needs_design`，这是新合同体系的重点。

- 输入、输出、端口和字段的类型及语义。
- 运行输入、节点输出、Store 数据和持久化边界。
- 绑定来源、绑定目标、转换和缺失行为。
- 资源声明、资源所有权、资源解析和连通性检查。
- 模型、工具、MCP、文件、远程地址和凭据的引用方式。
- 哪些数据可公开，哪些数据只能在包内或宿主内流动。
- 数据链断裂、类型不匹配、资源不可用时的错误行为。

相关实现：

- `src/core/protocol/data_contracts.py`
- `src/core/protocol/authoring_contract.py`
- `src/core/studio/resource_catalog.py`
- `src/core/studio/resource_resolver.py`
- `src/core/studio/resources.py`
- `src/core/lab/node_executor.py`

### 4.5 设置、UI 和外部工具

状态：`confirmed`，需要明确宿主渲染与包所有权。

- 能力拥有的设置字段及其类型、默认值和编辑范围。
- 设置与 Flow 节点、运行输入和模型配置的绑定。
- 能力公开 UI、宿主渲染 UI 和包自有 UI 的边界。
- 工具声明、工具参数、效果、幂等性和超时。
- MCP 源代码解析、操作图、沙箱和包自有 DLC。
- 外部工具调用的权限、审计、失败和重试。

相关实现：

- `src/core/protocol/tool_plan.py`
- `src/core/extensions/`
- `src/core/lab/mcp/`
- `src/core/studio/resources.py`
- `src/core/studio/authoring_service.py`

## 5. 第三层：发行与信任

### 5.1 Cartridge 边界与内容清单

状态：`confirmed`。

- 发行物身份、版本和内容清单。
- Root Flow、能力来源、精确版本和依赖闭包。
- 包内自有 DLC、资源、配置模板和入口。
- 文件路径、文件类型、大小和符号链接安全约束。
- 来源链、能力身份和物化后的可追溯信息。

相关实现：

- `src/core/cartridge/validator.py`
- `src/core/cartridge/assets.py`
- `src/core/cartridge/dependencies.py`
- `src/core/cartridge/artifacts.py`
- `src/core/studio/release.py`

### 5.2 完整性、签名和信任

状态：`confirmed`，需要重新按新身份设计。

- 内容摘要和清单摘要。
- 签名输入、签名结果和签名验证。
- 发布者、组织和本地工作区的信任范围。
- 导入时的身份、完整性、兼容性和权限检查。
- 信任不能由本地 UI 自行授予。
- 任何签名成功都不能绕过合同和运行验证。

相关实现：

- `src/core/protocol/release_signing.py`
- `src/core/protocol/release_envelope.py`
- `src/core/cartridge/registry.py`
- `src/core/cartridge/permissions.py`

### 5.3 安装、升级与交付边界

状态：`needs_design`。

- 安装前置检查、目标宿主和资源要求。
- 升级时的版本选择、不可变依赖和回滚边界。
- 运行产物、用户可见结果和交付状态。
- 包失败、运行失败、交付失败之间的责任区分。
- 交付结果不得以“生成了文件”替代真实业务输出。

## 6. 第四层：运行与交付

### 6.1 Host Profile 与目标协商

状态：`confirmed`，需要统一 Python 与 Go 的合同。

- 宿主身份、版本、可用状态类型和 UI 模式。
- 发行物目标、协议版本和能力需求。
- 目标检查、兼容性报告和阻断原因。
- Python Base 与 Go Runtime Shell 的一致行为。

相关实现：

- `src/core/protocol/data_contracts.py`
- `CartridgeFlow-runtime-shell/shell/go/internal/runtimeprofile/profile.go`
- `CartridgeFlow-runtime-shell/shell/go/internal/runtimeprofile/profile.json`

### 6.2 调度、执行和状态

状态：`confirmed`，需要把散落状态收敛为新状态模型。

- 运行创建、排队、开始、执行、等待、完成和失败。
- 节点级状态、运行级状态和交付级状态的边界。
- 顺序、并发、条件分支和交互等待。
- 重试、取消、暂停、恢复和重新进入。
- 状态转换必须是有限、可验证和可审计的。

相关实现：

- `src/core/cartridge/runner.py`
- `src/core/runtime/state_machine.py`
- `src/core/runtime/manager.py`
- `CartridgeFlow-runtime-shell/shell/go/internal/runner/`
- `CartridgeFlow-runtime-shell/shell/go/internal/scheduler/`

### 6.3 错误、Checkpoint、Artifact 和交付

状态：`confirmed`，需要统一错误和生命周期定义。

- 稳定错误码、错误严重级别、原始节点和诊断上下文。
- Checkpoint 的保存、恢复、版本和安全边界。
- 运行 Artifact 的所有权、命名、摘要和清理。
- 成功输出、失败输出和交付结果的区别。
- 交付状态必须能反映真实数据链是否完成。

相关实现：

- `src/core/runtime/errors.py`
- `src/core/runtime/checkpoints.py`
- `src/core/cartridge/artifacts.py`
- `CartridgeFlow-runtime-shell/shell/go/internal/store/`
- `CartridgeFlow-runtime-shell/shell/go/internal/verify/`

## 7. 跨层边界清单

以下边界必须在新协议中各自有明确所有权，不能重复定义：

| 边界 | 生产方 | 消费方 | 需要的正式定义 |
|---|---|---|---|
| 意图节点 | 意图编排层 | 能力匹配与交接 | 语义节点合同、审核状态、缺口状态 |
| 能力发布物 | 能力实现层 | 注册表、组合器 | 不可变能力合同、来源、依赖和证据 |
| Flow | 能力实现层 | 编译器、运行器 | Flow 结构、节点、边和执行计划 |
| 数据绑定 | Flow 与资源系统 | 节点执行器、Store | 输入输出、来源目标、类型和缺失行为 |
| 发行物 | 发行层 | 宿主 | 清单、完整性、签名、信任和目标 |
| 运行状态 | 宿主 | 调度器、恢复器、交付器 | 状态机、错误、Checkpoint 和结果 |
| 验证证据 | Base 与运行验证 | 发布治理 | 正反例、报告、测试和阻断规则 |

## 8. 待用户确认的设计决定

- [ ] 哪些当前代码行为是目标行为，哪些只是历史实现遗留。
- [ ] 能力发布物和完整应用发行物是否使用同一套内容清单模型。
- [ ] 哪些设置属于能力公开合同，哪些只属于包或宿主内部配置。
- [ ] Store 的持久化、隔离、恢复和清理边界。
- [ ] 外部资源和凭据是否允许进入发行物，还是只能由宿主注入。
- [ ] 交互等待、用户决定和恢复是否属于同一状态模型。
- [ ] 运行失败后是否允许自动重试，哪些错误必须人工处理。
- [ ] 本地工作区可信发布的最小证明要求。
- [ ] 新协议仓库采用新仓库还是现有仓库的干净根历史。
- [ ] 现有旧格式发行物是否完全不再接受，还是仅作为产品外部导入工具处理。

## 9. 进入协议设计的条件

- [ ] 第 3 至第 6 节的能力边界没有未解释的空白。
- [ ] 第 7 节的生产者、消费者和所有权已经确认。
- [ ] 第 8 节的设计决定已经由用户确认或明确标记为不做。
- [ ] 每个业务模块至少有一条成功链路和一条失败链路可作为验收样例。
- [ ] 协议设计不会需要引用旧协议身份才能解释当前业务。
