# ModelDial Roadmap

最后更新：2026-08-08

## 当前阶段

首个公开源码候选的最后加固。公开仓库已经能够独立测试和构建 macOS App；源码公开、正式签名二进制和 GitHub Release 是三个分开的发布门槛。

当前公开源码候选已通过全量验证，并以单一无父 `main` 根提交固化；源码树发布门槛已经完成。正式签名二进制和 GitHub Release 仍是独立门槛，tag、Release 或生产部署需要单独确认。

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
- 双语 README 已包含 `modeldial.com`、未来 GitHub Releases DMG 安装方式、macOS 13+ Apple Silicon 支持边界、源码构建要求和 candidate-only 产物说明。

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

- [ ] 为 PyPI 构建依赖补 artifact hashes、完整许可证清单和 SBOM。
- [ ] 按实际展示逐项复核 provider 名称、图标和商标政策。
- [ ] 为远端参考快照增加独立发布者签名，并在客户端缓存写入前验签。
- [ ] 完成 Developer ID Application 签名、secure timestamp、notarization 和 stapling。
- [ ] 生成版本化 DMG、Sparkle ZIP、appcast、SHA-256 和 SBOM，并保持源码仓不跟踪二进制产物。
- [ ] 在干净的 macOS 13／14 Apple Silicon 机器上完成构建、Gatekeeper 首次安装、升级、Keychain、CLI 探测、hook、配置和历史恢复验收。
- [ ] 经单独授权创建 GitHub Release 并上传 DMG 等发行附件。

## 兼容性边界

- 当前可发行目标是 macOS 13+ Apple Silicon；Windows 客户端仍是后续独立里程碑，不能把 macOS 验证写成跨平台验收。
- 自定义 endpoint 的 HTTP 支持为明确兼容行为；公开客户端不会替用户强制升级传输协议，但不会把认证头带到跨源重定向目标。
- 源码构建默认不配置远端参考快照，App 的本地扫描与历史查看不依赖官网或私有服务。
- `build-dev.sh` 只适用于 Swift／资源迭代；修改 `scanner/`、`scripts/` 或 `questions/` 后必须重新运行 `./build.sh`。

## 下一步

1. 继续处理首个正式二进制 Release 的供应链、签名、公证、干净机器和 DMG 验收。
2. 收集真实 Intel Mac 用户需求，再决定是否建立独立的 universal2 支持里程碑。

## 最近验证

- 2026-08-08：私有公共核心锁已同步到公开根提交及内容哈希；双仓边界、镜像内公共核心校验、私有边界／Cloud Runner／发布器回归 `50/50`、Cloudflare 普通 Vitest `37/37`、Workers Runtime `1/1`、TypeScript／生成类型、Linux／AMD64 镜像 `/health` 和 `wrangler deploy --dry-run` 全部通过，未部署 Cloudflare 或创建二进制 Release。
- 2026-08-08：endpoint、HTTP 兼容、跨源认证头、隔离 worker、外部 CLI、Codex app-server、候选 grader、构建合同定向回归 `177/177` 通过；随后全量 Python `1404/1404`（`636.856s`）通过。完整 `./build.sh` 完成 Python 3.14.3 冻结、snapshot smoke、Xcode Release、60 个 Mach-O／65 个架构记录的 macOS 13 门禁、Sparkle 嵌套签名、整包严格签名和 Designated Requirement，生成 `build/modeldial-candidate.app` 且未替换正式 App。`./build-dev.sh` 实际从 candidate 复用同一冻结后端并通过严格签名；candidate 题包与源码 11 个文件一致，bundle 不含测试或私有服务目录。`ResourceWarning` 按错误处理，`compileall`、Shell 语法和 `git diff --check` 通过。
- 2026-08-07：锁定 Python runtime 与 macOS 13 bundle 兼容门禁定向回归 `18/18` 通过；candidate 的真实 Mach-O、冻结 runtime smoke、CA store、深层签名和 Designated Requirement 通过。
- 2026-08-07：LiteLLM pricing provenance 定向回归 `16/16` 通过；刷新后 34 个条目的价格数值无变化，policy 与 snapshot 来源身份一致。
