# 可拆卸性验收

治理脚手架必须在两种模式下得到一致结果：

- `enabled`：目标进程可以看到治理仓位置；
- `absent`：目标进程获得一个确认不存在的治理路径，并且只使用目标仓自身的 `PYTHONPATH` 和二进制。

当前验收比较产品 Base 身份、默认协议、API 路由，分别构建 DR 并比较 `-trimpath` 二进制摘要，同时比较规范化后的 DR 运行状态。固定摘要不是永久产品事实，每次有效验收应以 Ledger 中最新证据为准。

跨仓交付是独立的必过场景。它通过工作台 API 创建临时 Flow，完成调优、兼容验证、认证和生产打包，得到带签名的 `CF-CRE@2` 卡带及 clean-v1 安装请求与计划。临时 DR 通过公开安装 API 消费这些内容，产品合同校验器再检查 DR 返回结果。

场景还必须证明：

- DR 能公开并持久化卡带设置；
- 缺少输入会被拒绝；
- 带本地资源角色的非空 passive UI 可以被解析；
- `remote_api` 宿主绑定会在真实运行中被调用；
- 篡改包返回合法的 clean-v1 失败结果，且不改变当前激活卡带；
- 临时产品与 DR 数据在结束后被清理。

通过统一入口运行两类证明：

```powershell
python scripts/run_governance_checks.py --timeout 600
```

机器可读结果写入 `.data/removability-report.json`、`.data/handoff-e2e-report.json` 和追加写入的 `governance-ledger.sqlite`。重建 `.data/governance-index.sqlite` 不得删除路由、检查、验收、诊断或知识同步事件。
