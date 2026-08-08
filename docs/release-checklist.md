# GitHub open-source release checklist

本清单用于把当前“开源抽取候选”推进到公开源码仓库和首个可安装版本。完成项必须有可复查证据；源码公开和二进制发布分别过门。

## Gate 1：公开源码仓库前

- [x] 只保留 App、scanner、题包、脚本、测试和必要开发工具。
- [x] 排除官网、Cloudflare、容器配置、远端评测运行器、快照发布与管理后台。
- [x] 清除个人绝对路径、密钥文件和生产配置。
- [x] 补齐 Apache-2.0、NOTICE、商标、隐私、安全和贡献说明。
- [x] 运行 Python 全量回归和完整 App 构建；2026-08-08 当前未提交候选为 `1415/1415`，完整 unsigned preview fresh build、macOS 13 bundle 兼容门禁和严格 ad-hoc 签名通过。
- [x] 复查跟踪文件和当前构建产物的公开边界。
- [x] 将内置 development seed 改为可重复生成的纯合成数据，不保留本机 score／耗时／Token／费用、run ID 或 endpoint ID；开发 seed 不进入官方建议证据。
- [x] App 后端脚本采用运行时 allowlist，不再把 Swift 开发验证脚本随 App 分发；公开测试仍只存在于源码仓。
- [x] 本地公开历史已在明确授权下统一为确认后的 GitHub noreply 作者身份；原始历史已另行离线备份，未推送远端。
- [x] 提供中英文 README 和构建／测试入口说明。
- [x] 自定义 endpoint 保持 HTTP／HTTPS 兼容；普通响应、模型目录、SSE 和隔离 worker 均有总字节预算，默认客户端在跨源重定向时移除 `Authorization`／`x-api-key`，同源重定向保留认证头。
- [x] Codex、Claude Code、Grok Build、Codex app-server 和候选 grader 子进程使用共享 stdout／stderr 总预算；超限会终止或拒绝本轮结果，timeout 与既有测试 runner 语义保持兼容。
- [x] `build-dev.sh` 优先复用 candidate 冻结后端，并在 candidate 不存在时兼容使用正式 App；fresh clone 完成一次 `build.sh` 后不要求预先存在正式 App。
- [x] 删除根目录独立单题评测原型及其专用测试；正式执行能力只保留在 App scanner 链路，并由双仓边界检查阻止该原型重新进入公开仓。
- [x] 补充脱敏的中英文产品截图：Radar、对比页、通用设置和扫描设置位于 [docs/screenshots](screenshots/)；截图中的指标是示例数据，不代表实时榜单。
- [ ] 补充功能动图（可选，不阻塞首个源码发布）。
- [x] 在 [benchmark-and-data-policy.md](benchmark-and-data-policy.md) 明确题包、答案 fixture、价格快照的版权／再分发和 benchmark 可见性口径；具体外部来源仍需逐项复核。
- [x] 2026-08-06 owner 书面确认 Q1–Q5 题包及答案 fixture 均为用户原创，ModelDial 图标、wordmark 和截图均为项目自有视觉资产；见 [open-source-content-audit.md](open-source-content-audit.md)。
- [x] 核对 LiteLLM pricing 与 LobeHub icon 包的上游许可证和版本／来源边界；NOTICE 已包含 LiteLLM／Berri AI 和 LobeHub 的完整 MIT attribution，并保留 provider 无关联／不背书说明。LiteLLM policy 与当前 snapshot 已固定完整 upstream commit 和 raw SHA-256，网络与离线刷新均在 JSON 解析前 fail closed 校验原始字节。
- [ ] 正式二进制发行前按实际展示逐项复核 provider 商标政策；LobeHub MIT 许可与 owner 原创声明均不替代该项。
- [x] 保存每题核心句的精确公开检索 query、日期和结果，见 [open-source-content-audit.md](open-source-content-audit.md)；检索仅作辅助证据，未发现公开命中不得作为原创证明，也不能替代 owner attestation。
- [x] 创建公开 GitHub 仓库、添加 `origin`、推送 `main`，并核对本地／远端 SHA；公开根提交为 `f419324b675f416f0caceff0e555642f2e20f8f3`。
- [x] 配置仓库描述、Topics 和默认分支保护；`main` 禁止 force push／删除并要求线性历史。
- [x] 添加 Issue／PR 模板；远端启用后仍需核对标签和权限。
- [x] 启用 GitHub Private Vulnerability Reporting、secret scanning 和 push protection。
- [ ] 增加 CI 前单独评审权限、缓存、日志脱敏和 fork PR 的密钥边界。

## Gate 2：首个二进制 Release 基础

- [x] 建立最小 Xcode Project／SwiftPM，由 Xcode 负责 App、Sparkle 框架和嵌套辅助组件的装配与签名。
- [x] 保留 `build.sh` 作为本地统一入口；Python 3.14.3 installer 由独立 lock 固定 URL、SHA-256 和 signer，只解包到 `build/`；PyInstaller、certifi CA bundle 及传递构建依赖同时固定版本和 wheel SHA-256，使用 `pip --require-hashes`，requirements receipt 变化时重建隔离的 `build/pyinstaller-env`。
- [x] 统一声明与实际二进制最低系统版本。当前 Xcode／Info.plist 目标为 macOS 13，candidate 的 60 个真实 Mach-O／65 个架构记录全部不高于 13.0，且不存在非系统绝对动态库依赖；构建在签名前 fail closed 执行该检查，并真实加载 TLS／SHA-256／zstd 与 bundle 内 CA store。macOS 13／14 干净机器验收仍保留在 Gate 4。
- [x] 为正式构建依赖补充 artifact hashes／来源证明；Python installer 固定 SHA-256 和 signer，PyPI requirements 固定实际 wheel SHA-256，并已在 fresh build 中通过 `--require-hashes` 重建验证。
- [x] 使用独立 marketing version 和单调递增 build number；首个候选固定为 `0.1.0（Build 100）`。
- [x] 将官方品牌候选中的个人 Workers 域名迁移为 ModelDial 产品域名，并完成候选 bundle 回读及远端刷新验证；正式发行包仍需在 Gate 3 最终扫描中复核。
- [x] 在首个公开二进制中接入 Sparkle、设置页“检查更新”、自动检查和默认关闭的自动下载。
- [ ] 使用 HTTPS、Sparkle EdDSA 更新包签名，并验证签名 appcast 和私钥恢复边界。
  - 开发态隔离演练已完成 Build 99 → 100 的 R2 下载、EdDSA 校验、安装和重启；长期私钥备份与正式发行包仍未完成。
- [ ] 使用 Developer ID Application、Hardened Runtime 和 secure timestamp 完成签名。
- [ ] 通过 Apple notarization，并对 App／DMG 完成 stapling 和票据验证。
- [x] 更新隐私说明，披露更新检查／下载的 HTTPS 访问和常规 CDN 日志边界；`SUSendProfileInfo=false`，系统画像上传保持关闭。
- [x] 补齐 Python、PyInstaller、OpenSSL、xz、zstd、mpdecimal、Sparkle 等最终二进制依赖的许可证、Notice 和 SPDX 2.3 SBOM；生成后同时通过仓库 validator 和 SPDX 2.3 官方 JSON Schema。
- [ ] 在干净 macOS Apple Silicon 机器上复现构建。
- [x] 将所有候选代码评测统一放入 OS 级 deny-by-default sandbox；AST／导入检查只能作为第二层防线，并补齐 `object.__subclasses__`、`__globals__`、文件和网络访问回归。当前 macOS Seatbelt 已实测，其他平台 fail closed 且 Windows 实机仍属独立里程碑。
- [x] 将跨进程暂停／停止请求改为原子 mailbox／带序号确认，并完成并发写入、读取和清理竞态测试；当前已在 macOS 完成真实多进程回归，Windows 留在独立客户端里程碑验证。
- [x] 在 advisor projection 前拒绝 `development_seed` 和缺少明确 first-party provenance 的参考快照；对 `elapsed_ms` 增加 Swift 安全数值上限，并在 Radar／对比／导出转换前再次 checked conversion。
- [x] 公开客户端对远端参考快照强制 HTTPS；仅保留 `localhost`／loopback HTTP 作为本地开发与测试入口，非 loopback HTTP 在刷新前 fail closed。该规则只适用于第一方参考快照，不改变自定义模型 endpoint 的 HTTP 兼容策略。
- [ ] 为远端参考快照增加独立签名并在缓存写入前验签；验签失败只回退到最近的已验证缓存或内置快照。
- [x] 为本地 CLI／模型子进程建立环境白名单，默认不继承 API Key、Token、SSH／Git 凭据等无关环境变量；仅调用方显式注入必要的 ModelDial 配置和 Codex cloud key。

## Gate 2A：不付费 `unsigned preview`（可先行渠道）

该门槛允许在没有 Apple Developer Program 会员资格时先发布可手动放行的预览版；它不是 Gate 2 的正式签名替代品，也不能把预览版标记为正式 `v0.1.0`。

- [x] 预览版本固定使用 `v0.1.0-preview.1`；资产命名固定为 `modeldial-0.1.0-preview.1-macos-arm64.dmg`、`modeldial-0.1.0-preview.1-build-100-macos-arm64.zip`、`SHA256SUMS` 和 `modeldial-0.1.0-preview.1-sbom.spdx.json`。正式版文件名不得复用。
- [x] README 和预览 Release 正文明确写出 **unsigned / unnotarized**、无 Developer ID、无 Apple notarization、无 Intel 支持，并说明这不是普通用户直接双击即可打开的正式发行包。
- [x] 用 `./build.sh` 生成并记录 `build/modeldial-candidate.app`，确认候选包的版本／build（`0.1.0`／`100`）和 macOS 13+ Apple Silicon 目标；不以 `build/modeldial.app` 的旧状态冒充本次预览。
- [x] 由候选包生成 DMG／ZIP 和 SPDX SBOM；在同一目录生成 `SHA256SUMS`，逐项核对文件名、大小和 SHA-256。
- [x] 记录 `codesign --verify`、`codesign -dv --verbose=4` 结果；App 为 ad-hoc 签名，无 `Authority`，未宣称 Developer ID、secure timestamp、Apple notarization 或 stapling。
- [ ] 在至少一台可用的 macOS 13+ Apple Silicon 机器上完成 DMG 挂载、拖入 `Applications` 和首次启动手动放行；这只证明预览安装路径，不等于 Gatekeeper 干净机验收、Apple 公证或正式 Release 验收。
- [x] Release 正文引导用户使用“系统设置 → 隐私与安全性 → 仍要打开（Open Anyway）”；不要求关闭 Gatekeeper，不建议 `xattr -dr com.apple.quarantine`、`spctl --master-disable` 或同类绕过命令。
- [x] 预览版不进入正式 Sparkle `appcast.xml`，不创建 Homebrew Cask；更新、回滚和自动下载仍以正式签名门槛为准。
- [x] 已创建公开 GitHub prerelease `v0.1.0-preview.1` 并上传 4 个资产；随后不带 GitHub API 认证从公开资产 URL 重新下载，SHA-256、DMG／ZIP、ad-hoc 签名、版本／arm64、SPDX 和冻结后端 smoke 全部通过。

## Gate 3：发行产物与渠道

- [ ] 生成版本化 DMG、整包更新 ZIP、`SHA256SUMS`、SBOM 和发布说明。
- [ ] 对最终 App bundle 再做私有模块、密钥、个人路径和第三方许可证扫描。
- [ ] 将不可变版本包上传到 R2 暂存路径，验证长度、SHA-256、签名、公证和下载。
- [ ] 创建 GitHub Release 镜像，并保证 tag、源码和二进制版本一致。
- [ ] 最后发布短缓存或不缓存的签名 `appcast.xml`；不覆盖已经发布的版本化安装包。
- [ ] 创建公开 Homebrew Tap；Cask 使用同一正式 DMG、精确版本和 SHA-256，并声明 App 自带更新能力。

## Gate 4：最终验收

- [ ] 在 Gatekeeper 开启、无开发环境的 macOS 13／14 Apple Silicon 机器上完成 DMG 首次安装和真实 UI 验收。
- [ ] 完成 Homebrew Cask 安装、卸载和重新安装验收。
- [ ] 完成旧版本到新版本的 Sparkle 更新、错误签名拒绝、离线／404 和缓存刷新验收。
- [ ] 验证 Keychain、CLI 探测、hook 安装／卸载、数据清理及升级后的配置／历史保留。
- [ ] 验证活跃扫描期间更新不会损坏运行日志、历史或恢复状态。
- [ ] 发布说明准确标出平台范围、已知限制、数据边界和升级／回滚方式。

## 发布顺序

1. 构建、签名、公证并生成不可变产物。
2. 上传 R2 暂存路径并完成下载验收。
3. 发布 GitHub Release 镜像。
4. 发布签名 appcast，使 App 看见新版本。
5. 更新 Homebrew Cask。

若已发布版本有缺陷，先停止继续分发，再发布更高 build number 的修复版；不把降级安装冒充回滚。

## 明确不宣称

- 未验证 Windows 前，不宣称跨平台客户端已可用。
- 本地测试和 ad-hoc 签名通过，不等于正式发行、公证或 Gatekeeper 验收通过。
- App 能读取版本化参考快照，不等于私有服务、官网或生产发布链路已开源。
