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
- [x] 补充中英文功能动图；两条 README 素材均为 `840×406`、10 fps、8.6 秒，并覆盖 compact → Radar → Compare → Radar → compact。
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
- [x] 将官方品牌候选中的个人 Workers 域名迁移为 ModelDial 产品域名；`preview.2` 打包门禁固定注入并回读 `https://reference.modeldial.com/reference-snapshots`。最终发行包及远端刷新仍需在 Gate 3 复核。
- [x] 接入 Sparkle、设置页“检查更新”、自动检查和默认关闭的自动下载；普通源码构建和 unsigned preview 默认不配置更新通道，只有同时显式提供 HTTPS appcast 与 EdDSA 公钥时才启用；update-enabled preview 只能使用独立的官方 preview feed。
- [ ] 使用 HTTPS、Sparkle EdDSA 更新包签名，并验证签名 appcast 和私钥恢复边界。
  - preview 通道已完成长期身份导出到仓库外 `0600` 文件、文件签名／验签、签名 appcast、R2 下载和 Build 106 → 107 → 108 → 109 → 110 安装重启；独立离线备份、恢复演练和正式 stable 发行包仍未完成。
- [ ] 使用 Developer ID Application、Hardened Runtime 和 secure timestamp 完成签名。
- [ ] 通过 Apple notarization，并对 App／DMG 完成 stapling 和票据验证。
- [x] 更新隐私说明，披露更新检查／下载的 HTTPS 访问和常规 CDN 日志边界；`SUSendProfileInfo=false`，系统画像上传保持关闭。
- [x] 补齐 Python、PyInstaller、OpenSSL、xz、zstd、mpdecimal、Sparkle 等最终二进制依赖的许可证、Notice 和 SPDX 2.3 SBOM；生成后同时通过仓库 validator 和 SPDX 2.3 官方 JSON Schema。
- [ ] 在干净 macOS Apple Silicon 机器上复现构建。
- [x] 将所有候选代码评测统一放入 OS 级 deny-by-default sandbox；AST／导入检查只能作为第二层防线，并补齐 `object.__subclasses__`、`__globals__`、文件和网络访问回归。当前 macOS Seatbelt 已实测，其他平台 fail closed 且 Windows 实机仍属独立里程碑。
- [x] 将跨进程暂停／停止请求改为原子 mailbox／带序号确认，并完成并发写入、读取和清理竞态测试；当前已在 macOS 完成真实多进程回归，Windows 留在独立客户端里程碑验证。
- [x] 在 advisor projection 前拒绝 `development_seed` 和缺少明确 first-party provenance 的参考快照；Swift 端只有 snapshot kind、provenance kind 均为 `first_party_snapshot` 且 `public_official_snapshot=true` 时才允许进入 Radar／对比／证据，其他情况 fail closed。对 `elapsed_ms` 继续执行安全数值上限和 checked conversion。
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
- [x] Release 正文引导 DMG 用户使用“系统设置 → 隐私与安全性 → 仍要打开（Open Anyway）”；不要求关闭 Gatekeeper，不让用户自行运行 `xattr -dr com.apple.quarantine`、`spctl --master-disable` 或同类系统级绕过命令。
- [x] 预览版不进入正式 stable `appcast.xml` 或正式 Homebrew Cask；已发布的 `preview.1` 虽残留尚未发布的 stable appcast 地址，但没有可用自动升级路径，`preview.2`～`preview.4` 的 feed 与公钥均为空。`preview.5`／`preview.6` 的更新器配置不完整，`preview.7` 起使用独立 preview appcast；正式 stable 通道保持未发布。
- [x] 已创建公开 GitHub prerelease `v0.1.0-preview.1` 并上传 4 个资产；随后不带 GitHub API 认证从公开资产 URL 重新下载，SHA-256、DMG／ZIP、ad-hoc 签名、版本／arm64、SPDX 和冻结后端 smoke 全部通过。

## Gate 2B：`v0.1.0-preview.2` 修复候选

- [x] marketing version 保持 `0.1.0`，build number 从 100 单调递增到 101；预览打包默认 label 改为 `preview.2` 并拒绝复用已公开的 `preview.1`。
- [x] 打包脚本要求包含未跟踪文件在内的工作树在构建前后保持干净（忽略 `.gitignore` 内容）、HEAD 不变化，并将精确 Git commit 写入 App；candidate 与 ZIP 都必须回读为同一 commit。
- [x] 打包脚本固定注入第一方参考快照地址，candidate 与 ZIP 必须回读精确 URL；unsigned preview 的 `SUFeedURL` 与 `SUPublicEDKey` 必须为空。
- [x] 完成本次修复的合并定向回归 `187/187`、全量 Python `1420/1420`、架构基线 `11/11` 和 Build 101 正式构建；candidate 回读为官方 Radar URL、空更新通道、arm64 和有效深层 ad-hoc 签名，冻结后端真实远端刷新得到 15 条可信第一方结果。
- [x] 已在干净提交 `a20d14e` 上生成 `preview.2` DMG／ZIP／SBOM／SHA256SUMS；三项哈希、DMG 只读挂载、ZIP 解包、bundle SBOM、官方 Radar URL、空更新通道、精确源码提交、深层 ad-hoc 签名和冻结后端真实刷新均通过下载前本机复验。
- [ ] 在 Gatekeeper 开启的 macOS 13+ Apple Silicon 机器上完成首次安装和真实 UI 验收。
- [x] 经单独授权创建 `v0.1.0-preview.2` annotated tag／GitHub prerelease 并上传 4 个精确资产；tag 的 peeled commit 与二进制 `ModelDialSourceCommit` 均为 `a20d14e`，`preview.1` 未被修改或覆盖。随后从公开 URL 无认证下载，完成 SHA-256、DMG／ZIP、签名、macOS 13、SPDX 和冻结后端官方快照复验。

## Gate 2C：`v0.1.0-preview.3` 交互修复候选

- [x] marketing version 保持 `0.1.0`，build number 从 101 单调递增到 102；预览打包默认 label 改为 `preview.3`，并拒绝复用已公开的 `preview.1` 与 `preview.2`。
- [x] 完成构建合同定向回归 `28/28`、全量 Python `1421/1421`、完整 Build 102 构建和真实 UI 交互验收；官方 Radar 自动刷新在隔离副本中从零取得 15 条结果。
- [x] 在干净提交 `16e0dd2cafa82ef1b77b719edc0a3db90e5bf68f` 上生成独立目录中的 `preview.3` DMG／ZIP／SBOM／SHA256SUMS；没有覆盖 `preview.1`／`preview.2` 本地产物或清单。
- [x] 三项 SHA-256、DMG 只读挂载、ZIP 解包、版本／build／源码提交、官方 Radar URL、空更新通道、深层 ad-hoc 签名、thin arm64、macOS 13 bundle 兼容性和两份 bundle SBOM 均通过独立复验。
- [x] ZIP 内冻结后端在隔离 HOME 和全新数据目录真实刷新官方端点，delivery 为 `http/refreshed`，得到 15 条 provenance 合格的第一方结果。
- [ ] 在 Gatekeeper 开启的 macOS 13+ Apple Silicon 机器上完成首次安装和真实 UI 验收；当前机器 assessments disabled，不能据此核销。
- [x] 经单独授权创建 `v0.1.0-preview.3` annotated tag／GitHub prerelease 并上传 4 个精确资产；tag 的 peeled commit 与二进制 `ModelDialSourceCommit` 均为 `16e0dd2`，Release 非 draft 且仅包含四项预期资产。随后从无认证公开 URL 下载并完成 SHA-256、DMG／ZIP、签名、macOS 13、SPDX 和冻结后端官方快照复验；双语 README 已切换到 `preview.3`。

## Gate 2D：`v0.1.0-preview.4` Radar 韧性修复候选

- [x] marketing version 保持 `0.1.0`，build number 从 102 单调递增到 103；预览打包默认 label 改为 `preview.4`，并拒绝复用已公开的 `preview.1`、`preview.2` 与 `preview.3`。
- [x] 官方 Radar 的索引和归档单请求默认超时从 3 秒提高到 8 秒；失败后的 App 首次自动重试提前到 30 秒，连续失败再按 5 分钟、15 分钟、1 小时和 6 小时退避。调度版本同步迁移，缓存／内置快照回退和 first-party provenance 门禁保持不变。
- [x] 完成超时／刷新调度、构建合同和预览打包定向回归 `66/66`，以及全量 Python `1422/1422`（`ResourceWarning` 按错误处理）；`git diff --check` 通过。
- [x] 从干净源码提交 `7237db3413a040f9ad912f7c21917aec392323f3` 完成 Build 103 fresh build，并通过版本／build／源码提交、官方 Radar URL、空更新通道、thin arm64、macOS 13 和深层 ad-hoc 签名回读。
- [x] ZIP 内冻结后端用超过旧 3 秒门限的 4.2 秒隔离慢响应完成 `http/refreshed`，并从真实官方端点在全新数据目录取得 15 条 provenance 合格的第一方结果。
- [x] 在独立目录生成并复验 `preview.4` DMG／ZIP／SBOM／SHA256SUMS；三项 SHA-256、DMG 只读挂载、ZIP 解包、两份 bundle 身份／签名／兼容性／SBOM 和主程序一致性均通过，没有覆盖历史预览产物或清单。
- [ ] 在 Gatekeeper 开启的 macOS 13+ Apple Silicon 机器上完成首次安装和真实 UI 验收。
- [x] 经单独授权创建 `v0.1.0-preview.4` annotated tag／GitHub prerelease 并上传 4 个精确资产；tag object 为 `d6b96b9`，peeled commit 与二进制 `ModelDialSourceCommit` 均为 `7237db3`。Release 非 draft 且仅包含四项预期资产；公开 URL 无认证回下载的大小／SHA-256、DMG／ZIP、bundle 身份、签名、macOS 13、SPDX 均通过，公开 ZIP 在首次官方 Radar `unavailable` 后按 30 秒 App 级退避重试成功并取得 15 条可信第一方结果；双语 README 已切换到 `preview.4`。

## Gate 2E：`preview.5`～`preview.11` 软件更新通道

- [x] 使用独立 `https://updates.modeldial.com/macos/preview/appcast.xml`，不覆盖或复用正式 stable feed；版本化资产使用不可变 R2 路径，appcast 使用 `max-age=60, must-revalidate`。
- [x] Ed25519 私钥导出到仓库外、权限为 `0600`，公开包只包含对应公钥；ZIP、生成后的 appcast 和在线回下载 appcast 均通过 Sparkle `sign_update --verify`。
- [x] `preview.5`（Build 104）和 `preview.6`（Build 105）已作为历史 prerelease 与 R2 镜像保留；真实 UI 验收发现两版缺少 `SUVerifyUpdateBeforeExtraction`，因此不能启动更新器，并在 README／发布文档中明确要求手动升级。
- [x] `preview.7`（Build 106）同时启用 `SURequireSignedFeed` 与 `SUVerifyUpdateBeforeExtraction`；设置页回读版本正确，“立即检查”按钮可用。
- [x] `preview.8`（Build 107）在干净提交 `537dfaaf6204557133689a8b45e6729a6cf66bd6` 上完成 fresh build；DMG／ZIP／SBOM／SHA256SUMS、macOS 13 兼容性、thin arm64、深层 ad-hoc 签名和 bundle 身份复验通过。
- [x] `preview.9`（Build 108）在干净提交 `abf28096ccc30fb923213e257eb131a069b79633` 上完成 fresh build；DMG／ZIP／SBOM／SHA256SUMS、macOS 13 兼容性、thin arm64、深层 ad-hoc 签名和 bundle 身份复验通过。
- [x] `preview.10`（Build 109）在干净提交 `d60488b4d2d22ca35f44f552521ccb5354fc5640` 上完成 fresh build；DMG／ZIP／SBOM／SHA256SUMS、macOS 13 兼容性、thin arm64、60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名、bundle 身份和冻结后端官方刷新复验通过。
- [x] `preview.11`（Build 110）在干净提交 `8afa91e205053e10d9a4820277504bb53080ab60` 上完成 fresh build；`1,438/1,438` 全量合同、DMG／ZIP／SBOM／SHA256SUMS、macOS 13 兼容性、thin arm64、60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名、bundle 身份和冻结后端官方刷新复验通过。
- [x] GitHub prerelease 与 R2 都包含 `preview.7`～`preview.11` 的四项精确资产；在线 appcast 精确指向无查询参数的 `15,469,502` bytes Build 110 ZIP，feed 与 enclosure 签名有效。
- [x] 从 `/Applications` 的 Build 106 依次真实升级到 Build 107、108、109 和 110，均完成下载、验签、安装和重启；最新 App 回读精确 build／源码提交／feed／公钥／安全开关，再次检查显示“当前已是最新版本”。Build 109 → 110 前后的本机榜单、完成时间、配置、历史、官方快照和 Secrets 元数据保持；9 个 `session-events/inbox` 临时事件被正常消费，6 个实时观察文件按预期更新。
- [ ] 在 Gatekeeper 开启、无开发环境的另一台 macOS 13+ Apple Silicon 机器上完成 `preview.11` 首次安装人工放行；本机升级验收不替代该项。

## Gate 2F：`preview.11` 个人 Homebrew Tap

- [x] 在独立 `homebrew-tap` 源码根维护 `Casks/modeldial.rb`，当前固定 `preview.11` GitHub DMG、SHA-256、macOS 13+、arm64 和 `auto_updates true`；Tap 不保存第二份 App 二进制。
- [x] Cask 的 quarantine 移除只作用于安装后的 `modeldial.app`，不使用 `sudo`，不修改 Gatekeeper 或系统级安全设置；Cask caveats、Tap README、App README 与官网双语披露保持一致。
- [x] 使用临时 App 目录完成 Cask 解析、下载、SHA-256、安装、quarantine 属性、bundle 版本／build／架构、ad-hoc 签名结构、Sparkle feed／Ed25519 key、卸载和重新安装验证，不覆盖 `/Applications` 中的正式 App；`preview.11` 已在 Homebrew 6.0.15 下通过 `brew style`、`brew audit --cask --strict` 和完整安装验收。
- [x] 经单独授权创建并推送公开 `tianwdong/homebrew-tap`，随后用不依赖本地路径的完整限定命令在临时 App 目录重新安装，完成下载、SHA-256、单 Cask trust、quarantine、版本／架构、卸载和重装验证。
- [x] 远端命令真实可用后，已提交并发布 App README、当前 Release 说明和官网中英文 Homebrew 入口；生产页面与公开 Tap 均已回读验证。

## Gate 3：发行产物与渠道

- [ ] 生成版本化 DMG、整包更新 ZIP、`SHA256SUMS`、SBOM 和发布说明。
- [ ] 对最终 App bundle 再做私有模块、密钥、个人路径和第三方许可证扫描。
- [ ] 将不可变版本包上传到 R2 暂存路径，验证长度、SHA-256、签名、公证和下载。
- [ ] 创建 GitHub Release 镜像，并保证 tag、源码和二进制版本一致。
- [ ] 最后发布短缓存或不缓存的签名 `appcast.xml`；不覆盖已经发布的版本化安装包。
- [ ] 创建正式 stable Homebrew Cask；使用同一已签名／公证 DMG、精确版本和 SHA-256，声明 App 自带更新能力，并移除预览 Tap 的 quarantine 兼容逻辑。

## Gate 4：最终验收

- [ ] 在 Gatekeeper 开启、无开发环境的 macOS 13／14 Apple Silicon 机器上完成 DMG 首次安装和真实 UI 验收。
- [ ] 完成正式 Homebrew Cask 安装、卸载和重新安装验收。
- [ ] 完成旧版本到新版本的 Sparkle 更新、错误签名拒绝、离线／404 和缓存刷新验收。
- [ ] 验证 Keychain、CLI 探测、hook 安装／卸载、数据清理及升级后的配置／历史保留。
- [ ] 验证活跃扫描期间更新不会损坏运行日志、历史或恢复状态。
- [ ] 发布说明准确标出平台范围、已知限制、数据边界和升级／回滚方式。

## 发布顺序

1. 构建、签名、公证并生成不可变产物。
2. 上传 R2 暂存路径并完成下载验收。
3. 发布 GitHub Release 镜像。
4. 更新并验收 Homebrew Cask、官网和其他固定版本入口。
5. 最后发布签名 appcast，使 App 看见新版本，并完成真实升级复查。

若已发布版本有缺陷，先停止继续分发，再发布更高 build number 的修复版；不把降级安装冒充回滚。

## 明确不宣称

- 未验证 Windows 前，不宣称跨平台客户端已可用。
- 本地测试和 ad-hoc 签名通过，不等于正式发行、公证或 Gatekeeper 验收通过。
- App 能读取版本化参考快照，不等于私有服务、官网或生产发布链路已开源。
