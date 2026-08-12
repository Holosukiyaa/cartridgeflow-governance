# 受治理开发命令手册

## 目录范式

假设任务名为 `<task>`：

```text
C:\_HOLOLAB\worktrees\<task>\
|-- AGENTS.md
|-- CartridgeFlow\
|-- CartridgeFlow-governance\
`-- CartridgeFlow-runtime-shell\
```

三个仓必须是真实 Git worktree 且互为同级目录。这样 `targets.json`、Floor 检查、真实交接场景和可拆卸性检查都能使用仓库原生路径约定。即使某仓不修改，也建立 detached worktree 供检查读取。

## 1. 创建任务 worktree

在正式 `CF WS` 根目录只执行 Git 管理命令，不编辑文件：

```powershell
$TaskRoot = "C:\_HOLOLAB\worktrees\<task>"
New-Item -ItemType Directory -Force -Path $TaskRoot | Out-Null
Copy-Item -LiteralPath ".\AGENTS.md" -Destination (Join-Path $TaskRoot "AGENTS.md")

git -C .\CartridgeFlow worktree add (Join-Path $TaskRoot "CartridgeFlow") -b "feat/<task>"
git -C .\CartridgeFlow-governance worktree add (Join-Path $TaskRoot "CartridgeFlow-governance") -b "chore/<task>-governance"
git -C .\CartridgeFlow-runtime-shell worktree add --detach (Join-Path $TaskRoot "CartridgeFlow-runtime-shell") HEAD
```

若 DR 也要修改，为它创建任务分支；若产品不修改，则产品也使用 `--detach`。创建前先用 `git worktree list` 和 `git branch --list` 避免重名。

## 2. 准备产品依赖

```powershell
npm --prefix "$TaskRoot\CartridgeFlow\src\intent-studio" ci
npm --prefix "$TaskRoot\CartridgeFlow\src\capability-workshop" ci
```

`node_modules` 是任务 worktree 的本地依赖，不提交。若 Node engine 版本不足，先报告环境事实；不能通过降低 `package.json` 要求消除警告。

## 3. 建索引并路由

在任务治理 worktree：

```powershell
python scripts/build_governance_index.py build
python scripts/build_governance_index.py verify
python scripts/build_governance_index.py findings

python scripts/compile_context.py `
  --path cartridgeflow:src/<expected-path> `
  --goal "<用户目标>" `
  --output .data/task-context.md
```

公开合同变化增加：

```powershell
python scripts/compile_context.py `
  --path cartridgeflow:src/<producer-path> `
  --contract <contract-id> `
  --goal "<用户目标>" `
  --output .data/task-context.md
```

路径参数必须带目标 ID，例如 `cartridgeflow:` 或 `desktop-runner:`。先路由后编辑；新增文件使用它预计落入的父目录路由。

## 4. 原生验证

按路由返回的 checker 和目标仓说明选择命令。常见产品完整证明：

```powershell
python scripts/audit_protocol_registry.py
python -B scripts/run_conformance.py --quiet
```

常见前端证明：

```powershell
npm --prefix src/intent-studio test
npm --prefix src/intent-studio run typecheck
npm --prefix src/intent-studio run build
npm --prefix src/capability-workshop test
npm --prefix src/capability-workshop run typecheck
npm --prefix src/capability-workshop run build
```

DR 使用目标仓 `go.mod` 所在目录运行相关 `go test`。若 `go` 不在 PATH，先使用治理检查器已有的工具链发现方式或定位 `C:\_HOLOLAB\toolchains`，不要修改产品源码。

## 5. 治理验收

目标仓仍有未提交变更：

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
python scripts/run_governance_checks.py --changed
```

目标仓已经提交，`--changed` 无法发现差异时，显式列出本任务所有路径：

```powershell
python scripts/run_governance_checks.py `
  --path cartridgeflow:src/path/a.py `
  --path cartridgeflow:src/path/b.ts
```

完整收尾按治理仓 `AGENTS.md` 的当前验证顺序执行。不要把这里的示例当成固定检查器清单；治理源和路由结果是当前事实。

## 6. 提交、合并和知识同步

1. 在受影响目标仓任务分支提交已验证代码。
2. 在正式目标仓确认 `git status --short` 为空。
3. 使用 `git merge --ff-only <task-branch>` 合并目标仓。
4. 从治理任务 worktree 扫描合并后的同提交产品事实。
5. 人工确认 Knowledge 当前解释仍成立或同步正文后，再运行：

```powershell
python scripts/sync_knowledge_anchors.py `
  --actor codex `
  --reason "<本次审核与同步原因>"
python scripts/governance_ledger.py verify
python scripts/build_governance_index.py findings
```

6. 提交治理仓的 source/ledger 变化，再以 `--ff-only` 合并治理分支。
7. 在正式治理仓重建 index、验证 ledger 并确认 finding。

同步锚点不是“消除 stale 的按钮”。只有审阅过对应源码与 Knowledge 当前解释后才能执行。
