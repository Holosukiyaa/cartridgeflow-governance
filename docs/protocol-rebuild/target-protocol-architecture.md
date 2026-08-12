# 新协议目标架构

> 状态：设计草案，不是正式协议发布物。
>
> 本架构只表达 CartridgeFlow 当前目标业务，不包含任何旧协议身份、历史关系或迁移信息。

## 1. 核心原则

1. 四层是责任边界，不是四篇承载全部细节的大文档。
2. 业务域负责组织模块，协议模块负责拥有规则和数据合同。
3. 数据合同只定义跨边界稳定数据；内部临时对象不进入协议库。
4. 行为规则、状态转换和错误码必须是一等记录，不能藏在正文段落里。
5. Base 声明支持时，必须同时提供实现位置、正向测试和失败测试。
6. 上层只能依赖下层稳定事实，任何依赖不得形成环。
7. 已发布版本不可原地修改，语义变化发布新的 SemVer。
8. 新协议数据库只有一个现行来源，不保存旧体系内容。

## 2. 总体结构

```text
CF-FOUNDATION@1.0.0
  治理与基座
    base
    conformance
    publication
    change

CF-AUTHORING@1.0.0
  创作与数据
    intent
    capability
    flow
    data
    presentation
    integration
    composition

CF-DISTRIBUTION@1.0.0
  发行与信任
    package
    integrity
    trust
    installation
    exposure

CF-RUNTIME@1.0.0
  运行与交付
    host
    execution
    interaction
    recovery
    artifact
    delivery
```

## 3. 第一层：CF-FOUNDATION

### 3.1 `base` 模块

职责：声明一个 CartridgeFlow 实现实际支持的协议、模块、合同和适配器。

候选合同：

- `cartridgeflow.foundation.implementation`
- `cartridgeflow.foundation.support`

核心规则：

- 实现声明必须包含精确协议版本。
- 宣称支持的模块必须具有实现证据。
- 宣称支持的合同必须绑定可执行验证器。
- 缺失协议库、摘要不匹配或证据失效时启动失败关闭。

### 3.2 `conformance` 模块

职责：统一表达协议验证结果和阻断原因。

候选合同：

- `cartridgeflow.foundation.conformance-report`
- `cartridgeflow.foundation.finding`
- `cartridgeflow.foundation.evidence`

核心规则：

- 报告必须具有稳定状态、发现项和验证范围。
- blocker 必须阻止对应发布、安装或运行操作。
- 证据必须同时包含成功路径和失败路径。
- 找不到验证器不能降级为警告。

### 3.3 `publication` 模块

职责：治理协议源、不可变发布、摘要和产品只读副本。

候选合同：

- `cartridgeflow.governance.protocol-release`
- `cartridgeflow.governance.registry-lock`

核心规则：

- 正式协议只允许从权威 SQLite 发布。
- 发布必须是事务性的。
- 已发布版本不可修改。
- 产品副本必须锁定权威仓库提交和数据库摘要。

### 3.4 `change` 模块

职责：定义新体系内部未来版本变化的分类和批准条件。

候选合同：

- `cartridgeflow.governance.change`

核心规则：

- 兼容增加、兼容修复和破坏性变化必须对应正确 SemVer。
- 合同字段、行为、状态和错误码变化均属于协议变化。
- 变化必须在发布前通过影响范围和实现证据检查。

## 4. 第二层：CF-AUTHORING

### 4.1 `intent` 模块

职责：表达用户目标、语义节点、业务字段、审核和能力缺口。

候选合同：

- `cartridgeflow.intent.project`
- `cartridgeflow.intent.node`
- `cartridgeflow.intent.field`
- `cartridgeflow.intent.review`
- `cartridgeflow.intent.capability-gap`
- `cartridgeflow.intent.capability-proposal`

核心规则：

- 用户意图不得被当前能力库存裁剪。
- 未解析能力必须保留完整语义和节点身份。
- AI 匹配结果只是待审核提议。
- 用户拒绝只针对精确发布摘要，不永久屏蔽未来版本。
- 进入能力实现层和返回原节点必须保持同一节点身份。

### 4.2 `capability` 模块

职责：表达可复用能力的边界、公开端口、字段、依赖和发布证据。

候选合同：

- `cartridgeflow.capability.definition`
- `cartridgeflow.capability.port`
- `cartridgeflow.capability.field`
- `cartridgeflow.capability.dependency`
- `cartridgeflow.capability.verification`
- `cartridgeflow.capability.release`

核心规则：

- 能力粒度必须保留用户需要审核的语义边界。
- 公开输入输出必须由内部 Flow 明确消费和产生。
- 依赖必须固定精确版本和摘要。
- 发布前必须证明成功路径真实执行并产生非空主要输出。
- 发布物不可变，新的实现发布新版本。

### 4.3 `flow` 模块

职责：定义可执行 Flow、节点、边、执行计划和交互点。

候选合同：

- `cartridgeflow.flow.definition`
- `cartridgeflow.flow.node`
- `cartridgeflow.flow.edge`
- `cartridgeflow.flow.plan`
- `cartridgeflow.flow.decision`
- `cartridgeflow.flow.interaction`

核心规则：

- Flow 必须有唯一入口和至少一个可达终止状态。
- 成功路径必须包含至少一个真实执行节点。
- 节点类型必须声明所需输入、产生输出和允许效果。
- 执行计划必须确定性表达顺序、条件、分支和汇合。
- 决策和交互等待必须拥有显式恢复点。
- 孤立节点、悬空边、循环依赖和不可达出口必须失败关闭。

### 4.4 `data` 模块

职责：定义端口值、绑定、Store 访问和跨节点数据链。

候选合同：

- `cartridgeflow.data.value-type`
- `cartridgeflow.data.binding`
- `cartridgeflow.data.store-access`
- `cartridgeflow.data.output-write`
- `cartridgeflow.data.lineage`

核心规则：

- 数据来源和目标必须可静态定位。
- 绑定必须区分运行输入、节点输出、设置、资源和 Store。
- 缺少必需输入、类型不匹配和多生产者冲突必须失败关闭。
- 主要输出必须具有从真实执行节点到交付结果的完整数据链。
- 敏感值不得写入公开输出、协议库或诊断报告。

### 4.5 `presentation` 模块

职责：定义设置、设置绑定和能力拥有的 UI 声明。

候选合同：

- `cartridgeflow.presentation.settings`
- `cartridgeflow.presentation.settings-binding`
- `cartridgeflow.presentation.ui`

核心规则：

- 设置字段必须有类型、默认值、可见性和编辑范围。
- 设置绑定必须指向存在且类型兼容的目标。
- 宿主渲染设置不得获得能力未声明的权限。
- 包自有 UI 必须在隔离边界内运行。

### 4.6 `integration` 模块

职责：定义模型、工具、MCP、DLC 和外部资源的声明边界。

候选合同：

- `cartridgeflow.integration.model-binding`
- `cartridgeflow.integration.tool`
- `cartridgeflow.integration.tool-binding`
- `cartridgeflow.integration.resource`
- `cartridgeflow.integration.extension`

核心规则：

- 工具必须声明输入、输出、效果、幂等性和超时。
- 节点只能调用显式允许且已绑定的工具。
- 包自有扩展必须声明入口、操作、资源和权限。
- 外部资源必须通过宿主解析，凭据不得写入发行物。
- 未绑定模型、工具、资源或扩展时发布失败关闭。

### 4.7 `composition` 模块

职责：定义能力依赖解析、递归闭包和确定性物化。

候选合同：

- `cartridgeflow.composition.request`
- `cartridgeflow.composition.resolution`
- `cartridgeflow.composition.materialization`
- `cartridgeflow.composition.provenance`

核心规则：

- 每个语义节点必须解析到一个已审核的精确能力发布物。
- 递归依赖必须无环、版本固定且摘要一致。
- 共享传递依赖在一次物化中只能出现一次。
- 命名空间必须确定性生成并防止碰撞。
- 物化不得丢失能力身份、版本、摘要和来源。

## 5. 第三层：CF-DISTRIBUTION

### 5.1 `package` 模块

职责：定义 Cartridge 发行物、内容清单和依赖锁。

候选合同：

- `cartridgeflow.package.manifest`
- `cartridgeflow.package.content-entry`
- `cartridgeflow.package.dependency-lock`
- `cartridgeflow.package.entrypoint`

核心规则：

- 所有包成员必须出现在内容清单中。
- 路径必须规范化且不能逃逸包根目录。
- 包必须声明唯一入口和精确依赖锁。
- 发行物不得包含凭据、用户数据和运行历史。

### 5.2 `integrity` 模块

职责：定义内容摘要、清单摘要和签名载荷。

候选合同：

- `cartridgeflow.integrity.manifest`
- `cartridgeflow.integrity.signature-payload`
- `cartridgeflow.integrity.verification`

核心规则：

- 每个包成员必须具有内容摘要。
- 清单摘要必须覆盖身份、版本、入口和全部成员摘要。
- 签名只覆盖规范化载荷。
- 任一成员变化都必须导致完整性验证失败。

### 5.3 `trust` 模块

职责：定义发布者身份、信任范围和导入决策。

候选合同：

- `cartridgeflow.trust.publisher`
- `cartridgeflow.trust.signature`
- `cartridgeflow.trust.decision`

核心规则：

- 信任范围必须是系统、组织或当前工作区之一。
- 当前工作区不能授予系统或组织信任。
- AI 生成和新导入对象默认为待验证。
- 签名有效不等于业务可信或运行兼容。

### 5.4 `installation` 模块

职责：定义安装、激活、升级和失败回滚。

候选合同：

- `cartridgeflow.installation.request`
- `cartridgeflow.installation.plan`
- `cartridgeflow.installation.result`

核心规则：

- 安装前必须通过完整性、信任、兼容性和资源检查。
- 安装写入必须原子化。
- 失败安装不得留下可运行的半成品。
- 升级必须保留明确的目标版本和回滚点。

### 5.5 `exposure` 模块

职责：定义用户可见体验、公开设置和交付声明。

候选合同：

- `cartridgeflow.exposure.experience`
- `cartridgeflow.exposure.delivery`

核心规则：

- 用户可见入口、设置、主要输出和交付方式必须显式声明。
- 公开内容不得暴露内部节点、凭据和宿主实现细节。
- 交付声明必须能关联到运行时真实交付结果。

## 6. 第四层：CF-RUNTIME

### 6.1 `host` 模块

职责：定义宿主能力、运行目标和兼容性协商。

候选合同：

- `cartridgeflow.host.profile`
- `cartridgeflow.host.target`
- `cartridgeflow.host.compatibility`

核心规则：

- 宿主必须声明支持的状态、UI、工具和扩展能力。
- 发行物必须声明精确运行目标和必需能力。
- 缺失必需能力时不得进入执行。
- Python 与 Go 必须对同一输入产生等价协商结论。

### 6.2 `execution` 模块

职责：定义运行请求、执行实例、节点状态和运行错误。

候选合同：

- `cartridgeflow.execution.request`
- `cartridgeflow.execution.run`
- `cartridgeflow.execution.node-state`
- `cartridgeflow.execution.error`
- `cartridgeflow.execution.event`

核心规则：

- 每次运行必须绑定不可变发行物和唯一运行 ID。
- 节点只能按执行计划和允许状态转换运行。
- 运行错误必须保留稳定错误码、原始节点和可公开诊断。
- 运行完成必须具有明确成功出口和真实主要输出。

### 6.3 `interaction` 模块

职责：定义等待用户决定、恢复输入和交互时效。

候选合同：

- `cartridgeflow.interaction.pending`
- `cartridgeflow.interaction.response`

核心规则：

- 等待状态必须固定运行、节点、请求和恢复目标。
- 响应必须通过类型和权限检查。
- 重复、过期和不匹配响应必须失败关闭。

### 6.4 `recovery` 模块

职责：定义 Checkpoint、重试、恢复和取消。

候选合同：

- `cartridgeflow.recovery.checkpoint`
- `cartridgeflow.recovery.request`
- `cartridgeflow.recovery.result`

核心规则：

- Checkpoint 必须绑定运行、节点、状态版本和数据摘要。
- 恢复只能进入协议允许的目标状态。
- 重试不得重复已经提交的非幂等副作用。
- 恢复失败必须保留原运行事实，不得伪造成功。

### 6.5 `artifact` 模块

职责：定义运行产物的身份、所有权、摘要和可见性。

候选合同：

- `cartridgeflow.artifact.record`
- `cartridgeflow.artifact.content-reference`

核心规则：

- Artifact 必须绑定运行和产生节点。
- Artifact 路径必须位于运行隔离目录。
- 敏感 Artifact 不得作为公开输出。
- Artifact 摘要必须能够验证内容未被替换。

### 6.6 `delivery` 模块

职责：定义运行结果、主要输出和真实交付状态。

候选合同：

- `cartridgeflow.delivery.result`
- `cartridgeflow.delivery.receipt`

核心规则：

- 完成运行和完成交付是两个独立状态。
- 交付成功必须关联非空主要输出或明确外部回执。
- 数据链断裂、空输出和下游失败不得报告为已交付。
- 交付失败必须保留可重试性和原始错误。

## 7. 依赖方向

```text
CF-FOUNDATION
  <- CF-AUTHORING
  <- CF-DISTRIBUTION
  <- CF-RUNTIME

CF-AUTHORING
  <- CF-DISTRIBUTION
  <- CF-RUNTIME 只消费其可执行定义，不修改创作事实

CF-DISTRIBUTION
  <- CF-RUNTIME 只消费已验证发行物
```

禁止：

- Foundation 依赖具体创作、发行或运行实现。
- Authoring 根据某个宿主能力反向裁剪用户意图。
- Distribution 在打包时猜测未确认的能力实现。
- Runtime 修改发行物、能力定义或用户意图。
- 任意模块通过共享可变对象绕过正式合同。

## 8. 规则 ID

规则 ID 使用以下稳定格式：

```text
CF-<LAYER>.<MODULE>.<NUMBER>
```

示例：

```text
CF-AUTHORING.INTENT.001
CF-AUTHORING.FLOW.001
CF-DISTRIBUTION.INTEGRITY.001
CF-RUNTIME.RECOVERY.001
```

要求：

- 规则 ID 一经发布不得复用。
- 删除规则只能在新版本中标记为不再适用，不能把编号分配给其他含义。
- 正文、验证脚本、代码证据和错误发现都引用同一个规则 ID。

## 9. 数据合同准入标准

一个对象只有满足以下任一条件才进入协议库：

- 跨进程传递。
- 跨 Python 与 Go 传递。
- 写入持久化存储并需要未来读取。
- 进入或离开发行物。
- 在不同层或不同模块之间传递。
- 由第三方扩展生产或消费。
- 作为公开 API 请求或响应。

纯函数内部字典、页面临时状态、缓存对象和仅用于展示的派生对象不进入协议库。

## 10. 新 SQLite 必需记录

```text
protocol_layer
protocol_release
protocol_module
normative_rule
data_contract_family
data_contract_release
data_contract_usage
data_contract_example
state_machine
state_definition
state_transition
error_code
implementation_manifest
implementation_binding
implementation_evidence
artifact
document_section
registry_metadata
```

首版不包含旧来源、旧协议映射和旧体系迁移记录。

## 11. 设计验收

- [ ] 四层下的所有模块都有唯一职责和明确非职责。
- [ ] 业务能力清单中的每项能力都能归入一个主要模块。
- [ ] 跨层边界都具有唯一生产者和消费者。
- [ ] 候选合同均满足数据合同准入标准。
- [ ] 状态、行为和错误不再依附于零散正文。
- [ ] 依赖方向不存在环。
- [ ] 不需要任何旧协议身份才能解释本架构。

