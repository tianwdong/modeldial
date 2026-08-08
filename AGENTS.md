# AGENTS.md

本文件适用于本仓库及其子目录。

## 项目边界

- 产品入口是 macOS 原生 App；不要添加第二套 Web 运行壳。
- `Sources/` 是 Swift App，`scanner/` 是 Python 后端，二者通过版本化 Native Bridge DTO 通信。
- 官网、Cloudflare、远端评测运行器、快照发布和运营后台不属于本仓库。
- `scanner/legacy_scan_compat.py` 是历史数据兼容边界；不要向其他模块复制兼容判断。

## 源码所有权

- 本仓库是 App、本地 scanner、Native Bridge DTO、Presenter、导出、本地化和相关测试的唯一源码主仓。
- 私有官网、Cloudflare、远端评测运行器、快照发布与运营后台只能依赖这里的版本化协议或固定 commit，不得维护另一套可独立修改的 App 公共实现。
- 私有仓不再保留公共源码镜像；Cloudflare Container 以共同 build context 直接复制本仓公共核心，再叠加私有运行器／发布器模块。公共核心由内容哈希锁定，更新后运行 `devtools/check_public_private_drift.py`、私有回归和 Container dry-run。

## 开发规则

- 修改前先读 `ROADMAP.md`，只处理与任务直接相关的代码。
- `AppSessionStore` 是完整 App snapshot 的唯一 Swift 所有者。
- 展示规则进入 Presenter／Projector，不在 View 中重写业务规则。
- Query 只读；写入、恢复、观察和刷新放在显式 Command／Application Service 中。
- `ScanPlanner`、`ExecutionEngine` 与 `RunStateMachine` 是扫描生命周期的共享权威边界。
- App snapshot、refresh snapshot 和 runtime event 必须保持版本化 DTO 与 fixture。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest tests.test_architecture_baseline -v
./build.sh
```

修改 `scanner/`、`scripts/` 或 `questions/` 后必须运行 `./build.sh`。`build-dev.sh` 只适用于复用已冻结后端的 Swift／资源迭代。

完成开发、修复或重要文档补齐后同步更新 `ROADMAP.md`；只有已实现并验证的事项才能标记完成。
