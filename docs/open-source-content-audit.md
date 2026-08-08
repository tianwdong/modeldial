# Open-source content audit

审计日期：2026-08-07

本文记录公开可复核的上游许可证、仓库 attribution 状态和题包精确短语检索结果。检索结果只描述本次查询范围和日期，不能证明原创性、非侵权或不存在未索引来源；本文也不是法律意见。

## 上游许可证事实

### LiteLLM pricing

- [LiteLLM LICENSE](https://raw.githubusercontent.com/BerriAI/litellm/b45b4b73004261b47369d7d12c97d58b137a732e/LICENSE) 规定：`enterprise/` 目录（如存在）适用其独立许可证；除此之外的仓库内容按 MIT License 提供，并要求保留版权与许可声明。
- [model_prices_and_context_window.json](https://raw.githubusercontent.com/BerriAI/litellm/b45b4b73004261b47369d7d12c97d58b137a732e/model_prices_and_context_window.json) 是仓库根目录的模型价格与上下文元数据文件，文件本身没有单独的许可证头。当前公开策略固定到上游 commit `b45b4b73004261b47369d7d12c97d58b137a732e`，raw 文件 SHA-256 为 `960279570fc4d2bdd56258a58ce30d124097cee61fdec6fdc483d4a057a4909b`。根据上游 LICENSE 的目录边界，它属于上述仓库内容范围；这只是许可证文本与文件位置之间的事实性对应，不是对第三方价格、模型名称、商标或服务条款的额外授权判断。
- 仓库的 [pricing policy](../devtools/pricing/policy.json) 保留不可变上游 URL、commit、raw SHA-256、来源名称和匹配规则；[pricing snapshot](../scanner/pricing_snapshot.json) 保留同一来源身份、生成时间及每个条目的匹配 provenance。网络下载与离线 source file 都必须先通过 raw SHA-256 校验再解析。仓库政策明确不重新许可上游材料，也不表示 LiteLLM 或模型提供商背书。
- [third-party notices](../Resources/Legal/THIRD_PARTY_NOTICES.txt) 已补入 LiteLLM 来源、Berri AI 版权声明和完整 MIT 文本；根级 [NOTICE](../NOTICE) 明确指向 pricing 与 icon 两类第三方材料。

### LobeHub provider icons

- exact package [@lobehub/icons-static-svg@1.94.0 package.json](https://app.unpkg.com/@lobehub/icons-static-svg@1.94.0/files/package.json) 标明版本 `1.94.0`、仓库 `lobehub/lobe-icons` 和 `license: MIT`。
- exact package [README](https://app.unpkg.com/@lobehub/icons-static-svg@1.94.0/files/README.md) 写明该项目使用 MIT；上游 [LICENSE](https://raw.githubusercontent.com/lobehub/lobe-icons/master/LICENSE) 包含 LobeHub 版权声明、MIT 授权条款和保留许可文本的要求。
- 仓库的 [third-party notices](../Resources/Legal/THIRD_PARTY_NOTICES.txt) 已记录包版本、上游来源、MIT attribution，以及 provider 名称和 logo 仅用于识别、无关联或背书的说明。

## 题包精确检索

以下是 2026-08-06 对每个 enabled 题目核心句执行的完整引号查询。结论限定为“在本次公开索引和日期内未发现明显公开命中”，不延伸为原创证明。

| 题目 | 完整精确查询 | 受限结果 |
| --- | --- | --- |
| `01_session_bundle_repair` | `"You are designing a compact black-box regression suite for a session-bundle system."` | 未发现明显公开命中 |
| `02_code_counterexample_maxgap` | `"You are constructing counterexamples for a retry planner named plan_retries."` | 未发现明显公开命中 |
| `03_ci_optimality_certificate` | `"You are auditing a CI planner by constructing compact scenarios that expose incorrect audit implementations."` | 未发现明显公开命中 |
| `04_transaction_regression_design` | `"You are designing compact regression scenarios for a function named replay_frames."` | 未发现明显公开命中 |
| `05_unified_diff_patch_applicator` | `"You are designing regression tests for a function named run_scan."` | 未发现明显公开命中 |

## Owner attestation（2026-08-06）

项目 owner 于 2026-08-06 书面确认：

- Q1–Q5 题包及对应答案 fixture 均为用户原创内容。
- ModelDial 图标、wordmark 和截图均为项目自有视觉资产。

这两项是 owner 对内容与资产权属的书面声明，不是本记录对原创性或商标权的独立法律意见。LiteLLM pricing 与 provider logo 仍按下列公开来源和独立边界继续核查，不纳入上述原创声明。

## 核对结论与持续边界

- LiteLLM pricing：当前 snapshot 记录固定 commit、raw SHA-256、抓取时间、匹配 provenance 和 snapshot content hash；来源身份变化会生成新的 snapshot ID，即使价格数值未变。NOTICE 已满足已核对 MIT 文本的保留要求。该结论不扩展为模型提供商价格、商标或服务条款的额外授权，价格继续只作为参考估算。
- Provider logos：当前公开资产精确来自 `@lobehub/icons-static-svg@1.94.0`，已保留包版本、来源、LobeHub 版权和完整 MIT 文本，并明确仅用于识别及无关联／不背书。各 provider 的商标政策不是 LobeHub MIT 许可的一部分，仍应在正式二进制发行前按实际使用逐项复核。

本记录确认的是上游文件位置、许可证文本与仓库 attribution 状态，不构成法律意见，也不替代 provider 商标政策或服务条款判断。
