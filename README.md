<div align="center">
  <img src="Resources/AppIcon.svg" alt="ModelDial App 图标" width="96" height="96">
  <h1>ModelDial</h1>
  <p><strong>用真实 coding 评测，找到更适合当前任务的模型配置。</strong></p>
  <p>比较完整的 <code>model + effort + route</code> 组合，同时保留质量、耗时、Token 和参考费用证据。</p>
  <p>
    <a href="https://github.com/tianwdong/modeldial/releases/download/v0.1.0-preview.1/modeldial-0.1.0-preview.1-macos-arm64.dmg"><strong>下载 macOS 预览版</strong></a>
    · <a href="https://modeldial.com">官网</a>
    · <a href="https://modeldial.com/radar">官方 Radar</a>
    · <a href="https://github.com/tianwdong/modeldial">GitHub</a>
    · <a href="README.en.md">English</a>
  </p>
  <p><code>macOS 13+</code> · <code>Apple Silicon</code> · <code>本地优先</code> · <code>无内置遥测</code></p>
</div>

![ModelDial 雷达页](docs/screenshots/modeldial-radar-zh.jpg)

*示例数据，不代表实时榜单结果。*

## 先看官方 Radar，本地评测可选

ModelDial 的第一条使用路径是浏览公开的第一方 Radar，而不是要求用户先配置模型或运行本地测试。修复后的 App（下一预览包起）打开后点击菜单栏顶部的 ModelDial 胶囊，即可查看官方定时评测的配置榜单；现在也可以直接访问 [modeldial.com/radar](https://modeldial.com/radar)。浏览官方 Radar 不需要 API Key，也不会消耗你的模型额度。

只有当你希望比较自己的 provider、route 或 effort 组合时，才需要接入本机 Codex、Claude Code、Grok Build 或兼容 endpoint，并运行本地评测。本机结果与官方榜单是两个明确的数据来源，可以分别查看。

## 为什么用 ModelDial

模型名只是配置的一部分。同一个模型在不同 effort、route 或 provider 下，质量、耗时和成本都可能不同。ModelDial 把这些组合放进可重复的 coding 评测里，帮助你用自己的任务证据做选择：

- 用真实的 `model + effort + route` 组合比较，而不是只看模型名称。
- 用版本化题包和评测 profile 固定比较范围，减少一次次手工试错。
- 同时保留质量、耗时、Token、参考费用、失败原因和题目级证据。
- 在本机完成配置、运行和历史留存；是否调用远端模型由你选择的 provider 或 endpoint 决定。

## 产品截图

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/modeldial-compare-zh.jpg" alt="ModelDial 当前配置与候选配置对比"></td>
    <td width="50%"><img src="docs/screenshots/modeldial-settings-scan-zh.jpg" alt="ModelDial 扫描设置"></td>
  </tr>
  <tr>
    <td><strong>配置对比</strong><br>查看当前配置与候选配置的质量、耗时、Token 和参考费用差异。</td>
    <td><strong>扫描策略</strong><br>设置题数、并行度、超时与重试，再运行可重复评测。</td>
  </tr>
</table>

## 下载与首次运行

**当前版本：[`v0.1.0-preview.1`](https://github.com/tianwdong/modeldial/releases/tag/v0.1.0-preview.1)** · [直接下载 Apple Silicon DMG](https://github.com/tianwdong/modeldial/releases/download/v0.1.0-preview.1/modeldial-0.1.0-preview.1-macos-arm64.dmg)

系统要求：macOS 13 或更高版本、Apple Silicon。Intel Mac 当前不支持。如需校验下载文件，请从同一 Release 获取 `SHA256SUMS`，在资产目录运行 `shasum -a 256 -c SHA256SUMS`。

> [!IMPORTANT]
> `v0.1.0-preview.1` 是 unsigned／unnotarized 预览包，没有 Developer ID 签名或 Apple notarization。若 macOS 阻止首次打开，请前往“系统设置 → 隐私与安全性 → 仍要打开”确认该 App；不要关闭 Gatekeeper，也不要使用 `xattr`、`spctl` 或其他命令绕过系统安全检查。

> [!NOTE]
> 已发布的 `v0.1.0-preview.1` 没有写入官方 Radar 数据地址，也不包含本次首次使用流程修复。修复后的源码将进入下一预览包；发布前可先使用[网页 Radar](https://modeldial.com/radar)，或按下文方式从源码构建。

修复版首次运行（下一预览包起）：

1. 打开 DMG，把 `modeldial.app` 拖到 `Applications`，推出 DMG 后从 `Applications` 启动。
2. 点击菜单栏顶部的 ModelDial 胶囊，直接浏览官方 Radar；无需接入本地模型，也无需先运行扫描。
3. 如需生成自己的对比证据，再进入「评测 → 模型接入」，导入 provider 或新增兼容 endpoint，然后选择 profile 运行扫描。

## 工作方式

1. **浏览官方 Radar。** App 从第一方只读快照加载公开榜单，无需本地模型或 API Key。
2. **按需连接模型。** 只有要评测自己的配置时，才导入本机 Codex、Claude Code、Grok Build provider，或配置兼容 endpoint。
3. **运行本地评测。** 题包和 profile 的权威入口是 [`questions/catalog.json`](questions/catalog.json)；按设置中的题数、并行度、超时和重试执行可重复评测。
4. **分别查看证据。** 在官方榜单与本机实测之间切换，查看 Radar、对比和历史，也可以导出榜单图像。

## 核心能力

- **配置级比较：** 记录完整的模型、effort、route 和 provider 身份，保留题目级结果。
- **可解释的结果：** 同时展示质量、耗时、Token、参考费用和失败状态，方便回看一次选择的依据。
- **本机会话观察：** 支持 Codex、Claude Code、Grok Build 的本机会话状态，并可与扫描结果一起查看。
- **菜单栏原生体验：** macOS 原生 App 是唯一产品运行入口；仓库中的脚本和后端模块由 App 或构建脚本调用。

## 隐私

- 配置、扫描历史、运行状态和有限的会话元数据保存在本机。
- API Key 保存在 macOS Keychain；凭据不会写入扫描历史。
- 评测时，合成题目和模型回复会经过你选择的本地 CLI 或模型服务；这些服务各自适用其条款和隐私政策。
- App 不内置遥测，也不上传会话正文或本机评测结果。ModelDial 品牌预览包只读取公开的第一方 Radar 快照；源码构建默认不读取远端参考榜单，只有显式配置兼容的快照地址时才会访问。

更多边界见 [PRIVACY.md](PRIVACY.md) 和 [公开架构边界](docs/architecture.md)。官网、Cloudflare Worker、远端评测运行器和快照发布服务不属于本仓库，也不是 App 的运行入口。

## 从源码构建

源码构建面向 macOS 13+ Apple Silicon，需要 Xcode 16.4。首次运行 `build.sh` 会将锁定的 Python runtime 解包到被忽略的 `build/` 目录，不需要另行安装 Python：

构建输入由 [`python-runtime.lock.json`](build-support/python-runtime.lock.json) 固定为 Python 3.14.3，并由 [`pyinstaller-requirements.txt`](build-support/pyinstaller-requirements.txt) 固定 PyInstaller 6.21.0 及其依赖；完整供应链门禁见 [发布清单](docs/release-checklist.md)。

```bash
MODELDIAL_REFERENCE_SNAPSHOT_URL=https://reference.modeldial.com/reference-snapshots ./build.sh
open build/modeldial-candidate.app
```

上面的构建命令启用官方 Radar。若只需要完全离线的源码构建，直接运行 `./build.sh`；远端参考快照地址默认保持为空。

`build.sh` 会构建 Swift App、冻结 Python 后端并运行 snapshot smoke。只修改 Swift 或资源、且已经完成过一次完整构建时，可以使用：

```bash
./build-dev.sh
```

修改 `scanner/`、`scripts/` 或 `questions/` 后，请重新运行 `./build.sh`。源码构建和二进制预览的独立门槛见 [预览发布说明](docs/releases/v0.1.0-preview.1.md)。

<details>
<summary>移除从源码安装的会话观察 hook</summary>

以下命令只移除 ModelDial 自己的 Codex／Claude Code hook 和 helper，其他 hook 会保留：

```bash
python3 scripts/install_session_observer.py --uninstall
```

</details>

## 开发与测试

完整 Python 回归：

```bash
python3 -m unittest discover -s tests -v
```

修改版本化 DTO 或架构合同时，再运行定向合约回归：

```bash
python3 -m unittest tests.test_architecture_baseline -v
```

提交前检查差异格式：

```bash
git diff --check
```

## 文档

- [公开架构边界](docs/architecture.md)：App、scanner 与私有服务的职责边界。
- [Benchmark 与数据发布策略](docs/benchmark-and-data-policy.md)：题包、答案 fixture、价格快照和 provider 资产。
- [公开内容来源审计](docs/open-source-content-audit.md)：上游来源、attribution 和题包检索留痕。
- [发布清单](docs/release-checklist.md)：源码候选和二进制发行的独立门槛。
- [预览发布说明](docs/releases/v0.1.0-preview.1.md)：v0.1.0-preview.1 的安装步骤与限制。
- [下一预览候选](docs/releases/v0.1.0-preview.2.md)：尚未发布的 v0.1.0-preview.2 修复、验证与剩余门槛。
- [安全策略](SECURITY.md)：私密漏洞报告渠道。

## 参与贡献与许可证

提交改动前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。源代码使用 [Apache License 2.0](LICENSE)。ModelDial 名称、Logo 和 App 图标是品牌资产，不随源代码许可证授权，详情见 [TRADEMARKS.md](TRADEMARKS.md)。随 App 分发的第三方声明见 [NOTICE](NOTICE) 与 [Resources/Legal](Resources/Legal)。
