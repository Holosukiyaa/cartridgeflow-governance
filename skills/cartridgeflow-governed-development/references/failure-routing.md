# 治理失败责任路由

先看失败发生在哪个阶段，再处理该阶段的所有者。不要用产品改动掩盖治理环境问题，也不要用治理调整掩盖产品缺陷。

| 症状 | 所有者 | 正确动作 | 禁止动作 |
|---|---|---|---|
| `path must use target-id:relative/path` | 调用参数 | 改为 `cartridgeflow:...` 或 `desktop-runner:...` | 扩大所有 scope |
| `at least one --path or --changed artifact is required` | 调用时机 | 未提交时用 `--changed`；已提交时显式传本任务路径 | 假称没有影响范围 |
| index 指向旧仓或旧提交 | 任务环境 | 在任务治理 worktree 重建 index，确认 `targets.json` 的同级真实 worktree | 继续使用旧索引证据 |
| `knowledge-source-stale` | Knowledge 审核 | 先审阅变更和卡片解释；验收后同步锚点并记录 Ledger | 开工前或未审核时直接 sync |
| `tsc` / Vite / npm 命令不存在 | 前端依赖 | 在报错所指的任务 worktree 运行 `npm ci`，检查 Node engine | 修改检查器跳过构建 |
| Vite 报输入路径越出根目录 | worktree 布局 | 使用同级真实 worktree | 用 junction/symlink，或修改 Vite 配置迁就治理 |
| 检查器从治理仓同级找不到产品/DR | worktree 布局 | 在同一任务根建立三个真实同级 worktree，并放置根 `AGENTS.md` | 把临时 targets 或链接塞进正式 CF WS |
| Windows `gbk`/Unicode 编解码错误 | 命令环境或治理工具 | 先设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；若检查器仍失败再修治理仓 | 修改产品中文内容为 ASCII |
| 产品原生测试失败 | 对应产品 Floor | 修产品实现并补最小回归测试，再扩大验证 | 同步锚点或改治理规则让其变绿 |
| Boundary 失败 | 合同生产者/消费者 | 核对正式锁定合同、两端实现和发布代际，跑真实交接 | 引入未发布合同或兼容猜测 |
| Scenario 失败而单测通过 | 跨仓工作流 | 检查真实包、安装、设置应用、运行结果和失败路径 | 用生产者静态测试冒充交付通过 |
| detachability/removability 失败 | 依赖边界 | 删除产品/DR 对治理的运行引用，或修治理检查环境 | 给产品增加治理环境变量依赖 |
| static 通过但 floor/complete 未运行 | 验收状态 | 明确报告各状态并继续所需阶段 | 表述为“全部通过” |
| Ledger 因失败检查产生新事件 | 审计 | 保留真实失败事件，修复后追加成功证据 | 为保持 diff 小而删除审计历史 |
| 新需求只能靠新 schema/version 实现 | 协议治理 | 停止局部实现，先设计、发布、锁定，再开发生产者与消费者 | 私自发明 v2/v3 并只改一端 |

## 三步诊断法

1. **确认命令环境**：当前目录、目标提交、targets、依赖、编码、工具链。
2. **确认责任事实**：finding 的 `rule_id`、`card_id`、目标文件、Boundary/Scenario 和证据新鲜度。
3. **只修所有者**：环境问题修任务环境；产品问题修产品；治理算法问题修治理；协议缺口走协议发布。

连续两次修复仍在不同层反复失败时，停止试探，重新编译上下文并检查是否遗漏公开合同或消费者，不继续堆兼容补丁。
