# Open-source architecture boundary

ModelDial 的公开仓库包含本地 App 与其可验证的评测核心；官方站点和远端服务作为独立、私有部署维护。

```text
macOS App (Swift)
  └─ Native Bridge DTO
       └─ scanner (Python)
            ├─ local CLI / configured model endpoint
            ├─ local history and runtime state
            └─ versioned reference-snapshot reader

Private service, not in this repository
  ├─ official website
  ├─ Cloudflare Worker and administration surface
  ├─ remote evaluation runner
  └─ reference-snapshot publication pipeline
```

## Public contracts

- App snapshot、refresh snapshot 和 runtime event 的版本化 DTO 与 fixture。
- 本地扫描、重试、历史持久化、评分与展示投影。
- 参考榜单 JSON 的下载、校验、缓存和降级读取逻辑。

## Source ownership

本仓库是上述公开 App 和本地核心的唯一源码主仓。私有服务通过固定 commit、内容哈希和版本化 DTO／fixture 消费公开合同，不再保存 `Sources／Resources／scanner／scripts／questions／tests／devtools` 副本。Cloudflare 构建前从锁定 commit 导出公共 scanner、题包和价格更新器，并只把这些公共文件、三个私有 overlay、锁文件和任务规范放入独立的最小 Docker build context；镜像内再次校验内容哈希。`devtools/check_public_private_drift.py` 同时验证公共路径唯一性、提交与内容锁、公共核心工作树状态，以及 Docker `COPY` 白名单。

## Private implementation

- 官方快照如何运行、复核、签名、发布和回滚。
- Cloudflare 账户、路由、存储、密钥、管理 API 与部署配置。
- 官网源码、运营内容和生产监控。

源码和 Xcode 工程默认把第一方只读快照地址保持为空；正式 ModelDial 品牌构建通过 `MODELDIAL_REFERENCE_SNAPSHOT_URL` 构建参数显式注入公开地址。`NativeBridgeClient` 仅在进程环境未显式设置时把构建值传给本地后端，`scanner` 自身仍保持空默认值。第三方也可以提供符合公开协议的快照源，但不能使用 ModelDial 品牌暗示官方背书。

远端内容进入“官网榜单”前必须同时满足 snapshot kind 与 provenance kind 均为 `first_party_snapshot`，且 `public_official_snapshot=true`。Python advisor projection 与 Swift `trustedLatest` 使用同一 fail-closed 规则；`development_seed`、缺失 provenance 或不一致 kind 只能用于开发降级，不得进入 Radar、对比、证据、compact 或通知中的官方展示。独立发布者签名仍是正式发行前的后续完整性门槛。

Sparkle 更新地址与 EdDSA 公钥同样默认留空，只有正式构建同时显式提供 `MODELDIAL_UPDATE_FEED_URL` 与 `MODELDIAL_UPDATE_PUBLIC_ED_KEY` 时才启用；只提供其中一个会构建失败。unsigned preview 明确禁用更新通道，避免把尚未发布的 appcast 呈现为可用升级路径。
