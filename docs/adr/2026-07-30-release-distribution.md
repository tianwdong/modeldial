# ADR：首个公开版本的构建、更新与分发

- 日期：2026-07-30
- 状态：已接受，实施中
- 最近修订：2026-08-08（增加不付费 unsigned preview 先行渠道）

## 背景

ModelDial 的公开仓库只包含 macOS App、本地 scanner、题包、脚本、测试和必要开发资源。官网、Cloudflare、远端评测运行器、快照发布和运营后台继续私有维护。

决策形成时，开源候选可以独立测试和构建，但构建仍由 `swiftc` 和 PyInstaller 脚本直接装配，版本与 build number 没有分离，产物使用 ad-hoc 签名，也没有自动更新、DMG、SBOM、正式公证或公开分发渠道。首个公开二进制必须先建立完整升级路径，否则早期用户只能手工重装。

2026-07-30 已完成第一段实现：Xcode Project／SwiftPM 接管 Swift App 和 Sparkle 装配，版本固定为 `0.1.0（Build 100）`，`build.sh` 与 `build-dev.sh` 已迁移到 Xcode 构建，Sparkle 2.9.4 的完整许可证已进入 bundle，更新控制器、设置页、公开 feed URL 与公钥也已接通。临时私钥导出／离线签名与隔离的 Build 99 → 100 R2 更新演练已经通过；长期安全备份、正式 archive／export、Developer ID、公证和发行产物仍待实现。

2026-08-08 决定在正式签名路径完成前保留一个不付费的人工预览渠道。预览版用于收集愿意手动放行的 macOS 用户反馈，不改变正式版本的签名、公证、自动更新和干净机器验收门槛；GitHub Release 的实际创建和上传仍需单独授权。

## 决策

### 1. 公开与私有边界

公开仓库包含：

- App 和 scanner 源码、Xcode Project、固定版本的 SwiftPM 依赖和本地构建入口。
- Sparkle 的公开 feed URL、公钥和客户端接入代码。
- 不含凭证的构建、产物校验和发布合同文档。
- 最终二进制所需的第三方许可证、Notice 和可复查 SBOM。

私有发布侧包含：

- Developer ID 证书、notary 凭证和 Sparkle EdDSA 私钥。
- R2 写入 Token、Bucket／DNS／缓存配置和带凭证的上传自动化。
- 官网、Cloudflare Worker、远端评测、快照发布和运营监控。

更新域名、下载路径和 Sparkle 公钥本身是客户端必须读取的公开信息，不把它们当作秘密；真正需要保护的是写入权限和私钥。

### 2. 构建所有权

- Xcode Project 负责 Swift App、Sparkle 框架、辅助组件、Info.plist、bundle 装配和嵌套签名关系。
- `build.sh` 保留为本地统一入口，负责准备固定的 Python 构建环境、冻结 scanner 后端、执行 smoke，并调用正式 App 构建。
- 正式发行使用 `xcodebuild archive`／export 路径；不继续依赖单条 `swiftc` 命令手工拼装带 Sparkle 的发行 App。
- Python 版本、PyInstaller 和传递构建依赖必须固定；构建记录同时保存 Xcode、Swift、macOS SDK 和 Python 版本。
- 修改 scanner、scripts 或 questions 后仍必须重新冻结后端，不能用 Swift 开发构建冒充正式候选。

### 3. 版本合同与产物

首个正式候选目标（付费签名路径）：

- Git tag：`v0.1.0`
- `CFBundleShortVersionString`：`0.1.0`
- `CFBundleVersion`：`100`
- 平台：macOS 13+，Apple Silicon

首个候选产物命名：

```text
modeldial-0.1.0-macos-arm64.dmg
modeldial-0.1.0-build-100-macos-arm64.zip
SHA256SUMS
modeldial-0.1.0-sbom.spdx.json
```

后续 build number 只增不减。DMG 面向人工安装和 Homebrew Cask；ZIP 只包含完整 `.app`，供 Sparkle 原子更新。scanner 不建立独立热更新通道，避免 Swift、Native Bridge DTO 和冻结后端错配。

### 4. App 内更新

- 使用固定精确版本的 Sparkle 2，并由独立更新控制器持有 `SPUStandardUpdaterController`。
- 设置页增加“软件更新”，展示当前版本／build、立即检查、自动检查和自动下载；自动下载默认关闭。
- 保留明确的自动检查授权；关闭 Sparkle 系统画像上传，不引入业务遥测。
- App 只读取一个权威 appcast，不同时比较 R2 和 GitHub 哪个更新。
- 正式 stable 更新至少要求 HTTPS、Developer ID、公证和 Sparkle EdDSA 更新包签名；签名 appcast 上线前必须完成私钥备份与恢复演练。明确标注为 unsigned／unnotarized 的预览渠道可以只依赖 Sparkle EdDSA 包与 feed 签名，但不得冒充正式发行。
- 安装／重启更新必须覆盖活跃扫描和运行恢复验收，不能静默破坏历史。

### 5. 托管与安装渠道

R2 自定义域名是自动更新的唯一主源，路径按平台和渠道隔离：

```text
https://updates.modeldial.com/macos/stable/appcast.xml
https://updates.modeldial.com/macos/preview/appcast.xml
https://updates.modeldial.com/macos/releases/0.1.0/modeldial-0.1.0-build-100-macos-arm64.zip
https://updates.modeldial.com/macos/releases/0.1.0/modeldial-0.1.0-macos-arm64.dmg
```

- `appcast.xml` 使用不缓存或极短缓存；版本化 ZIP／DMG 使用长缓存和 `immutable`。
- 已发布的版本化对象永不覆盖；更正内容必须提高 build number 并使用新路径。
- GitHub Releases 保存相同版本的源码、DMG、ZIP、校验和、SBOM 和发布说明，作为公开镜像与人工兜底，不作为第二自动更新权威。
- Homebrew Tap 的 Cask 下载同一正式 DMG，并固定版本与 SHA-256；不提供 `curl | sh` 安装器，也不指导用户删除 quarantine 绕过 Gatekeeper。

### 5A. 不付费 `unsigned preview` 先行渠道

在没有 Apple Developer Program 会员资格时，允许先发布带有明确预览标签的人工安装包。该渠道与正式 `v0.1.0` 分离：

- GitHub Release 标签固定为 `v0.1.0-preview.1`；App 的 marketing version／build 仍为 `0.1.0`／`100`，预览标签不能省略或改写为正式版本。
- 资产固定为 `modeldial-0.1.0-preview.1-macos-arm64.dmg`、`modeldial-0.1.0-preview.1-build-100-macos-arm64.zip`、`SHA256SUMS` 和 `modeldial-0.1.0-preview.1-sbom.spdx.json`。DMG 内附 `UNSIGNED_PREVIEW.txt`，再次说明 unsigned／unnotarized 状态和人工放行步骤。
- 发布正文必须醒目标出 unsigned／unnotarized、无 Developer ID、无 Apple notarization、无 Intel 支持，并附 SHA-256 校验方法。不能声称普通用户下载后直接双击即可打开，也不能把本预览版列入正式 stable appcast 或 Homebrew Cask。连续预览更新只能进入独立 preview appcast，并继续保留相同限制说明。
- 预览安装仅验证候选包构建、签名类型记录、DMG 挂载／拖入 `Applications`、SHA-256 和“系统设置 → 隐私与安全性 → 仍要打开（Open Anyway）”人工放行路径；这些证据不等于 Developer ID、secure timestamp、Apple notarization、stapling、Gatekeeper 干净机器验收或正式发行验收。
- 文档不得要求关闭 Gatekeeper，也不得建议 `xattr -dr com.apple.quarantine`、`spctl --master-disable` 或其他绕过系统安全检查的命令。遇到来源无法确认时，用户应停止安装并重新核对 Release 资产。

### 6. 发布顺序与回滚

发布按以下顺序执行：

1. 在干净环境构建完整 App。
2. Developer ID 签名、notarization、stapling，并验证 Gatekeeper。
3. 生成 DMG、更新 ZIP、SHA-256、SBOM 和发布说明。
4. 上传 R2 暂存路径并从公开下载路径重新验证产物。
5. 发布 GitHub Release 镜像。
6. 最后发布签名 appcast。
7. 更新 Homebrew Cask。

若发现坏版本，立即停止 appcast 继续分发，并发布更高 build number 的修复版。已安装用户不会通过普通 Sparkle 更新自动降级，因此“回滚”指停止扩散和向前修复，不指覆盖旧对象或强制降级。

### 7. 依赖与许可证基线

当前构建产物已确认包含或使用：

- Python 3.14 运行时和标准库。
- PyInstaller 6.21.0 及其构建依赖。
- OpenSSL 3、liblzma、libzstd 和 libmpdec 动态库。
- LobeHub Provider Logos；现有 MIT 声明已收录。
- Sparkle 2.9.4；完整上游许可证和外部声明已随 App 打包。

发布前必须从最终 bundle 重新生成依赖清单，逐项核对实际版本、许可证文本、归属和再分发要求；上述列表只是审计起点，不能代替最终 SBOM。

## 备选方案

### GitHub Releases 作为唯一更新源

可减少 R2 运维，但自定义域名、缓存控制、发布撤回和未来渠道隔离较弱。保留为公开镜像，不作为 App 的唯一更新权威。

### 自建版本比较和下载器

会重复实现签名校验、替换 App、权限、重启和失败恢复，供应链风险高于采用成熟的 Sparkle，因此不采用。

### 只提供 DMG，不在首版接入自动更新

会把首批用户留在手工重装路径，后续无法无感迁移到正式更新源，因此不采用。

### `curl | sh` 一行安装

可复制性强，但执行远端脚本的供应链和权限边界差于 Homebrew Cask，不采用。命令安装统一使用 Homebrew Cask。

## 影响

- 首次正式发布会增加 Xcode 工程、Sparkle 依赖、签名和二进制许可证工作，短期交付量上升。
- App 源码和更新验证逻辑保持公开；Cloudflare、证书和带凭证的发布操作仍可私有维护。
- Windows 更新机制不复用 Sparkle；当前路径只为 macOS 命名空间留出扩展空间，不提前实现 Windows 发布链路。

## 完成标准

只有以下条件全部满足，`v0.1.0` 才能从候选改为正式发布：

- 干净机器可复现构建并通过完整测试。
- Developer ID、公证、stapling、Gatekeeper 和 Sparkle EdDSA 验证通过。
- DMG、ZIP、SHA-256、SBOM、Notice 和发布说明齐全。
- DMG、Homebrew 和旧版到新版 Sparkle 更新完成真实验收。
- GitHub Release、R2 appcast 和 Homebrew Cask 指向同一版本事实。
