# Security Policy

## Supported versions

在首次正式发行前，只维护 `main` 分支的最新代码。旧 commit、个人 fork 和非官方二进制不提供安全支持。

## Reporting a vulnerability

请使用 GitHub 仓库的 Private Vulnerability Reporting 私密报告安全问题。不要先创建公开 Issue，也不要提交 API Key、Token、真实会话内容或未脱敏日志。

报告中请包含：

- 受影响的 commit 或版本；
- 可复现的最小步骤；
- 实际影响与可能攻击路径；
- 已做的脱敏说明。

重点攻击面包括自定义模型端点、本地 CLI 执行、Keychain 取用、会话 hook、快照下载、导入导出，以及模型生成内容进入本地评分流程的边界。
