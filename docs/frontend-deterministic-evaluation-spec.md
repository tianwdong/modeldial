# Frontend Deterministic Evaluation Spec

状态：v20 仅保留为校准与失败证据；该题型已被否决，不再作为下一版实现基础
更新：2026-08-30

> 2026-08-30 决策：v20 的单一综合题同时出现真实样本分数过度集中和跨协议长输出无法完成，问题属于题型结构而非局部权重。下一版改走候选题型搜索协议，见 `docs/frontend-vnext-task-search-spec.md`。本文件以下内容只记录 v20 当时冻结的合同与结果，不代表继续实施方向。

## 1. 目标

用一个完整产品题同时评估模型的前端实现能力，并以可重复、可解释、与具体 DOM／CSS 实现无关的规则生成唯一分数。

本规范解决两类问题：

- 不用截图相似度或主观审美模型决定正式得分；
- 不把得分绑定到参考实现的 class、id、DOM 层级、固定文案、精确坐标或某一种布局写法。

截图只用于失败诊断和人工复核，不进入正式计分。

## 2. 单题与四个独立维度

题目必须是一个可运行的完整工作区，而不是多个互不相关的小组件。四个维度独立计分，固定总分 100：

| 维度 | 分值 | 主要证据 |
| --- | ---: | --- |
| 产品与交互 | 30 | 状态迁移、过滤排序、选择、键盘、异步保存、错误恢复 |
| 语义与可访问性 | 20 | landmark、名称、ARIA 状态、焦点顺序、live／alert |
| 响应式与内容韧性 | 25 | 多视口几何、内部滚动、移动形态、长内容与缺失字段 |
| 设计系统一致性 | 25 | 字号层级、色彩／对比度、间距族、组件族、状态可辨识度 |

任何维度没有证据时不按 0 分补齐；该检查失败，其他已获得分数保留。应用壳或注入数据完全不可运行时，输出无效并以 0 分参与排名，同时保留诊断。

## 3. 唯一测量合同

评测器只允许依赖以下稳定接口：

1. 题面公开的 `window.MODELDIAL_CASES` 和 `window.MODELDIAL_SAVE_CASE`；
2. HTML 标准语义、ARIA 属性、可见文本的非精确语义用途；
3. `data-md-role`、`data-md-action`、`data-md-id` 三个测量属性；
4. 浏览器实际行为、焦点、DOM 几何和 `getComputedStyle`。

不得依赖：

- CSS class 或普通 element id；
- 某个元素必须是第几个子节点或必须嵌套在特定容器；
- 精确 `aria-label`、固定按钮文案或固定语言；
- 参考实现的颜色值、字号值、间距值、圆角值或坐标；
- 截图像素相似度、OCR、视觉模型评分；
- 候选自行上报的分数、状态或隐藏 JSON。

### 3.1 `data-md-role`

静态角色：

- `app`、`page-heading`、`section-heading`、`summary`、`metric`、`metric-label`、`metric-value`；
- `query`、`search`、`sort`、`facet-group`、`facet`；
- `bulk-actions`、`selection-count`；
- `results-panel`、`results`、`results-empty`、`row`、`row-title`、`row-meta`、`row-priority`、`row-status`；
- `inspector`、`inspector-heading`、`inspector-body`；
- `live-status`、`alert`。

### 3.2 `data-md-action`

- `set-facet`；
- `select`、`open`、`close-inspector`；
- `bulk-investigating`、`bulk-resolved`。

### 3.3 `data-md-id`

- metric：`total`、`matching`、`selected`；
- facet group：`team`、`status`；
- facet：`team:<value>` 或 `status:<value>`；
- row 及其行内角色／动作：对应 case id；
- inspector：当前打开的 case id；没有打开对象时为空字符串。

这些属性是测试探针，不规定标签类型、视觉结构或组件实现。模型可以更换 class、普通 id、文案、DOM 包装、CSS 技术和视觉风格，但不得删除、复用或伪造探针含义。

## 4. 冻结输入矩阵

正式评分至少覆盖以下组合：

### 4.1 视口

- Desktop：`1440 × 900`；
- Tablet：`768 × 1024`；
- Mobile：`390 × 844`。

### 4.2 状态

- 初始状态；
- 搜索命中、搜索为空和清空恢复；
- team／status facet 与四种排序；
- 行 active、selected、inspector opened／closed；
- bulk saving、partial success、single failure restored；
- reduced motion。

### 4.3 数据种子

- normal：常规英文数据，包含确定性的排序和失败样例；
- multilingual-long：中英文、重音字符、长标题、长 owner／tag；
- dense-missing：更高数据密度，以及空 summary、空 tags、超长 metadata。

种子由宿主注入，候选不能读取种子名称来分支实现。每次评分使用同一冻结内容和顺序。

### 4.4 运行时身份

同一评分合同的所有候选必须使用同一运行时配置：

- 同一 Chromium 可执行文件和版本；
- `deviceScaleFactor = 1`；
- `locale = en-US`、`timezoneId = UTC`；
- 同一操作系统字体环境，且题面禁止远程字体；
- 固定视口，不允许页面自行改变缩放；
- 普通动效和 `prefers-reduced-motion: reduce` 分开执行；
- 每次报告记录浏览器版本、平台、评分器 Hash、合同 Hash 和候选 HTML Hash。

Pilot 可以使用本机已冻结的 Playwright Chromium。进入正式评测前，必须改为内容寻址的 Container 镜像和固定字体包；不同浏览器版本或字体环境的结果不能混在同一榜单批次中。

### 4.5 取值与容差

- 几何值统一使用浏览器返回的 CSS pixel 浮点数；边界容差为 `±1 CSS px`；
- 颜色按实际合成后的 sRGB 计算，正式比较前不做显示层截图采样；
- 比例先使用原始值判断，报告中最多保留四位小数；
- 缺失节点、非有限数值、不可见或不可操作证据统一按该规则失败，不猜测替代值；
- 阈值属于合同版本，不随当批模型分布、排名百分位或预期名次动态变化。

## 5. 确定性规则

每条规则必须包含：固定输入、适用条件、浏览器操作、期望结果、分值和诊断证据。通过／失败只能由确定性布尔条件生成。

`score-contract.json` 冻结分值与规则身份，`rulebook.json` 冻结每条规则的输入、操作、期望、证据和校准反例。浏览器评分器只能返回规则事实，不能自行改变分值、跳过规则或补发额外分数。

允许使用相对关系和容差，不允许使用参考实现的绝对值。例如：

- 字号：比较角色之间的层级、重复角色的一致性和合理范围；
- 间距：比较同族组件的离散度、全局 token 数量和异常值；
- 色彩：计算真实前景／背景对比度和状态之间可观察通道差异；
- 布局：判断区域可见、无重叠、无页面横向溢出、内部滚动有效；
- 交互：以操作后的数据、ARIA、焦点、API 调用和错误恢复结果为准。

单一风格不构成高分。一个视觉上漂亮但功能错误的实现不能借设计分覆盖交互失败；一个功能正确但层级、对比度和一致性差的实现也不能获得设计分。

## 6. 校准门槛

评分器进入模型横向测试前必须同时满足：

1. 至少 4 个合格正例全部通过，其中至少 2 个改变文案、DOM 包装或语义标签，而不只是换色；
2. 每个计分规则都有明确的负例或由更小原子规则覆盖；
3. 定向变异全部命中预期规则，mutation kill rate 为 100%；
4. 同一 HTML 连续评分至少 2 次，逐规则结果完全一致；
5. 静态检查确认评分器没有 class、普通 id、精确可见文案或参考坐标绑定；
6. reference 为 100 分，starter 必须明显低于 reference；
7. 匿名真实模型输出用于发现合同缺陷，但既有模型名次不是评分器验收条件。

静态实现无关性门禁至少拒绝：

- `getElementById`、`getElementsByClassName`；
- 以普通 `#id` 或 `.class` 开头的 locator／query selector；
- `data-md-role`、`data-md-action`、`data-md-id` 以外的自定义 `data-*` 依赖；
- starter／reference 的普通 id、class、精确文案或绝对坐标常量。

校准报告只有在正例全过、定向变异全部命中、规则覆盖完整、重复评分一致、reference 为 100、starter 明显失分、静态门禁通过时才能标记为 `qualified`。

如果异构正例失败，优先修复评分器绑定；如果缺陷变异存活，优先补足可观测证据。不得为了符合预期名次调整模型专属阈值。

## 7. v20 Pilot 交付边界

v20 只形成下一版题目与评分候选：

- 独立 prompt、starter、reference 和 score contract；
- 只使用测量合同的 browser scorer；
- 多视口、多状态、多数据种子；
- 异构正例与定向缺陷校准；
- 可重复的本地验证脚本和测试。

v20 不自动替换生产 Frontend 题，不写入榜单，也不部署 Cloudflare。题包与评分器本身不持有模型凭据；只有通过本地校准门槛并获得单独授权后，才允许由隔离运行过程发起真实模型请求。是否进入生产仍需单独决定。

## 8. 本地校准结果

最终 v20 scorer Hash 为 `5d5b1694b9ca39ee8b2a5245904b535c4d92065a86515628562f9dfb3e18df55`。在 Chromium `151.0.7922.175`、`MacIntel`、`deviceScaleFactor = 1`、`en-US`、`UTC` 环境中：

- reference：`100／100`；
- starter：诊断分 `14／100`，因核心壳不合格，排名分 `0`；
- 异构正例：`4／4` 全部 `100／100`；
- 定向变异：`21／21` 命中预期规则，mutation kill rate `100%`；
- reference 重放：`2／2` 逐规则结果一致；
- scorer 实现无关性静态审计：通过。

报告保存在 `artifacts/frontend-v20-calibration/latest/calibration-report.json`。这只证明本地评分合同已达到进入匿名模型横向校准的门槛，不代表 v20 已替换生产 Frontend v17。

## 9. 首轮真实模型横向校准

2026-08-29 使用同一 v20 prompt、同一 `high` effort、同一 Chat Completions SSE 协议，对 `gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-5.6-sol` 各生成一次候选 HTML。评分器只读取候选 HTML，不读取模型身份；每份候选连续评分两次，逐规则结果完全一致。

| 模型 | 总分 | 产品与交互 | 语义可访问性 | 响应式韧性 | 设计系统 | 生成耗时 | 总 Token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-5.6-sol` | `87` | `30／30` | `12／20` | `25／25` | `20／25` | `381.282s` | `27,101` |
| `gpt-5.6-luna` | `82` | `30／30` | `12／20` | `25／25` | `15／25` | `383.244s` | `27,210` |
| `gpt-5.6-terra` | `81` | `30／30` | `16／20` | `15／25` | `20／25` | `245.551s` | `19,572` |

三者均完整收到 `finish_reason=stop` 与 `[DONE]`，全部通过产品交互规则，且没有页面运行错误、控制台错误、外部请求或远程资源。共同缺陷是非 saving 行没有显式暴露 `aria-busy="false"`，因此 A13 失败。Sol 与 Luna 的 selected／saving 状态可见通道不足；Luna 还没有形成至少三类可区分的 priority 样式。Terra 的移动端 inspector 关闭按钮与多类主控件低于 `44px`，同时 workspace surface 区分不足。

完整证据保存在 `artifacts/frontend-v20-live/20260829-gpt-three-high-chat-completions-curl/comparison.json`。本轮只用于本地合同校准和模型横向比较，不写入生产榜单，也不替换 Frontend v17。

## 10. Claude Opus 5 Anthropic Messages 探针

随后使用同一 v20 prompt 对 `claude-opus-5` 做跨路由探针。Claude 请求走 Anthropic Messages SSE、adaptive thinking；它与前三个模型的 Chat Completions 路由不同，因此只有完整候选才能进入同一离线评分比较，协议启动和传输表现必须单独记录。

| Effort | 20 分钟结果 | SSE／正文证据 | 是否评分 |
| --- | --- | --- | --- |
| `xhigh` | `incomplete` | 持续返回思考流，未形成可保存 HTML | 否 |
| `high` | `incomplete` | `HTTP 200`；`93` 个 SSE 事件；约 `7,730` bytes 部分正文；无 `stop_reason`／`message_stop` | 否 |

最小真实请求已确认服务端接受 `claude-opus-5 + adaptive thinking + xhigh`，但这只证明请求字段可用，不证明综合前端任务可在运行时限内完成。完整 `high` 请求于 `1200.030s` 以 curl 超时码 `28` 结束；部分正文没有保存为候选 HTML，也没有交给评分器。两次完整请求均无有效分数，所以不能据此判断 Opus 5 是否高于 Sol 的 `87` 分，也不能用失败样本推动评分器改分。证据保存在 `artifacts/frontend-v20-live/20260829-claude-opus-5-xhigh-anthropic-messages/comparison.json` 与 `artifacts/frontend-v20-live/20260829-claude-opus-5-high-anthropic-messages/comparison.json`。
