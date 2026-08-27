# Privacy

ModelDial 采用本地优先设计。本文件描述开源 App 的默认行为；你选择连接的模型服务或本地 CLI 仍受其各自隐私政策和服务条款约束。

## 默认不会做什么

- 不内置第三方统计或广告 SDK。
- 不向 ModelDial 运营方上传用户提示词、模型回复、工具输入输出或会话 transcript 正文。
- 不把 API Key、Token 或自定义端点凭证写入扫描历史。

## 本地保存的数据

- App 配置、模型与 effort 选择、扫描历史和运行状态。
- 质量、耗时、Token、参考费用及错误状态等评测证据。
- 会话观察所需的有限元数据，例如 session ID、工作目录、模型、effort、生命周期时间和 transcript 路径；观察脚本不读取或保存 transcript 正文。
- API Key 保存在 macOS Keychain；执行时只在需要的进程边界内取用。

这些数据默认保存在当前用户的本机目录中。导出文件由用户主动选择保存位置。

## 网络请求

- 运行评测时，固定合成题目和模型回复会经过你选择的 Codex／Claude Code CLI 或模型 API。
- 自定义兼容端点会收到完成请求所需的模型参数和合成题目。
- 新运行开始时，App 默认从第一方只读价格目录获取 `current.json` 和对应的版本化价格快照，并在本机缓存 last-good 与本轮冻结副本；请求不包含扫描历史、模型回复、API Key 或自定义端点凭证。源码运行可通过 `MODELDIAL_PRICING_CATALOG_URL` 指定兼容目录或显式设为空。与普通静态文件请求一样，托管服务或 CDN 可能记录 IP 地址、请求时间、User-Agent、响应状态和传输字节数等常规访问日志。
- 只有 App 包内配置了 `ModelDialReferenceSnapshotURL` 时，ModelDial 才会从对应只读地址获取版本化参考榜单 JSON，并在本机缓存可用快照；不会上传本机评测结果。ModelDial 品牌发行候选使用公开的第一方地址，普通源码构建默认留空，也可通过 `MODELDIAL_REFERENCE_SNAPSHOT_URL` 显式指定兼容地址。与普通静态文件请求一样，托管服务或 CDN 可能记录 IP 地址、请求时间、User-Agent、响应状态和传输字节数等常规访问日志。
- 只有发行包配置了有效 Sparkle 更新清单和公钥时，手动或自动更新检查才会通过 HTTPS 请求更新清单；下载更新时会访问更新包地址。修复后的 unsigned preview（`preview.2` 起）默认不启用更新通道；已发布 `preview.1` 的例外见其版本说明。托管服务或 CDN 可能记录上述常规访问日志。
- App 明确关闭 Sparkle 系统画像上传（`SUSendProfileInfo=false`），不随更新检查发送 ModelDial 扫描历史、会话正文、模型配置或系统画像；自动下载默认关闭，可在设置中开启或关闭。

## 会话观察

启用会话观察时，ModelDial 会在本机 Codex 或 Claude Code 配置中安装 hook。该 hook 只发送上述有限元数据给本地 App，不发送会话正文。停用该功能后，可在源码目录运行 `python3 scripts/install_session_observer.py --uninstall`；卸载只移除 ModelDial 自己的 hook 和 helper，保留其他工具的 hook。

## 报告问题

隐私问题请按 [SECURITY.md](SECURITY.md) 的私密渠道报告，不要在公开 Issue 中附带密钥、真实会话内容或包含个人路径的完整日志。
