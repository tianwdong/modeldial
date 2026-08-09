# ModelDial Roadmap

最后更新：2026-08-09

## 当前阶段

公开源码和不付费 macOS 预览通道已经可用。公开仓库能够独立测试和构建 App；unsigned preview 与完成 Developer ID／Apple notarization 的正式发行仍是两个分开的发布门槛。

当前公开源码候选已通过全量验证，并以单一无父 `main` 根提交固化；源码树发布门槛已经完成。正式签名二进制、stable appcast、正式 stable Homebrew Cask 和干净机器 Gatekeeper 验收仍未完成。

独立的 unsigned preview Sparkle 通道已建立并完成连续真实升级验收。`preview.5`／`preview.6` 因缺少 Sparkle 必需的解压前验签配置而无法启动更新器；`preview.7`（Build 106）修复后，已依次升级到 `preview.8`（Build 107）、`preview.9`（Build 108）和 `preview.10`（Build 109），每次均完成下载、验签、安装、重启和最新版本复查。`preview.6` 及更早版本需要手动安装 `preview.10` 一次。

不付费 `v0.1.0-preview.1`～`v0.1.0-preview.10` 均已作为 GitHub prerelease 公开；当前 README 和安装入口指向 `preview.10`。版本化 DMG／ZIP／SHA-256／SBOM 同步保存在 GitHub 与 R2，preview appcast 单独发布。Gatekeeper 开启的独立机器“仍要打开”人工放行仍未完成。

签名前个人 Homebrew Tap 已发布到 `tianwdong/homebrew-tap`，复用同一 `preview.10` GitHub DMG 和 SHA-256；从公开远端完成临时目录安装、卸载、重装、单 Cask trust、quarantine、版本／架构、签名结构和 Sparkle 配置验证。App README、当前 GitHub Release 正文和 `modeldial.com` 中英文下载入口均已发布，完整限定命令已可公开使用。

针对 `preview.1` 遗漏官方 Radar 地址、无 Provider 时被模型设置空状态阻断、开发 seed 可能进入官网展示以及未发布 appcast 仍显示为已配置的问题，`v0.1.0-preview.2` 已在源码提交 `a20d14e` 上完成修复与 Build 101 打包；tag、GitHub prerelease、4 个资产和公开下载复验均已完成。它仍是 unsigned／unnotarized 预览版，不等于正式 `v0.1.0`。

交互修复版 `v0.1.0-preview.3` 已在源码提交 `16e0dd2cafa82ef1b77b719edc0a3db90e5bf68f` 上完成 Build 102 打包并公开；annotated tag、GitHub prerelease、四项资产和无认证公开下载复验均已完成，双语 README 已切换到该版本。它仍是 unsigned／unnotarized 手动安装预览版，不进入正式 Sparkle 通道。

`v0.1.0-preview.4` 已在源码提交 `7237db3413a040f9ad912f7c21917aec392323f3` 上完成 Build 103 打包并公开，用于修复官方 Radar 短时首字节超过旧 3 秒门限时的刷新脆弱性；annotated tag、GitHub prerelease、四项资产和无认证公开下载复验均已完成，双语 README 已切换到该版本。它仍是 unsigned／unnotarized 手动安装预览版，不进入正式 Sparkle 通道。

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
- 双语 README 已重排为产品优先的公开首页：首屏展示定位、对应语言的 compact／Radar／Compare 动图、官网／GitHub／`preview.10` DMG 入口、平台与本地优先标签；正文按“无需配置浏览官方 Radar → 下载安装 → 产品价值 → 可选本地评测 → 隐私与源码构建”展开。unsigned／unnotarized 限制集中在下载区，并继续明确“隐私与安全性 → 仍要打开”、macOS 13+ Apple Silicon、Intel 不支持及禁止 `xattr`／`spctl` 绕过；同时写明 `preview.7` 起可应用内更新，旧版需手动过渡。
- unsigned preview 个人 Homebrew Tap 已公开：Cask 固定 `preview.10` DMG／SHA-256、macOS 13+、arm64 和 App 自带更新，仅对安装目标中的 `modeldial.app` 移除 quarantine，不使用 `sudo` 或修改 Gatekeeper；双语 README、ADR、发布清单、当前预览说明和官网中英文入口保持一致。
- Radar 成为无需本地 Provider 的首要使用路径：首次打开始终保留官方榜单、刷新和空状态，本地模型接入只作为次要 CTA；中英文 README 同步说明可直接查看官网 Radar、本地评测完全可选，并指向当前 `preview.10`。
- Swift 官方参考快照增加与 Python 一致的三条件信任门禁：snapshot kind、provenance kind 均为 `first_party_snapshot` 且 `public_official_snapshot=true`；Radar、对比、证据、compact 和通知链路均 fail closed，开发 seed 不再可能被标成官网榜单。
- `preview.2` 候选固定为 Build 101；打包门禁注入并回读官方快照 URL、禁用未发布的 Sparkle 通道、拒绝复用 `preview.1`，并要求包含未跟踪文件在内的工作树干净（忽略 `.gitignore` 内容）、HEAD 稳定及 App／ZIP 内 `ModelDialSourceCommit` 精确一致。
- 在干净提交 `a20d14e` 上生成 `preview.2` DMG、ZIP、SPDX SBOM 和 `SHA256SUMS`；tag 精确指向该二进制源码提交，4 个资产已上传公开 GitHub prerelease。公开 URL 无认证回下载后的 DMG 只读挂载、ZIP 解包、哈希、bundle SBOM、深层 ad-hoc 签名、arm64 兼容性和冻结后端官方快照刷新均通过复验。
- 在干净提交 `16e0dd2cafa82ef1b77b719edc0a3db90e5bf68f` 上生成 Build 102 的 `preview.3` DMG、ZIP、SPDX SBOM 和 `SHA256SUMS`；产物位于独立的忽略目录，未覆盖历史预览清单。三项 SHA-256、DMG 只读挂载、ZIP 解包、App 身份／源码提交／官方 Radar URL／空 Sparkle 通道、深层 ad-hoc 签名、macOS 13 兼容性、两份 bundle SBOM 和冻结后端空目录刷新均通过本地复验。
- 官方 Radar 索引和归档请求的默认单次超时由 3 秒提高到 8 秒；App 首次失败重试由 5 分钟提前到 30 秒，后续仍按 5 分钟、15 分钟、1 小时和 6 小时退避。调度版本、回归和 Build 103／`preview.4` 防覆盖合同已同步，远端信任门禁和缓存回退行为不变。
- 在干净提交 `7237db3413a040f9ad912f7c21917aec392323f3` 上生成 Build 103 的 `preview.4` DMG、ZIP、SPDX SBOM 和 `SHA256SUMS`；三项哈希、DMG 只读挂载、ZIP 解包、版本／build／源码提交、官方 Radar URL、空 Sparkle 通道、深层 ad-hoc 签名、macOS 13 兼容性、两份 bundle SBOM 和主程序一致性均通过。ZIP 冻结后端在 4.2 秒慢响应和真实官方端点两个全新隔离目录均完成刷新。
- Build 104／105 的 preview 更新身份、R2 版本化资产和 GitHub 镜像已发布；真实 UI 验收发现 `SURequireSignedFeed` 缺少 Sparkle 2.9.4 强制要求的 `SUVerifyUpdateBeforeExtraction`，因此将两版明确标记为需要手动升级的历史版本。
- `preview.7`（Build 106）补齐签名 feed 与解压前验签的组合门禁；安装后设置页的更新服务正常启用。`preview.8`（Build 107）在干净提交 `537dfaaf6204557133689a8b45e6729a6cf66bd6` 上完成发布；`preview.9`（Build 108）在 `abf28096ccc30fb923213e257eb131a069b79633` 上完成发布；`preview.10`（Build 109）在 `d60488b4d2d22ca35f44f552521ccb5354fc5640` 上完成 fresh build、产物校验、GitHub／R2 发布和签名 appcast 更新。
- `/Applications` 中的 Build 106 已依次通过 Sparkle 升级到 Build 107、108 和 109。最后一次真实下载 `15,432,016` bytes Build 109 ZIP 并完成验签、安装和重启；新 App 回读精确 build／源码提交／feed／公钥和安全开关，深层签名有效，再次检查显示“当前已是最新版本”。升级前后的 81 个持久文件无新增或缺失，仅 4 个实时使用观察文件按预期更新。
- 对比页“切换后的实际变化”按真实生效配置维护独立 `actual_switch` 时段，不再依赖当时是否存在推荐或对比上下文；轮询间已经完成的目标配置任务可回溯切换边界，复用会话、已关闭时段的迟到记录和超过 100 段的历史累计均可继续准确归因。App 空闲刷新会先由命令层观察本机使用记录，再发布完整只读快照；相同模型／档位的近期使用记录用 Provider 消歧，无法唯一确认时继续 fail closed。

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
- [x] `v0.1.0-preview.3` 已在干净源码提交上生成并完成本地产物验收；Build 102、官方 Radar、空 Sparkle 通道、arm64、ad-hoc 签名、SPDX 和精确资产哈希均有记录。
- [x] 经单独授权发布 `v0.1.0-preview.3` annotated tag／GitHub prerelease，并从公开 URL 无认证回下载复测；双语 README 已切换到 `preview.3`。
- [x] 经单独授权发布 `v0.1.0-preview.4` annotated tag／GitHub prerelease，并从公开 URL 无认证回下载复测；公开 ZIP 在第一次官方刷新瞬时失败后按 30 秒 App 级退避重试成功，双语 README 已切换到 `preview.4`。
- [x] `preview.5`／`preview.6` 的历史资产已镜像至 GitHub 与 R2，并明确记录更新器配置缺陷；它们不再作为安装入口。
- [x] `preview.7`～`preview.10` 已使用长期 Ed25519 身份、独立 preview appcast 和不可变 R2 版本路径发布；Build 106 → 107 → 108 → 109 的真实 App 内升级、重启和最新版本复查通过，双语 README 已切换到 `preview.10`。

## 兼容性边界

- 当前可发行目标是 macOS 13+ Apple Silicon；Windows 客户端仍是后续独立里程碑，不能把 macOS 验证写成跨平台验收。
- 自定义 endpoint 的 HTTP 支持为明确兼容行为；公开客户端不会替用户强制升级传输协议，但不会把认证头带到跨源重定向目标。
- 源码构建默认不配置远端参考快照，App 的本地扫描与历史查看不依赖官网或私有服务。
- `build-dev.sh` 只适用于 Swift／资源迭代；修改 `scanner/`、`scripts/` 或 `questions/` 后必须重新运行 `./build.sh`。

## 下一步

1. 在另一台 Gatekeeper 开启、没有开发环境的 macOS 13+ Apple Silicon 机器上，完成 `preview.10` DMG 的“仍要打开”首次安装验收。
2. 为 Ed25519 私钥增加独立离线备份和恢复演练；正式 stable 通道继续使用独立 URL，不复用 preview appcast。
3. 继续处理正式 `v0.1.0` 的 Developer ID、notarization、独立快照签名、错误签名／404／离线升级矩阵和干净机器门槛；Intel 仍按真实需求决定是否建立 universal2 里程碑。

## 最近验证

- 2026-08-09：完成个人 Homebrew Tap 的公开安装入口闭环。App 双语 README 已由公开 `main` 提交 `57c86b0` 发布，`v0.1.0-preview.10` GitHub prerelease 正文已同步完整限定命令、同一 DMG／SHA-256 和 scoped quarantine 边界；`modeldial.com` 中英文入口经 Cloudflare Pages 生产部署 `32bf53f7-aa8d-4cef-90f5-7ebfdef3df57` 发布，回滚基线为 `44b4507a-2b18-45ba-bf06-dcbd1c300262`。不可变部署地址与主域首页 SHA-256 一致；中英文首页、Radar、数据许可路由和 `www` 跳转均返回 `200`，生产 HTML 实际包含 Homebrew 命令、Tap、DMG、unsigned 与 `com.apple.quarantine` 披露，内置最新官方快照为 `2026-08-09T12:00:00Z`。本轮未改 stable Cask／appcast、DNS、Worker、Container、R2、Secret 或评测计划。
- 2026-08-09：修复本机来源在当前活动配置切换为未测试状态时短暂清空的问题。App 现在继续展示上一轮已完成的本机榜单和原完成时间，当前配置仍保持 `needs_test`，历史行不会被误标为当前项；只有新的完整本机证据就绪后才回到后端资格过滤结果。新增 Swift 回归先复现空榜单再通过；AppSessionStore 定向可执行测试 `1/1`、Expanded Selection 契约 `100/100`、Presenter 可执行测试 `5/5` 通过，`./build-dev.sh` 成功生成并签名验证 `build/modeldial-dev.app`，复用现有 candidate 的冻结后端。未覆盖 `/Applications/modeldial.app`，尚未把开发包验收写成已安装 App 的真实 UI 验收。
- 2026-08-09：完成 unsigned `preview.10` 个人 Homebrew Tap 发布与远端验收。Tap `main` 已推送到 `99a4e4e`；`ruby -c` 与 `brew style` 通过，`brew audit --cask --new` 的跨域 `verified` 与 `livecheck` 问题已修复，只剩 prerelease 和仓库知名度两项官方 `homebrew/cask` 收录门槛。Homebrew 6 通过完整限定命令自动添加 GitHub Tap 并只 trust `tianwdong/tap/modeldial`；下载 DMG SHA-256 精确为 `b03fcadc9e89104c162c18bbba836e2603de861db93db3e1cb159e6aaafd1372`。临时 App 目录安装、卸载和重装通过，bundle 回读 `0.1.0`／Build 109／macOS 13.0／thin arm64，深层 ad-hoc 签名有效，Sparkle preview feed 与 44 字节 Ed25519 公钥存在，整个 bundle 无 `com.apple.quarantine`；卸载后单 Cask trust 自动移除，现有 `/Applications/modeldial.app` 未覆盖。发布／签名定向回归 `29/29`、`git diff --check` 通过。
- 2026-08-09：`v0.1.0-preview.10` 在干净提交 `d60488b4d2d22ca35f44f552521ccb5354fc5640` 上完成 Build 109 fresh build；全量 Python／Swift 合同 `1437/1437`（`659.180s`，`ResourceWarning` 按错误处理）、DMG `17,648,316` bytes、ZIP `15,432,016` bytes、SPDX SBOM、SHA-256、thin arm64、60 个 Mach-O／65 个架构记录、macOS 13 门禁和深层 ad-hoc 签名均通过。冻结后端在隔离目录从官方端点刷新到 `snapshot-2026-08-09T06-00-00Z` 并完成缓存复读。annotated tag、GitHub prerelease、四项 GitHub／R2 资产和签名 appcast 已发布；公开回下载、feed 与 enclosure Ed25519 验签通过。发布前存在性检查曾留下大文件无查询 URL 的临时 `404` 负缓存，因此 appcast 对同一不可变 ZIP 使用 `?v=109`，该精确 URL 的字节和哈希已复验。随后 `/Applications` 的 Build 108 经真实 SwiftUI 设置页完成发现、下载、验签、安装和重启到 Build 109；再次检查显示“当前已是最新版本”，81 个持久文件无新增或缺失，仅 4 个实时使用观察文件按预期更新。双语 README 与发布说明已切换到 `preview.10`。
- 2026-08-09：修复官方 Radar 缓存跨来源复用与多文件非事务写入：HTTP cache 改为绑定规范化 `index_url`／ETag 的单文件原子 bundle，完整重验后一次替换；旧 `.http-cache.json`／根目录缓存只读兼容，来源未知、来源不符、损坏 bundle、写入／replace 失败和断链 symlink 均 fail closed。同轮把启动维护改为成功后结束、warning／load error 后 `250ms` 重试且最多 3 次，并保证 startup retry 不吞手动强制远端刷新。定向组合回归 `62/62`、参考快照 `38/38`、全量 Python／Swift 合同 `1437/1437`（`650.651s`，`ResourceWarning` 按错误处理）、`git diff --check` 和完整 `./build.sh` 通过；candidate 的 60 个 Mach-O／65 个架构记录、macOS 13 门槛、深层 ad-hoc 签名和 Designated Requirement 通过。冻结后端在隔离全新目录保持 `idle`／零历史／零启用目标；同源离线使用 last-good cache，切换来源且离线时回退 bundled。`build/modeldial.app` 与 `/Applications` 均未覆盖。私有消费者必须在绑定本提交时单独更新公共核心锁并运行双仓边界、私有回归和 Container 验证；公共仓不记录私有消费者的动态锁状态。本轮未发布。
- 2026-08-09：`v0.1.0-preview.9` 在干净提交 `abf28096ccc30fb923213e257eb131a069b79633` 上完成 Build 108 fresh build；全量 Python／Swift 合同 `1429/1429` 分两步全部实际执行并通过，DMG `17,652,142` bytes、ZIP `15,441,549` bytes、SPDX SBOM、SHA-256、thin arm64、60 个 Mach-O／65 个架构记录、macOS 13 门禁和深层 ad-hoc 签名通过。annotated tag、GitHub prerelease、四项公开资产、R2 不可变镜像和签名 appcast 已发布，公开回下载与 Ed25519 验签通过。随后从 `/Applications` 的 Build 107 经真实 SwiftUI 设置页完成下载、验签、安装和重启到 Build 108；再次检查显示“当前已是最新版本”，81 个持久文件无缺失。更新后的真实对比页显示 177 次切换后已完成任务，等待时间与参考费用覆盖 125／177 次，确认统计修复已落入更新安装包。
- 2026-08-09：修复对比页“切换后的实际变化”漏算与长期不增长。新增回归覆盖轮询间切换回溯、复用会话、无对比上下文切换、未观测默认项不误判、已关闭时段迟到对账、105 段累计不回退、App 周期观察失败降级和同模型／档位 Provider 消歧；全量 Python／Swift 合同 `1429/1429`（`682.740s`）及 `git diff --check` 通过，完整 `./build.sh` 生成并验证 `build/modeldial-candidate.app`。隔离复制的本机数据经新逻辑从 79 次恢复到 128 次，第二次观察仍为 128；真实候选 App 启动后自动显示 128 次、等待时间约节省 4 小时 33 分、参考费用约节省 `$157.72`，下一自动刷新周期仍为 128。未覆盖 `/Applications`，未创建提交、tag、Release 或发布。
- 2026-08-09：定位并修复 preview 更新器启动失败：Sparkle 2.9.4 在 `SURequireSignedFeed=true` 时要求同时启用 `SUVerifyUpdateBeforeExtraction=true`。修复后的 `preview.7`（Build 106）设置页“立即检查”可用；随后发布 `preview.8`（Build 107）、四项 GitHub prerelease 资产、R2 不可变镜像和 `https://updates.modeldial.com/macos/preview/appcast.xml`。在线 appcast 与 ZIP 的 Ed25519 验签通过，feed 精确指向 `15,418,953` bytes ZIP。
- 2026-08-09：从 `/Applications` 中的 Build 106 在真实 SwiftUI 设置页发现 Build 107，完成 15.4 MB 下载、验签、“安装并重启应用”和重新启动；新 App 回读 Build 107、源码提交 `537dfaaf6204557133689a8b45e6729a6cf66bd6`、preview feed、公钥、签名 feed 与解压前验签开关，深层 ad-hoc 签名有效。再次点击“立即检查”显示“当前已是最新版本”。升级前 94 个已有文件中，75 个内容不变、6 个实时状态文件正常更新、13 个缺失项全部为已消费的 `session-events/inbox` 临时事件。
- 2026-08-09：公开 `main` 推送至 `155f3f7`；`v0.1.0-preview.4` annotated tag object 为 `d6b96b9c558214388620bee3ace3db2e6bd4dded`，peeled commit 与 App 内 `ModelDialSourceCommit` 均精确为 `7237db3413a040f9ad912f7c21917aec392323f3`。GitHub prerelease 非 draft 且仅有四项预期资产；主代理和独立 Luna 审计确认四项大小／SHA-256、两份 bundle 身份和主程序一致。使用不带 GitHub API 认证的公开 URL 回下载时，首个 DMG 传输曾被 CDN 半途截断，有限传输重试后四项大小和哈希全部精确匹配；公开 DMG／ZIP 的容器、版本／build／源码提交／官方 Radar URL／空更新通道、thin arm64、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、深层 ad-hoc 签名和 bundle SPDX 均通过。公开 ZIP 第一次真实官方刷新以 `unavailable` 回退，30 秒后第二次刷新成功并取得 `snapshot-2026-08-09T00-00-00Z` 的 15 条公开官方第一方结果，真实覆盖了本版快速重试路径。当前机器 Gatekeeper assessments disabled，未覆盖 `/Applications`，未核销人工放行验收。
- 2026-08-09：`v0.1.0-preview.4` 本地发布候选在干净源码提交 `7237db3413a040f9ad912f7c21917aec392323f3` 上完成 release-only fresh build；独立目录生成 Build 103 DMG `17,631,085` bytes、ZIP `15,418,537` bytes、SPDX SBOM `200,931` bytes 和 `SHA256SUMS`。三项 SHA-256、DMG 容器与只读挂载、ZIP 解包、两份 App 的版本／build／源码提交／官方 Radar URL／空 Sparkle 通道／thin arm64、深层 ad-hoc 签名、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、bundle SBOM 和主程序一致性均通过。ZIP 内冻结后端在 `index.json` 延迟 4.2 秒时用 `4.512s` 完成刷新；真实官方端点在全新隔离目录用 `11.101s` 取得 `snapshot-2026-08-09T00-00-00Z` 的 15 条公开官方第一方结果。这一步仅形成本地候选，后续公开验收见上条。
- 2026-08-09：完成 `v0.1.0-preview.4` 的源码级 Radar 韧性修复：索引和归档的单请求默认超时由 3 秒提高到 8 秒，失败后的首次 App 自动重试由 5 分钟提前到 30 秒，连续失败仍按 5 分钟、15 分钟、1 小时和 6 小时退避；调度版本从 3 升到 4。marketing version 保持 `0.1.0`，build 升至 103，打包默认 label 升至 `preview.4` 并拒绝覆盖三个已公开预览。定向回归 `66/66`、全量 Python `1422/1422`（`651.097s`，`ResourceWarning` 按错误处理）和 `git diff --check` 通过。
- 2026-08-09：公开 `main` 推送至 `35b8472`；`v0.1.0-preview.3` annotated tag object 为 `d46b011`，peeled commit 与 App 内 `ModelDialSourceCommit` 均精确为 `16e0dd2cafa82ef1b77b719edc0a3db90e5bf68f`。GitHub prerelease 非 draft 且仅有四项预期资产；主代理和独立 Luna 复核均使用不带 GitHub API 认证的公开 URL 下载，三项 SHA-256 与大小一致。公开 DMG／ZIP 的容器、版本／build／源码提交／官方 Radar URL／空 Sparkle 通道、thin arm64、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、深层 ad-hoc 签名和 bundle SPDX 均通过。冻结后端复验期间，官方 `index.json` 曾短时出现约 4.0～4.7 秒首字节延迟并超过当前 3 秒单请求门限，正确回退到内置数据；端点随后连续 5 次恢复至 0.39～1.07 秒，重新下载的公开 ZIP 在全新隔离目录最终得到 `http/refreshed` 和 15 条公开官方第一方结果。当前机器 Gatekeeper assessments disabled，未覆盖 `/Applications`，未核销人工放行验收。
- 2026-08-09：`preview.3` 本地发布候选在干净源码提交 `16e0dd2cafa82ef1b77b719edc0a3db90e5bf68f` 上完成 release-only fresh build；独立目录生成 Build 102 DMG `17,631,107` bytes、ZIP `15,405,346` bytes、SPDX SBOM `200,931` bytes 和 `SHA256SUMS`。三项哈希自校验、DMG 容器与只读挂载、ZIP 解包、DMG／ZIP App 的版本／build／完整源码提交／官方 Radar URL／空 Sparkle feed 与公钥／thin arm64、深层严格 ad-hoc 签名、60 个 Mach-O／65 个架构记录的 macOS 13 门禁和两份 bundle SBOM 复验均通过；两份主程序 SHA-256 一致。ZIP 内冻结后端在隔离 HOME 与全新数据目录从官方端点得到 `http/refreshed`，缓存 `snapshot-2026-08-09T00-00-00Z` 精确为 15 条公开官方第一方结果。当前机器 Gatekeeper assessments disabled，未把本机 `spctl` 接受写成人工放行验收；未创建 tag／Release、未上传资产、未改 README 的 `preview.2` 下载链接。
- 2026-08-09：打包前 Build 102 候选固定为 `0.1.0（Build 102）`，构建合同定向回归 `28/28`、全量 Python `1421/1421`（`645.560s`）和 `git diff --check` 通过。先以源码提交 `ec2284d`、官方 `https://reference.modeldial.com/reference-snapshots`、空 Sparkle feed／公钥完成完整 `./build.sh`；candidate 回读版本、源码提交和 Radar URL 精确一致，60 个 Mach-O／65 个架构记录通过 macOS 13 门禁，thin arm64 与深层 ad-hoc 签名有效。随后在仅改变 bundle id／隔离 HOME 的临时副本中从零启动，没有点击刷新，约 19 秒后自动取得 `snapshot-2026-08-09T00-00-00Z`，本地缓存精确为 15 条并在 SwiftUI Radar 展示 08:00 新榜单；临时副本和隔离数据已删除。3 个交互提交、Build 102 合同和双语 README／GIF／验证记录共 5 个提交已 fast-forward 推送，远端 `main` 到达 `a1b7062`；标准 `build/modeldial-candidate.app` 随后从该精确提交重建并启动，继续回读官方 Radar URL 与空 Sparkle 通道。本次未创建 `preview.3`、tag、Release 或正式 Sparkle 通道。
- 2026-08-09：完成一次隔离的 Sparkle 设置页真实升级演练。以公开 `preview.2` 的 `0.1.0（Build 101）` 为基线，仅在临时副本注入一次性 EdDSA 公钥和临时 HTTPS appcast；当前交互修复提交 `b20cac0` 以 `0.1.0（Build 102）` 完整 Release 构建，60 个 Mach-O／65 个架构记录的 macOS 13 门禁、深层 ad-hoc 签名、appcast EdDSA 签名及 `sign_update --verify` 均通过。App 在“设置 → 软件更新”发现 Build 102，实际请求 appcast 与 `15,414,550` bytes ZIP，完成“安装并重启应用”后同一路径回读为 Build 102／源码提交 `b20cac0`；隔离数据哨兵保留，真实配置与历史文件的时间和大小未变化，随后再次检查显示“当前已是最新版本”。更新后的无障碍树只有一个“收起”按钮，确认新交互实现已落入升级包。本次未配置永久 feed／正式密钥，未修改 GitHub、tag、Release 或生产服务；公开 `preview.2` 仍不能自行发现后续更新。
- 2026-08-09：基于当前 `main` 源码的一次性临时构建录制中英文真实 SwiftUI 状态，录制钩子未进入仓库；两条最终 GIF 均为 `840×406`、10 fps、86 帧、8.6 秒，分别为 `1,756,924` 与 `1,878,796` bytes，覆盖 compact → Radar → Compare → Radar → compact，且启动数据稳定为 15 个档位。双语 README 同步压缩重复说明，把官方 Radar、`modeldial.com`、DMG 安装、unsigned／unnotarized 手动放行、本地评测可选、会话观察、隐私和源码构建放回同一条阅读路径。两份 README 共检查 44 个本地 Markdown／HTML 引用且 `0` 缺失；README／构建合同回归 `18/18`、GIF 元数据检查和 `git diff --check` 通过。原始帧与 MP4 只保存在被忽略的 `artifacts/readme-recordings/`，未修改 App 源码、发布资产或远端 Release。
- 2026-08-08：针对用户复测仍可感知的点击卡顿做主线程实采样，旧实现一次展开触发约 `1040ms` 的 SwiftUI `GraphHost.flushTransactions`，完整榜单树在点击路径内构建并因并发 snapshot 更新再次求值；现将展开内容以稳定输入常驻预热、用 `Equatable` 隔离无关根状态、固定单一路径标题布局并将榜单改为 `LazyVStack`，点击只切换外壳与已预热内容可见性。开发包热点击采样降为约 `26ms` 的 SwiftUI transaction，`openExpanded()` 约 `3ms`，展开时不再重建 `ExpandedSelectionView`；冷启动预热由约 `2007ms` 降至约 `818ms`。用户人工确认展开已顺滑；随后把收起命中区恢复为“箭头＋标题＋左侧剩余空白”整块单一按钮，自动化从按钮中心完成 expanded → compact → expanded 往返，且无障碍树始终只有一个“收起”。定向回归 `154/154`、全量 Python `1421/1421`（`677.085s`）、`git diff --check`、`./build-dev.sh` 和完整 `./build.sh` 通过；正式 `0.1.0（Build 101）` candidate 的冻结后端 smoke、60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名和 Designated Requirement 均通过。未创建 tag／Release，未 push。
- 2026-08-08：针对窗口级录像确认的点击展开中段停顿，改为先提交轻量外壳的 `0.42s` 高阻尼 spring，再于 `220ms` 后隐藏挂载完整榜单树，并在下一次主线程调度中淡入；展开期间保留 compact 身份内容，避免首帧文字挤压，收起仍立即停止内容命中并连续缩回。定向展开／交互回归 `136/136`、架构基线 `11/11`、全量 Python `1421/1421`（`643.747s`，`ResourceWarning` 按错误处理）、`git diff --check` 和完整 `./build.sh` 通过；candidate 的 60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名和 Designated Requirement 均通过。对 `build/modeldial-candidate.app` 录制 180 帧、`2240×1080`、9 秒窗口级样片，展开动作帧持续变化，不再出现上一版的中间尺寸静止平台，完整榜单在壳层接近最终尺寸后渐入，收起段保持连续。候选 App 已按完整路径启动供人工体感验收；验证完成后形成本地 Git 提交，未 push。
- 2026-08-08：参考 `codex-island` 的外壳／内容错峰与 `open-vibe-island` 的延迟卸载，调整胶囊点击展开／收起节奏：大尺寸外壳改为 `0.42s／0.30s` 高阻尼 spring，展开内容在 `110ms` 后渐入，收起内容立即停止命中并淡出，展开树在 `360ms` 后卸载；没有恢复 `Task.yield` 或 AppKit 窗口动画。定向交互合同 `36/36`、`git diff --check` 和完整 `./build.sh` 通过；新 Release candidate 的 60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名和 Designated Requirement 均通过，已启动 `modeldial-candidate-20260808-214603-48056.app` 供人工体感验收；该候选随后被上条的延后挂载版本替代。
- 2026-08-08：修复 compact Hover 与点击展开／收起的交互迟滞。胶囊与会话面板改由一个尺寸稳定的连接区域统一监听 Hover，移除子视图之间相互打断的监听；窗口鼠标事件接管只在状态实际变化时写入；展开／收起的固定 `40ms` 等待改为单次调度让步，弹簧响应由 `0.42s／0.30s` 收紧为 `0.30s／0.24s`。最终定向 UI 回归 `35/35`、全量 Python `1420/1420`（`734.577s`，`ResourceWarning` 按错误处理）、架构基线 `11/11`、`git diff --check` 和完整 `./build.sh` 通过；正式 candidate 的 60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名和 Designated Requirement 均通过。在开发候选上用调试日志确认 Hover 动作只产生一组进入／离开，在最终 Release candidate 上完成 compact → expanded → compact 实机往返；未触发扫描、未覆盖 `/Applications`。
- 2026-08-08：`main` 推送至 `fb615be`；annotated tag `v0.1.0-preview.2` 的 peeled commit 精确为二进制记录的 `a20d14e`，公开 GitHub prerelease 已创建且仅包含 4 个预期资产。随后使用不带 GitHub API 认证的 `curl` 从公开 URL 下载四项资产，`SHA256SUMS` 三项通过且与本地清单一致；DMG 只读挂载、ZIP 解包、`0.1.0（Build 101）`／源码提交／官方 Radar URL／空更新通道回读、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、深层 ad-hoc 签名和 bundle SPDX 均通过。公开下载副本的冻结后端首次刷新成功，后续条件请求为 `http/not_modified`，缓存保持 15 条 provenance 合格的第一方结果。未覆盖 `/Applications`，未把本机包体验证写成人工 UI／Gatekeeper 验收。
- 2026-08-08：`preview.2` 针对性修复已形成本地提交 `a20d14e`，并由 release-only packaging 在该干净提交上生成 Build 101 DMG `17,766,496` bytes、ZIP `15,451,034` bytes、SPDX SBOM `200,931` bytes 和 `SHA256SUMS`。三项产物 SHA-256、DMG 容器与只读挂载、ZIP 解包、bundle SBOM、官方 Radar URL、空 Sparkle 配置、精确源码提交和深层 ad-hoc 签名均通过；主程序为 thin arm64，60 个嵌套 Mach-O 均含 arm64。冻结后端在隔离目录从官方端点刷新得到 15 条 provenance 合格的第一方结果。未覆盖 `/Applications`，未创建 tag／Release 或上传资产。
- 2026-08-08：`preview.2` 针对性修复完成；合并定向回归 `187/187`、全量 Python `1420/1420`（`619.586s`，`ResourceWarning` 按错误处理）、架构基线 `11/11`、shell／plist／本地化 JSON／diff 检查通过。使用官方参考快照地址并禁用更新通道的完整 `./build.sh` 生成 `0.1.0（Build 101）` arm64 candidate；60 个 Mach-O／65 个架构记录、深层 ad-hoc 签名和 bundle 回读通过。冻结后端在隔离数据目录真实刷新官方端点成功，delivery 为 `http/refreshed`，得到 15 条 provenance 合格的第一方结果。
- 2026-08-08：中英文 README 完成产品首页重排；App 图标、Hero／双图截图、公开 Release／DMG、官网、语言切换和全部本地 Markdown／HTML 链接检查通过，双语版本／平台／签名限制／构建运行时事实一致。`git diff --check` 与 README 构建合同回归 `17/17` 通过；未改 App、发布资产或远端 Release。
- 2026-08-08：提交 `fcde71f` 已推送到公开 `main`，tag `v0.1.0-preview.1` 指向该提交并创建公开 GitHub prerelease。4 个资产大小与本地产物一致；随后不带 GitHub API 认证从公开 asset URL 重新下载，`SHA256SUMS` 三项均通过，DMG 只读挂载与 ZIP 解包通过，App 为 `0.1.0 (100)`／thin arm64／ad-hoc 且无证书 Authority，bundle SPDX 校验和冻结后端 OpenSSL 3.0.18／mpdecimal 4.0.0／zstd smoke 通过。本机 Gatekeeper disabled，未把此次复测写成“仍要打开”人工验收。
- 2026-08-08：unsigned preview 定向测试 `26/26`、全量 Python `1415/1415`（`619.687s`，`ResourceWarning` 按错误处理）通过。`package-unsigned-preview.sh` 强制 ad-hoc fresh build，PyPI requirements receipt 触发 `--require-hashes` 重建；60 个 Mach-O／65 个架构记录通过 macOS 13 门禁，整包深层签名有效。生成 DMG `17,735,966` bytes、ZIP `15,421,467` bytes、SPDX SBOM `200,931` bytes 和 `SHA256SUMS`；三项 hash、DMG／ZIP 容器、SPDX 2.3 官方 JSON Schema、只读挂载后 bundle SBOM／签名／arm64／冻结后端和 11 个 Legal 文件均通过。未创建 tag、GitHub Release 或上传二进制；当前机器 Gatekeeper disabled，未把本机挂载 smoke 写成人工放行验收。
- 2026-08-08：unsigned preview 的中英文 README、发布清单、ADR 和 `docs/releases/v0.1.0-preview.1.md` 已完成一致性检查；文件存在性、资产命名／警告／手动放行文案和 `git diff --check` 通过，未创建 tag、GitHub Release 或上传二进制。
- 2026-08-08：私有公共核心锁已同步到公开根提交及内容哈希；双仓边界、镜像内公共核心校验、私有边界／Cloud Runner／发布器回归 `50/50`、Cloudflare 普通 Vitest `37/37`、Workers Runtime `1/1`、TypeScript／生成类型、Linux／AMD64 镜像 `/health` 和 `wrangler deploy --dry-run` 全部通过，未部署 Cloudflare 或创建二进制 Release。
- 2026-08-08：endpoint、HTTP 兼容、跨源认证头、隔离 worker、外部 CLI、Codex app-server、候选 grader、构建合同定向回归 `177/177` 通过；随后全量 Python `1404/1404`（`636.856s`）通过。完整 `./build.sh` 完成 Python 3.14.3 冻结、snapshot smoke、Xcode Release、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、Sparkle 嵌套签名、整包严格签名和 Designated Requirement，生成 `build/modeldial-candidate.app` 且未替换正式 App。`./build-dev.sh` 实际从 candidate 复用同一冻结后端并通过严格签名；candidate 题包与源码 11 个文件一致，bundle 不含测试或私有服务目录。`ResourceWarning` 按错误处理，`compileall`、Shell 语法和 `git diff --check` 通过。
- 2026-08-07：锁定 Python runtime 与 macOS 13 bundle 兼容门禁定向回归 `18/18` 通过；candidate 的真实 Mach-O、冻结 runtime smoke、CA store、深层签名和 Designated Requirement 通过。
- 2026-08-07：LiteLLM pricing provenance 定向回归 `16/16` 通过；刷新后 34 个条目的价格数值无变化，policy 与 snapshot 来源身份一致。
