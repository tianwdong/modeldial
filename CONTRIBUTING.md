# Contributing

感谢你改进 ModelDial。请保持改动小而可验证，并遵守以下边界：

1. 本仓库是 App 与本地评测核心的唯一源码主仓；不要先在私有服务仓库修改同名文件再人工复制回来。
2. 先说明问题、预期行为和验证方式。
3. 不在 View 中复制评分、趋势、费用或运行状态规则；这些规则应放在 Presenter、Projector 或 scanner 的权威边界。
4. Query 保持只读；写入、恢复、观察和刷新使用显式 Command 或 Application Service。
5. 不提交 API Key、Token、个人端点、真实会话内容、运行产物或构建目录。
6. 修改 `scanner/`、`scripts/` 或 `questions/` 后运行完整 `./build.sh`；其他改动至少运行相关测试。

私有官网和服务通过版本化 DTO／fixture 或固定的公开 commit 与 App 协作。私有仓不保留公共源码镜像；Cloudflare Container 直接消费本仓公共核心并由内容哈希锁定。更新公共核心后还应执行：

```bash
python3 devtools/check_public_private_drift.py \
  --public-repo /path/to/modeldial \
  --private-mirror /path/to/private/app-mirror
```

提交前建议执行：

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Commit 信息建议使用 `feat:`、`fix:`、`refactor:`、`docs:` 或 `chore:` 前缀，并清楚描述单一目的。
