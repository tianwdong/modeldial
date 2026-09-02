# Frontend V17 Balanced Progress V1 Spec

状态：冻结候选，尚未替换正式 Frontend V17。

## 1. 目标与边界

本候选只把已经完成校准的 `frontend-case-stream-explorer-v17@v2` 浏览器证据重新聚合为更可读、
更有区分度的原始分和展示分。它不修改原题面、starter、浏览器检查、trace、截图、render environment
或既有模型答案，也不调用 LLM／VLM 仲裁。

已有 K3、Gemini、Sol、Luna 六份完整答案是冻结校准集，不重新请求模型。已有 Opus 5 完整答案保留为
第一次留出失败样本；它是有效的实现失败，不是传输失败，不得因相对名次不符合预期而删除或改分。

候选身份：`case_stream_explorer_v17_balanced_progress_v1`。

## 2. 输入身份

- source benchmark：`frontend-case-stream-explorer-v17@v2`；
- prompt：逐字复用 `questions/frontend/case_stream_explorer_v17_v2/prompt.md`；
- starter：逐字复用 `questions/frontend/case_stream_explorer_v17_v2/starter.html`；
- 输入仍为纯文本单消息，不公开 reference screenshot，不提供 workflow certificate；
- 只消费 source scorer 已保存的 `dimensions` 和 `score_details`，不重新解释候选 DOM。

任何 prompt、starter 或 source score evidence 漂移都创建新候选，不改写本候选。

## 3. 原始分合同

总分为 `100`，保留所有完整 source evidence 的部分得分，不使用“任一 workflow 失败则总分归零”的
all-or-nothing gate。传输未完成、HTML 不完整或三次 source browser regrade 不稳定仍保持 `score=null`。

### 3.1 Behavior：33 分

| source dimension | 新分值 |
| --- | ---: |
| `query_state` | 3 |
| `virtual_keyboard` | 3 |
| `selection_async` | 22 |
| `responsive_layout` | 2 |
| `state_distinction` | 1 |
| `stability` | 2 |

每项按 `source_points / source_max_points` 等比例换算。

### 3.2 Workflow：22 分

| source dimension | 新分值 |
| --- | ---: |
| `browse_continuity` | 10 |
| `transaction_correctness` | 10 |
| `responsive_inspector` | 2 |

每项继续只保留 source scorer 已经取得的完整 workflow 分，不恢复 synthetic credit。

### 3.3 Visual：45 分

七个 source screenshot check `V03～V09` 各自保留原最大分比例，但把线性进度 `p` 改为 `p²`：

```text
p = source_points / source_max_points
state_raw = 3 × source_max_points × p²
visual_raw = sum(state_raw)
```

因此 starter visual 为 `0／45`，reference visual 为 `45／45`。平方只抑制中间相似度，不改变每个状态
的 starter／reference 锚点，不根据模型身份调整。

原始总分四舍五入到六位小数：

```text
raw_score = behavior_raw + workflow_raw + visual_raw
```

## 4. 展示分合同

冻结锚点：

- starter raw：`14.565476190476192`，display：`20`；
- reference raw：`100`，display：`100`。

公开单调映射为：

```text
raw <= starter:
display = 20 × raw / starter

raw > starter:
display = 20 + 80 × sqrt((raw - starter) / (100 - starter))
```

展示分四舍五入到六位小数。原始分和展示分必须同时保存；展示分不得覆盖原始证据分，也不改变排序。

## 5. 冻结校准集

不重新调用下列模型，只读取已经保存且哈希匹配的 score evidence：

| 样本 | raw | display（3 位展示） |
| --- | ---: | ---: |
| K3 high | `35.211317` | `59.327` |
| Sol high sample 1 | `31.482854` | `55.599` |
| Sol high sample 2 | `27.228591` | `50.799` |
| Sol high 中位数 | `29.355723` | `53.199` |
| Gemini high | `28.710296` | `52.552` |
| Luna high sample 1 | `20.121799` | `40.402` |
| Luna high sample 2 | `27.078624` | `50.617` |
| Luna high 中位数 | `23.600212` | `45.509` |
| starter | `14.565476` | `20.000` |
| reference | `100` | `100.000` |

校准通过条件：上述 raw 六位数逐项一致、starter／reference 锚点一致、display 映射严格单调、输入身份
哈希一致。这里的“中位数”对两个样本等于两者算术平均；展示中位数必须由各样本 display 求中位数，
不得先对 raw 求中位数后再映射。

## 6. Opus 5 留出补测

已有有效样本：原生 Anthropic Messages、`claude-opus-5`、`high`、纯文本、`128000` 输出上限、
`1200s` 墙钟上限。该样本完整 `end_turn`，但 `#empty-state[hidden]` 覆盖页面，按本合同为
raw `5.325397`、display `7.312356`，保留为 `implementation_failure` 证据。

冻结后只补一份 Opus：

1. 继续使用完全相同的 source prompt、Anthropic Messages、`high`、`128000` 和 `1200s`；
2. transport、目标模型身份、`end_turn`、`message_stop`、完整 HTML 与三次稳定 browser regrade 全部通过后才计分；
3. 若新样本与旧样本的 display 绝对差 `< 10`，停止补测并同时报告两份；
4. 若绝对差 `>= 10`，再补第三份，并以三份 display 的中位数描述 Opus；
5. transport／协议失败不计为 Opus 能力分，可在同一冻结请求合同下重试一次；
6. 无论新结果高低，都不得修改本候选的权重、`p²`、starter 锚点或展示曲线。

补测只验证 Opus 方差与实现稳定性，不重新请求 K3、Gemini、Sol、Luna。

## 7. 推广边界

本候选在本轮保持隔离，不替换正式 Frontend V17、不写入榜单、不更新 App／Cloudflare／生产数据。
正式推广需要另行批准，并重新执行公共核心锁、Container 和发布链验收。
