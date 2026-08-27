# ADR：价格目录与 App 二进制解耦

- 日期：2026-08-27
- 状态：已接受，已实现（未发布）

## 背景

ModelDial 过去把当前价格快照和 LiteLLM／官网价格更新器一起冻结进 App。新增模型或价格变化后，即使 scanner、DTO 和界面都没有变化，也必须重新构建并发布整套 App；官网和远端运行器还可能各自保留另一份价格事实。该路径扩大了发布成本，也容易让“当前价格”和历史评测使用的价格混在一起。

价格会变化，但一次已经完成的评测必须能按当时价格复核。因此本决策只把“新运行使用哪个价格快照”改成远端目录读取，不重算历史结果，也不建立 scanner 代码热更新通道。

## 决策

### 1. 单一价格目录

第一方只读目录固定使用以下协议：

```text
https://modeldial.com/data/pricing/
  current.json
  snapshots/pricing-v1-<content-sha256>.json
```

`current.json` 只保存当前 `snapshot_id`、不可变快照相对路径、原始文件 SHA-256、发布时间和模型数。`snapshots/` 下的对象按语义内容哈希命名，发布后不得覆盖；修正价格必须产生新 `snapshot_id`。

公共仓库继续拥有价格策略、来源固定、生成器、schema 和读取校验。官网的 `public/data/pricing/` 是第一方只读目录，Pages 发布时原样输出；私有发布侧不在 Worker、网站业务代码或 App 中再维护手写费率。现有 Reference Worker 路由只允许榜单的固定 index／archive 路径，本方案不为价格目录扩大该 Worker 的公开路径。

### 2. App 与远端运行器

新运行开始时，App 和远端运行器读取目录，并依次校验：

- HTTPS（仅本机 loopback 测试允许 HTTP）和同源跳转；
- manifest／snapshot 大小、schema、模型数和费率上限；
- snapshot 原始 SHA-256；
- `snapshot_id`、`content_hash` 与去除易变抓取时间后的语义内容哈希一致；
- 每个价格条目保留来源 provenance。

校验成功后保存 `pricing/current.json`、`pricing/snapshots/<snapshot_id>.json` 和本次运行专属的 `pricing/runs/<scope_id>.json`。目录不可用或校验失败时使用最后一次有效快照，再回退 App 内置快照；失败不能阻止扫描，也不能把未知价格当作零。

App 发行包只包含读取器和一个可用的内置回退快照，不再包含上游抓取器或价格维护 policy。`MODELDIAL_PRICING_CATALOG_URL` 可为源码运行指定兼容目录；显式设为空会关闭目录读取并直接冻结 last-good／内置快照，不会转而从用户设备抓取 LiteLLM 或供应商网页。

### 3. 网站与历史数据

官网的当前价格 API 从同一份目录产物生成只读镜像，不维护第二份费率。官网 Radar 已发布批次继续展示批次内冻结的 `pricing_snapshot_id` 和参考费用；新目录上线后不得重算、覆盖或跨快照比较历史费用。

更新当前价格需要生成并发布一个新目录快照。App 无需重建；若官网要更新其静态镜像，仍按 Pages 的独立发布流程发布，但费率内容只能来自同一个目录产物。

### 4. 完整性边界

第一版依赖第一方 HTTPS、原始文件 SHA-256、内容寻址身份和严格 schema。它能够发现传输损坏、错误对象和非预期内容，但不能替代独立发布签名。后续如引入离线公钥签名，应在 manifest 上增加版本化签名字段并保持旧客户端可回退，不在本次为此增加运行时加密依赖。

## 不采用的方案

### 每次改价重新构建 App

实现简单，但把高频数据变更绑在低频二进制发布上，继续放大构建、签名、分发和用户升级成本，因此不采用。

### App 直接抓取 LiteLLM 或各供应商网页

会把来源兼容、官网覆盖、促销时段和供应链校验带到每个用户设备，难以复现，也让上游变化直接影响运行，因此只允许维护侧生成受审快照。

### 启动后覆盖所有历史费用

会破坏评测证据和跨批次可比性，因此历史运行永远按其冻结的 `pricing_snapshot_id` 解释。

## 发布与回滚

目录发布顺序为：生成候选、运行公共与私有合同测试、上传不可变 snapshot、回读并校验、最后切换 `current.json`。回滚只把 `current.json` 指回已存在的有效 snapshot；不得覆盖不可变对象。App 或远端运行器拉取失败时自动使用本地 last-good／baked 快照。

本 ADR 不授权上传 R2、更新公共核心锁、构建或部署 Container、部署 Pages、发布 App、commit 或 push；这些动作继续按各自发布门禁单独确认。
