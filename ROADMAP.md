# ModelDial Roadmap

最后更新：2026-08-08

## 当前阶段

首个公开源码候选的最后加固。公开仓库已经能够独立测试和构建 macOS App；源码公开、正式签名二进制和 GitHub Release 是三个分开的发布门槛。

当前公开源码候选已通过全量验证，并以单一无父 `main` 根提交固化；源码树发布门槛已经完成。正式签名二进制和 GitHub Release 仍是独立门槛，tag、Release 或生产部署需要单独确认。

不付费 `v0.1.0-preview.1` 已作为 GitHub prerelease 公开，公开文档、供应链、fresh build、DMG／ZIP／SHA-256／SBOM 和公开 URL 回下载复测均已完成；Gatekeeper 开启机器上的“仍要打开”人工放行仍未完成。

针对 `preview.1` 遗漏官方 Radar 地址、无 Provider 时被模型设置空状态阻断、开发 seed 可能进入官网展示以及未发布 appcast 仍显示为已配置的问题，`v0.1.0-preview.2` 源码候选已完成修复和 Build 101 验证。它尚未形成干净提交、DMG／ZIP 或 GitHub Release，不能把本地 candidate 写成已发布版本。

## 已完成

- App、本地 scanner、Native Bridge、题包、测试和构建工具已归并到本仓库；官网、Cloudflare、远端评测、快照发布和运营后台不在公开源码范围内。
- 公开题包只保留当前 `coding-fast-v4.10` 的 `catalog.json` 与 Q1～Q5 prompt／answer；owner 已确认这些题包、答案 fixture、ModelDial 图标、wordmark 和截图均为自有内容。
- `tests/` 作为可复现行为、安全边界和构建合同保留；历史原型、旧题包、无调用实现、构建产物和本地运行数据已从公开文件树移除。
- 候选代码执行使用 macOS Seatbelt fail closed、最小子进程环境和受监督 worker；模型控制的 JSON 已限制原始字节、深度、节点、集合、字符串和数值范围。
- endpoint 普通响应、模型目录、SSE 和隔离 worker 均有总字节预算；Codex、Claude Code、Grok Build、Codex app-server 及候选 grader 子进程共享明确的 stdout／stderr 总预算，超限会终止或拒绝本轮结果。
- 自定义 endpoint 继续兼容 HTTP 与 HTTPS。默认网络客户端在跨源重定向时移除 `Authorization` 和 `x-api-key`，同源重定向保留认证头。
- LiteLLM pricing 来源已固定完整 upstream commit 和原始文件 SHA-256；网络与离线刷新均先校验原始字节，再解析 JSON，来源身份进入 snapshot hash。
- `build.sh` 使用内容锁定的 python.org Python 3.14.3 installer，在项目 `build/` 内校验、解包并冻结 runtime；构建会检查 CA store、TLS／SHA-256／zstd、Mach-O 最低系统版本和非系统绝对动态库依赖。
- `build-dev.sh` 优先复用 `build/modeldial-candidate.app` 的冻结后端；仅在 candidate 不存在时兼容使用 `build/modeldial.app`，因此 fresh clone 完成一次正式构建后即可继续 Swift／资源迭代。
- 双语 README 已重排为产品优先的公开首页：首屏展示定位、App 图标、官网／GitHub／DMG 入口、平台与本地优先标签和 Radar；随后说明使用价值、配置对比、扫描策略、下载、工作方式、核心能力与隐私。`v0.1.0-preview.1` 的 unsigned／unnotarized 限制收敛到下载区单一警告，并继续明确“隐私与安全性 → 仍要打开”、macOS 13+ Apple Silicon、Intel 不支持及禁止 `xattr`／`spctl` 绕过；构建供应链细节下沉到发布文档，README 只保留公开运行时身份与最小命令。
- Radar 成为无需本地 Provider 的首要使用路径：首次打开始终保留官方榜单、刷新和空状态，本地模型接入只作为次要 CTA；中英文 README 同步说明可直接查看官网 Radar、本地评测完全可选，并明确已发布 `preview.1` 尚不包含该修复。
- Swift 官方参考快照增加与 Python 一致的三条件信任门禁：snapshot kind、provenance kind 均为 `first_party_snapshot` 且 `public_official_snapshot=true`；Radar、对比、证据、compact 和通知链路均 fail closed，开发 seed 不再可能被标成官网榜单。
- `preview.2` 候选固定为 Build 101；打包门禁注入并回读官方快照 URL、禁用未发布的 Sparkle 通道、拒绝复用 `preview.1`，并要求包含未跟踪文件在内的工作树干净（忽略 `.gitignore` 内容）、HEAD 稳定及 App／ZIP 内 `ModelDialSourceCommit` 精确一致。

## 源码公开门槛

- [x] 公共／私有代码边界和唯一源码所有权已建立。
- [x] 题包与自有视觉资产已获得 owner 书面确认。
- [x] LiteLLM、LobeHub、Sparkle、Python 和 certifi 的已知来源及许可证说明已进入公开文档或 bundle notice。
- [x] endpoint 凭据重定向边界、网络响应预算和外部子进程输出预算已有回归覆盖。
- [x] `build-dev.sh` 与 candidate-only 构建流程一致。
- [x] 对最终未提交 diff 运行全量 Python 回归、完整 `./build.sh`、bundle 兼容性、签名和内容检查。
- [x] 将验证后的公开文件树形成单一无父的本地 `main` 提交，不继承公开前文档和操作历史。
- [x] 在私有消费者侧单独更新公共核心内容锁并完成相关回归。

## 首个二进制 Release 门槛

以下项目均指完成 Developer ID／Apple notarization 的正式 `v0.1.0`，不因 unsigned preview 完成而自动满足。

- [x] 为 PyPI 构建依赖补 artifact hashes、完整许可证清单和 SBOM。
- [ ] 按实际展示逐项复核 provider 名称、图标和商标政策。
- [ ] 为远端参考快照增加独立发布者签名，并在客户端缓存写入前验签。
- [ ] 完成 Developer ID Application 签名、secure timestamp、notarization 和 stapling。
- [ ] 生成版本化 DMG、Sparkle ZIP、appcast、SHA-256 和 SBOM，并保持源码仓不跟踪二进制产物。
- [ ] 在干净的 macOS 13／14 Apple Silicon 机器上完成构建、Gatekeeper 首次安装、升级、Keychain、CLI 探测、hook、配置和历史恢复验收。
- [ ] 经单独授权创建 GitHub Release 并上传 DMG 等发行附件。

## 阶段性 `unsigned preview` 门槛

- [x] 固定 `v0.1.0-preview.1` 标签和 `modeldial-0.1.0-preview.1-*` 资产命名，并在 `docs/releases/v0.1.0-preview.1.md` 记录平台范围、资产、SHA-256 和已知限制。
- [x] README、发布清单和 ADR 均区分 preview 与正式签名 Release；预览文案不宣称 Developer ID、公证、stapling、Gatekeeper 验收或普通用户直接双击成功。
- [x] 生成并验证 `modeldial-0.1.0-preview.1-macos-arm64.dmg`、`modeldial-0.1.0-preview.1-build-100-macos-arm64.zip`、`SHA256SUMS` 和 `modeldial-0.1.0-preview.1-sbom.spdx.json`。
- [ ] 在 macOS 13+ Apple Silicon 上完成 DMG 挂载、拖入 `Applications` 和“隐私与安全性 → 仍要打开”人工放行；该结果不替代正式 Gatekeeper 干净机验收。
- [x] 经单独授权创建公开 `v0.1.0-preview.1` GitHub prerelease，上传 DMG／ZIP／SHA256SUMS／SPDX，并从公开资产 URL 无认证回下载复测。

## 兼容性边界

- 当前可发行目标是 macOS 13+ Apple Silicon；Windows 客户端仍是后续独立里程碑，不能把 macOS 验证写成跨平台验收。
- 自定义 endpoint 的 HTTP 支持为明确兼容行为；公开客户端不会替用户强制升级传输协议，但不会把认证头带到跨源重定向目标。
- 源码构建默认不配置远端参考快照，App 的本地扫描与历史查看不依赖官网或私有服务。
- `build-dev.sh` 只适用于 Swift／资源迭代；修改 `scanner/`、`scripts/` 或 `questions/` 后必须重新运行 `./build.sh`。

## 下一步

1. 审查本次未提交 diff；经单独授权形成干净提交后，运行 `build-support/package-unsigned-preview.sh` 生成 `preview.2` DMG／ZIP／SBOM／SHA256SUMS。
2. 对生成的 DMG 做本机只读挂载、拖入 `Applications` 和真实 UI 验收；再在另一台 Gatekeeper 开启的 macOS 13+ Apple Silicon 机器上完成“仍要打开”人工验收。
3. 经单独授权创建 `v0.1.0-preview.2` tag／GitHub prerelease 并上传资产，随后模拟公开下载和完整复验。
4. 继续处理正式 `v0.1.0` 的 Developer ID、notarization、独立快照签名和干净机器门槛；Intel 仍按真实需求决定是否建立 universal2 里程碑。

## 最近验证

- 2026-08-08：`preview.2` 针对性修复完成；合并定向回归 `187/187`、全量 Python `1420/1420`（`619.586s`，`ResourceWarning` 按错误处理）、架构基线 `11/11`、shell／plist／本地化 JSON／diff 检查通过。使用官方参考快照地址并禁用更新通道的完整 `./build.sh` 生成 `0.1.0（Build 101）` arm64 candidate；60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名和 bundle 回读通过。冻结后端在隔离数据目录真实刷新官方端点成功，delivery 为 `http/refreshed`，得到 15 条 provenance 合格的第一方结果。未形成干净提交、未运行 release-only packaging、未创建 tag／Release 或上传资产。
- 2026-08-08：中英文 README 完成产品首页重排；App 图标、Hero／双图截图、公开 Release／DMG、官网、语言切换和全部本地 Markdown／HTML 链接检查通过，双语版本／平台／签名限制／构建运行时事实一致。`git diff --check` 与 README 构建合同回归 `17/17` 通过；未改 App、发布资产或远端 Release。
- 2026-08-08：提交 `fcde71f` 已推送到公开 `main`，tag `v0.1.0-preview.1` 指向该提交并创建公开 GitHub prerelease。4 个资产大小与本地产物一致；随后不带 GitHub API 认证从公开 asset URL 重新下载，`SHA256SUMS` 三项均通过，DMG 只读挂载与 ZIP 解包通过，App 为 `0.1.0 (100)`／thin arm64／ad-hoc 且无证书 Authority，bundle SPDX 校验和冻结后端 OpenSSL 3.0.18／mpdecimal 4.0.0／zstd smoke 通过。本机 Gatekeeper disabled，未把此次复测写成“仍要打开”人工验收。
- 2026-08-08：unsigned preview 定向测试 `26/26`、全量 Python `1415/1415`（`619.687s`，`ResourceWarning` 按错误处理）通过。`package-unsigned-preview.sh` 强制 ad-hoc fresh build，PyPI requirements receipt 触发 `--require-hashes` 重建；60 个 Mach-O／65 个架构记录通过 macOS 13 门禁，整包深层签名有效。生成 DMG `17,735,966` bytes、ZIP `15,421,467` bytes、SPDX SBOM `200,931` bytes 和 `SHA256SUMS`；三项 hash、DMG／ZIP 容器、SPDX 2.3 官方 JSON Schema、只读挂载后 bundle SBOM／签名／arm64／冻结后端和 11 个 Legal 文件均通过。未创建 tag、GitHub Release 或上传二进制；当前机器 Gatekeeper disabled，未把本机挂载 smoke 写成人工放行验收。
- 2026-08-08：unsigned preview 的中英文 README、发布清单、ADR 和 `docs/releases/v0.1.0-preview.1.md` 已完成一致性检查；文件存在性、资产命名／警告／手动放行文案和 `git diff --check` 通过，未创建 tag、GitHub Release 或上传二进制。
- 2026-08-08：私有公共核心锁已同步到公开根提交及内容哈希；双仓边界、镜像内公共核心校验、私有边界／Cloud Runner／发布器回归 `50/50`、Cloudflare 普通 Vitest `37/37`、Workers Runtime `1/1`、TypeScript／生成类型、Linux／AMD64 镜像 `/health` 和 `wrangler deploy --dry-run` 全部通过，未部署 Cloudflare 或创建二进制 Release。
- 2026-08-08：endpoint、HTTP 兼容、跨源认证头、隔离 worker、外部 CLI、Codex app-server、候选 grader、构建合同定向回归 `177/177` 通过；随后全量 Python `1404/1404`（`636.856s`）通过。完整 `./build.sh` 完成 Python 3.14.3 冻结、snapshot smoke、Xcode Release、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、Sparkle 嵌套签名、整包严格签名和 Designated Requirement，生成 `build/modeldial-candidate.app` 且未替换正式 App。`./build-dev.sh` 实际从 candidate 复用同一冻结后端并通过严格签名；candidate 题包与源码 11 个文件一致，bundle 不含测试或私有服务目录。`ResourceWarning` 按错误处理，`compileall`、Shell 语法和 `git diff --check` 通过。
- 2026-08-07：锁定 Python runtime 与 macOS 13 bundle 兼容门禁定向回归 `18/18` 通过；candidate 的真实 Mach-O、冻结 runtime smoke、CA store、深层签名和 Designated Requirement 通过。
- 2026-08-07：LiteLLM pricing provenance 定向回归 `16/16` 通过；刷新后 34 个条目的价格数值无变化，policy 与 snapshot 来源身份一致。
