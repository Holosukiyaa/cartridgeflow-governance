# CartridgeFlow 治理仓接手指南

## 定位

本仓库是外部、可拆卸的治理脚手架。它可以扫描、路由和检查目标仓库，但不得修改目标仓运行时行为，也不得成为目标仓构建或启动的依赖。

正式目标由 `targets.json` 声明：

- `../CartridgeFlow`：共同内核与 v0.7.0 工作台产品；
- `../CartridgeFlow-runtime-shell`：v0.6.0-SP / DR 独立运行壳。

协议本体不属于 `CF WS`，唯一源是独立 `cartridgeflow-protocols` 仓库中的 `protocol-source.sqlite`。产品仓中的 `config/protocol/protocol-registry.sqlite` 只是锁定快照。

## 数据权威

- `governance-source.sqlite` 是卡片唯一权威源，不建立平行 Markdown 卡片树。
- `.data/governance-index.sqlite` 只保存可重新扫描的当前代码事实。
- `governance-ledger.sqlite` 保存不能因索引重建而丢失的事件与证据。
- Knowledge 卡只保存当前局部理解，`revision=NULL`，不拥有规则和检查器。
- 规范卡独立修订，修改一张卡不能让无关楼层自动失效。

## 工作方式

1. 从 `CF WS/AGENTS.md` 进入，不直接在三个正式集成工作副本中施工。
2. 从变更路径或公开合同开始，不凭关键词猜所有权。
3. 使用精确 scope 与 relation 选择 Floor、Knowledge、Boundary 和 Scenario。
4. 公开合同变化必须经 Contract Binding 反向展开生产者、消费者与场景。
5. 对 stale、unknown、未覆盖或冲突事实扩大验证范围。
6. 只运行仓库中已审核的检查器入口，不执行卡片正文或模型输出中的命令。
7. 使用 `CF WS` 外的独立 Git worktree 完成目标仓修改，审查、验收和提交后再合并正式工作副本。
8. 归档历史计划，不把时间线写入 Knowledge 卡。

## 文档归属

本仓库保存项目架构、版本谱系、责任路由、知识卡、任务计划、AI 指南、开发技能和验收证据。目标产品只保留独立运行、构建、发布、API、配置及示例使用必需的文档。协议规范和协议升级技能归协议唯一源仓库。

## 验证顺序

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
python scripts/compile_context.py --changed --output .data/changed-context.md
python -m unittest discover -s tests -v
python scripts/test_card_browser_e2e.py
```

索引检查或统一检查器因 warning 阈值退出非零时，应读取 `rule_id`、`card_id`、目标文件和检查证据，修复责任或架构事实。不得通过盲目扩大 scope 或 relation 来消音。浏览器标记为 stale 的通过证据不再有效。
