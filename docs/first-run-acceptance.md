# 首启自动验收

在源码根目录运行：

```bash
./devtools/verify-first-run-acceptance.sh
```

命令会先注入官方 Radar 地址与 preview 更新身份并重新构建 `build/modeldial-candidate.app`，再在临时目录中完成以下检查：

- App bundle、官方 Radar／preview 更新配置、macOS 13 兼容性和 ad-hoc 签名门禁；
- 冻结后端连接本机假 endpoint，生成两个档位的快速计划；
- 执行两个档位各五题的完整扫描，并验证 10 条历史、两行可比较本机榜单、双向 pairwise evidence 和匹配的 route evidence；
- 运行 Swift `AppSessionStore` 来源切换合同，确认无当前模型时也能切到本机结果并自由比较两行。

不会调用真实模型、读取 Keychain 或访问用户正式 ModelDial 数据。JSON 和文本报告写入 `artifacts/first-run-acceptance/`。

已有最新候选包时，可跳过构建做快速重放：

```bash
./devtools/verify-first-run-acceptance.sh --skip-build
```

以下三项仍必须在最终候选上人工验收，自动报告会持续标记为 `manual_required`：

- 新 macOS 用户的 Keychain 授权与真实 SwiftUI 点击；
- Safari 下载后的 quarantine、Gatekeeper 与首次放行；
- 真实 API 的一次最小请求与服务端账单核对。
