# CartridgeFlow 外挂卡片治理架构

## 当前结构

产品版本谱系与治理谱系是两条不同轴线：

```text
CartridgeFlow v0.6.0 共同内核
├── v0.6.0-SP：DR 运行台特化
└── v0.7.0：语义层与工作台扩展
```

治理仓是位于这些产品仓之外的可拆卸观察者。它不定义新的产品版本，也不被任何运行时导入。

依赖扫描当前支持五个真实 Floor。`src/core/protocol`、`src/core/runtime` 和 `src/core/studio` 之间存在大量双向源码依赖，因此仍属于共同内核，不伪装成彼此独立的楼层；它们由更窄作用域的 Knowledge 卡描述。

## 卡片角色

| 类型 | 当前数量 | 权威性 | 历史 | 职责 |
| --- | ---: | --- | --- | --- |
| Constitution | 1 | 规范 | 独立修订 | 外挂性与项目全局不变量 |
| Floor | 5 | 规范 | 独立修订 | 一个真实所有权与依赖域 |
| Boundary | 7 | 规范 | 独立修订 | 跨楼层产品交接事实 |
| Knowledge | 9 | 描述 | 无 | 一个楼层和精确作用域的当前可复用理解 |
| Task | 1 | 任务 | 无 | 目标、允许/禁止范围、必需卡片、检查与停止条件 |

Knowledge 卡是比 `AGENTS.md` 更深入、作用域更窄的局部接手说明。它解释用途、稳定概念、导航、工作方式和风险，但不能记录日期、修改者、历史施工顺序或流水线记录，也不拥有合规规则。

知识来源引用与内容摘要证明说明锚定到当前实现。经过审核的 `knowledge_assertion` 只把少量关键主张暴露给 Constitution 的确定性检查器；规则与可执行命令仍不属于 Knowledge 卡。

## 确定性治理闭环

```text
精确任务路径 / Git diff            公开合同 ID + version
             \                          /
              v                        v
    主 Floor + 局部 Knowledge     Contract Binding -> Boundary
             \                         |
              +---- 显式关系 + 生产者 + 消费者 + Scenario
                                       |
                                       v
                              已审核检查器计划
                                       |
                                       v
                 static / floor / boundary / scenario / complete
                                       |
                                       v
                              Ledger 精确依赖足迹
```

生成索引记录 Python AST、TypeScript Compiler API 和 Tree-sitter Go 依赖。索引中的文件与符号数量是可重建的当前事实，不写死在架构正文中；应从浏览器或 `governance-index.sqlite` 读取。

语义搜索不参与所有权、依赖、合规或验收。确定性上下文块与 FTS 只协助检索；未来 embeddings 也只能给出建议。

## 产品合同分类

产品锁定 Registry 是观测证据，不是卡片权威源。浏览器展示产品当前锁定的全部协议发布、使用关系及分类：

- `boundary`：当前跨楼层、跨进程、跨语言、跨仓库或发布包边界；
- `knowledge`：当前内部实现概念，只在局部有用；
- `legacy-review`：保留用于迁移审查的旧代际记录。

只有 `boundary` 的 Contract Binding 是新治理模型中的正式边界合同。其余记录继续可见，以免迁移擦除证据或把内部知识误升格为全局规则。

当前 `clean-v1` 权威源包含四层、22 个模块和 75 个候选正式合同。它们只有在已发布源 commit、产品 v4 lock、clean Base、运行兼容目录和正式 conformance 全部一致后，才是当前产品实际采用的协议事实。未来协议、旧代际合同和内部 Knowledge 不能仅凭所在仓库越过当前产品锁。

## 可拆卸边界

`check_removability.py` 分别在治理路径存在与不存在时运行产品和 DR 探针。两种模式下的产品 API、Base、协议目录事实、DR 构建摘要和运行状态必须一致。

独立交接场景通过工作台创作与生产打包 API 创建、验证、认证并打包临时 `CF-CRE@2` 卡带。产品响应携带 clean-v1 安装请求与计划，DR 通过公开安装 API 消费，产品合同验证器再校验 DR 结果。场景同时验证：

- DR 公开并保存卡带设置；
- 缺少必需输入会被拒绝；
- 篡改包不改变当前激活状态，并返回合法失败合同；
- 非空 passive UI 可访问；
- 本地资源角色可解析为宿主 `remote_api` 并在运行中实际调用。

## 证据与验收

三类存储拥有独立生命周期：

| 数据库 | 职责 | 可重建 |
| --- | --- | --- |
| `governance-source.sqlite` | 当前卡片、作用域、关系、规则与绑定 | 否 |
| `.data/governance-index.sqlite` | 当前代码、符号、依赖、合同与发现项 | 是 |
| `governance-ledger.sqlite` | 追加写入的路由、计划、结果、诊断、验收与知识同步事件 | 否 |

证据新鲜度由检查实际依赖的卡片、作用域、关系、文件、合同与绑定、检查器配置、路由器、上下文编译器、目标配置、选中闭包和检查计划共同决定。

全局 source/index 摘要只保护各自数据库完整性，不能让无关 Floor、Boundary 或 Scenario 的证据失效。精确足迹尚不确定时保守扩大验证范围，不能缩小范围。

CLI 与浏览器显示五种状态：`static`、`floor`、`boundary`、`scenario` 和 `complete`。有作用域的运行把未执行阶段标为 `not-run`；只有完整、无作用域运行才能计算 `complete`。产品正式协议审计与 conformance 是 blocker 级 Floor 检查。治理只报告不一致，不回写产品。

## 工作区边界

`check_workspace_layout.py` 约束 `CF WS` 根目录只包含根级 `AGENTS.md`，以及 `CartridgeFlow`、`CartridgeFlow-runtime-shell` 和 `CartridgeFlow-governance` 三个正式 Git 根，并阻止 DR、旧 `demos/`、协议源、计划、知识文档或旧协议浏览器重新进入产品仓。任务工作树、临时克隆、归档和协议唯一源均位于 `CF WS` 之外。
