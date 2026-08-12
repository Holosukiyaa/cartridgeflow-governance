# CartridgeFlow 外挂卡片治理

本仓库是 CartridgeFlow 的外部、可拆卸治理脚手架。它使用 SQLite 卡片、确定性责任路由、检查器和只读浏览器管理多个正式代码仓，但目标产品不导入这里的代码、数据库或服务。

## 仓库边界

```text
CF WS/
├── AGENTS.md                       # AI 施工总入口
├── CartridgeFlow/                  # v0.7.0 工作台与共同内核
├── CartridgeFlow-runtime-shell/    # v0.6.0-SP / DR 独立运行壳
└── CartridgeFlow-governance/       # 外挂治理、知识卡与开发协作

CF WS 之外
└── CartridgeFlow-protocols/        # 协议本体唯一源
```

`CF WS` 根目录只容纳一个 AI 总入口和三个正式集成工作副本。任务工作树、临时克隆、归档、缓存和协议源必须放在工作区之外。产品源码仓只保留运行、构建、发布、测试以及独立使用必需的文档；架构、责任路由、知识、任务和 AI 施工材料归本仓库。

## 权威数据

| 数据库 | 内容 | 能否重建 |
| --- | --- | --- |
| `governance-source.sqlite` | 当前卡片、作用域、关系、规则和检查绑定 | 否 |
| `.data/governance-index.sqlite` | 当前代码、符号、依赖、合同和发现项 | 是 |
| `governance-ledger.sqlite` | 路由、检查、验收和知识同步事件 | 否 |

原则是：卡片无历史，审计有事件；全局目录不等于全局失效域；静态通过不等于产品通过；不确定性只能扩大验证范围。

## 文档入口

- [治理架构](docs/GOVERNANCE_ARCHITECTURE.md)：卡片角色、责任路由、合同分类和证据模型。
- [责任路由与当前缺口](docs/RESPONSIBILITY_ROUTING_AND_CURRENT_GAPS.md)：实现现状、P0 缺口与路由原则。
- [项目版本谱系](docs/PROJECT_STATUS_AND_LINEAGE.md)：v0.6.0、v0.6.0-SP 与 v0.7.0 的分叉关系。
- [产品体验架构](docs/PRODUCT_EXPERIENCE_ARCHITECTURE.md)：工作台产品决策。
- [协议重建输入](docs/protocol-rebuild/target-protocol-architecture.md)：协议重建目标与业务能力清单。
- [AGENTS.md](AGENTS.md)：AI 和工程师接手本体系的入口。

## 确定性闭环

```text
变更路径 / 公开合同
  -> Floor 与局部 Knowledge
  -> Boundary、生产者、消费者与 Scenario
  -> 审核过的检查计划
  -> static / floor / boundary / scenario / complete
  -> 精确依赖足迹写入 Ledger
```

语义搜索只能给出建议，不能决定所有权、依赖、合规或验收。未覆盖、含糊或知识锚点过期时，路由必须进入保守模式并扩大检查范围。

## 常用命令

```powershell
python scripts/governance_db.py
python scripts/check_workspace_layout.py
python scripts/check_detachability.py
python scripts/build_governance_index.py build
python scripts/build_governance_index.py
python scripts/sync_knowledge_anchors.py
python scripts/governance_ledger.py verify
python scripts/run_governance_checks.py --changed
python scripts/check_handoff_e2e.py
python scripts/check_removability.py
python -m unittest discover -s tests -v
```

完整、无作用域限制的 `run_governance_checks.py` 才能产生 `complete` 状态。局部检查只能说明对应阶段，不得把静态通过解释为产品通过。

## 卡片浏览器

```powershell
python -m pip install -r requirements-browser.txt
python scripts/launch_card_browser.py --port 8041
```

浏览器以只读方式打开三库并绑定 `127.0.0.1`。它统一展示卡片、作用域、关系、源码覆盖、公开合同、发现项、检查证据、影响查询和确定性任务上下文。不要再在产品仓维护第二套协议浏览器。
