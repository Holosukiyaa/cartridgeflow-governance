---
name: cartridgeflow-governed-development
description: 在 CF WS 中实施受外挂治理的软件开发：先用治理索引进行责任路由，再建立同级独立 worktree，按正式合同代际修改 CartridgeFlow 或 Desktop Runner，运行原生与 Floor、Boundary、Scenario、complete 验收，最后同步 Knowledge 锚点与 Ledger。用于任何 CF WS 功能开发、修复、重构、发布、跨仓合同变更或治理工具维护；单纯创作业务卡带时同时使用 cartridgeflow-flow-author。
---

# CartridgeFlow 受治理开发

当前技能版本：`1.0.0`。以同目录 `VERSION` 为机器可读版本。

目标是在动手前给出正确施工路线，让治理负责导航和验收，而不是等检查失败后再猜。治理仓是本技能的唯一维护源；本机 Codex 目录只是安装镜像，不得单独修改。

## 开工读取

在任何编辑前依次读取：

1. `CF WS/AGENTS.md`。
2. `CartridgeFlow-governance/AGENTS.md`。
3. 本技能的 `references/workflow.md`。
4. 出现检查失败时再读取 `references/failure-routing.md`，按失败所有者处理。
5. 若任务是创作或修改具体业务卡带，再完整读取并使用 `cartridgeflow-flow-author`。

治理编译出的上下文负责回答“谁拥有、要带上谁、要跑哪些检查”。协议锁和目标仓当前源码负责回答“系统现在支持什么”。不要让本技能、Knowledge 正文或旧交付物凌驾于正式锁定合同。

## 强制工作流

1. **定义变更面**：从用户目标提取预计路径、公开合同和目标仓。未知时先用最窄合理路径路由，不凭关键词猜楼层。
2. **先建任务根目录**：使用 `C:\_HOLOLAB\worktrees\<task>\`。在其中为三个正式仓建立同级真实 Git worktree，并复制根 `AGENTS.md`。受影响仓使用任务分支；未修改仓使用 detached worktree。不要用目录联接或符号链接代替真实 worktree。
3. **准备依赖**：在任务产品 worktree 的 `src/intent-studio` 与 `src/capability-workshop` 运行 `npm ci`。先确认 Python、Node、Go 等目标仓原生工具可用。
4. **重建任务索引**：从任务治理 worktree 运行 `python scripts/build_governance_index.py build` 与 `verify`。三个仓同级时直接使用受版本控制的 `targets.json`，不要临时改检查器或产品构建配置来迁就路径。
5. **编译责任上下文**：路径必须写成 `target-id:relative/path`。公开合同变化同时传 `--contract`。阅读返回的 Floor、Knowledge、Boundary、Scenario、检查器和 finding；歧义、冲突、unknown 或公开合同变化时按治理结果扩大验证，不扩大代码范围。
6. **只在任务 worktree 编辑**：遵循目标仓既有模式。产品和 DR 不得读取、导入、启动、记录或要求治理存在。未发布协议代际不得进入实现；先核对产品协议锁、运行时能力和消费者。
7. **先跑目标仓原生证明**：从最小相关测试开始，再扩大到构建、协议审计和 conformance。UI 必须做真实浏览器行为与布局验证；跨仓合同必须验证真实消费者，不以生产者单测代替。
8. **再跑治理验收**：目标仓尚有变更时优先 `run_governance_checks.py --changed`；目标仓已经提交时显式传所有变更路径。区分 static、floor、boundary、scenario、complete，禁止把静态通过表述为产品通过。
9. **提交与合并分仓处理**：每个仓独立提交。确认正式副本干净且没有用户改动后，才允许 `--ff-only` 合并。不得覆盖、重置或清理不属于本任务的变更。
10. **最后同步知识**：只有源码已审核、原生证明与治理检查通过后，才运行 `sync_knowledge_anchors.py`。Knowledge 只保存当前理解；施工历史与失败运行留在 Ledger。同步后再次验证 source、index、ledger 与 finding。
11. **交付事实**：报告改了什么、正式合同代际、真实消费者证明、各验收状态、提交与合并状态、是否推送，以及仍存在的外部条件。未获明确授权不推送远端。

## 决策边界

- 治理路由精确且无冲突：按选中的责任区域施工。
- 路由为空或路径未归属：先修治理覆盖或请求用户决定，不把代码塞进“看起来接近”的楼层。
- 公开合同变化：强制带上 Boundary、生产者、消费者和场景；不只验证当前打开的楼层。
- 检查器与手工命令矛盾：先确认运行目录、targets、依赖和编码；环境正确后以仓库正式检查器为验收事实。
- 检测暴露产品缺陷：修复产品所有者代码并补回归测试。
- 检测暴露治理工具缺陷：只修改治理仓，不让产品适配治理。
- 需要新协议或新公开合同：停止局部补丁，先完成协议设计、发布和锁定流程。

## 失败纪律

检查失败时先分类再行动：环境、路由/索引、Knowledge 新鲜度、产品实现、合同边界、消费者场景、治理工具。不得通过放宽 scope、删除检查、改写证据、提前同步锚点或引入兼容假象让错误消失。

常见症状和确定处理见 `references/failure-routing.md`。标准命令和任务目录范式见 `references/workflow.md`。

## 版本维护

- `PATCH`：命令修正、措辞澄清、已知环境坑补充，不改变强制顺序。
- `MINOR`：新增仓库、检查阶段、兼容工作流或可选能力，旧流程仍成立。
- `MAJOR`：责任模型、正式仓边界、强制施工顺序或治理数据权威发生不兼容变化。

更新时必须在治理仓任务 worktree 中修改，提升 `VERSION`，校验技能，然后运行：

```powershell
python skills/cartridgeflow-governed-development/scripts/sync_local_skill.py --check
python skills/cartridgeflow-governed-development/scripts/sync_local_skill.py --install
python skills/cartridgeflow-governed-development/scripts/sync_local_skill.py --check
```

本机镜像与治理源不一致时，以治理源为准重新安装，不做双向合并。
