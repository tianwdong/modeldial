# Frontend V17 Visual Semantic V3 Spec

状态：隔离评分候选。不得替换正式 Frontend V17、balanced v1、visual relational v2、榜单或生产数据。

候选身份：`case_stream_explorer_v17_visual_semantic_v3`。

源题身份：`frontend-case-stream-explorer-v17@v2`。逐字复用源 prompt、starter、浏览器行为证据、
workflow 证据和七张最终状态截图；不重新请求模型，不改写历史 HTML。

## 1. 修正范围

v3 只修正 visual v2 审计中确认的两类实现无关性缺陷：

1. `saving` 不能继续按变色面积近似信息清晰度。克制的行内文字、状态点或局部描边，只要达到手写
   正例的可见信号强度，应和大面积换色一样取得完整状态分；
2. `structure` 只比较大尺度页面／面板关系，不比较 inspector 内部文案、字段数量或卡片内容。增加
   有用详情不得因为更不像唯一 reference 而扣分。

K3、Fable、Gemini、Sol、Luna、Opus 的模型截图仍是冻结后的留出集，不得用于锚点、阈值或权重。
本候选不以任何指定模型必须第一为通过条件；若冻结后排序仍不合理，只能淘汰 v3 或另建候选。

## 2. 总分与正式展示

总分保持 100：behavior `33`、workflow `22`、visual `45`。behavior／workflow 的原子证据和映射逐字
复用 balanced v1；任何 visual 或 workflow 缌失只损失对应分值，不触发总分归零。

内部 `raw_score`、`display_score` 和各原子保留 6 位小数，排序使用未取整的 `display_score`。正式
展示分 `official_score` 只在总分最后一步执行 decimal half-up 四舍五入为整数；不得先对子项取整。

## 3. Visual 45 分

visual 原子与权重保持 v2 不变：状态辨识 `15`、响应式构图 `12`、可读层级 `10`、大尺度结构 `8`。

### 3.1 状态辨识：15

同一候选的 default 与 saving／failure／desktop inspector／mobile inspector 截图分别比较，分值仍为
`4／4／3／4`。使用冻结 crop 内 `8×8` block 的 RGB 变化与边缘变化；每项经正例下界和定向 mutant
上界线性归一，达到最弱正确正例后饱和为满分。saving 额外把 crop 切成 `10×5` blocks 的局部区域，
以 `0.75 × 全局变化 + 0.25 × 局部变化 P90` 计量，防止清晰的小状态徽标被大面积背景变化淹没。

校准集新增 `semantic_cues_cards` 正例：saving 只使用局部描边、明确行内文字和状态点，不用大面积
换色。这一正例必须获得 saving 满分，从而把“信号可识别”与“变色面积更大”分开。

failure 仍要求最终截图出现独立失败反馈。`failure_hidden` mutant 必须用 `display:none` 真正移除 alert
及其布局占位，避免隐藏元素推挤内容形成伪状态变化。大面积、清晰的失败通知仍可获得真实优势。

### 3.2 响应式构图与可读层级：22

逐字复用 v2 的原子、crop、阈值和公式：desktop／tablet／mobile／reflow 为 `4／3／3／2`；contrast／
occupancy／type scale 为 `4／3／3`。本轮不因模型排序修改这些已通过定向 mutant 的规则。

### 3.3 大尺度结构：8

七个状态仍为 `2／1／1／1／1／1／1`，但结构图先按 `32×32` 像素聚合，移除文字和小控件级噪声；
每个状态取候选与所有正确正例的最大值：

`structure = 0.45 × coarse_grayscale_ssim + 0.55 × coarse_edge_f1`

edge threshold 为 `3`，容差为 `1` 个 coarse block，颜色距离不计分。该指标只保留页面占用、主面板
边界、分栏、sheet 和大块表面关系；不因字段更多、字段顺序不同或 inspector 内部卡片更丰富扣分。

`semantic_cues_cards` 还必须向 inspector 增加正确且有用的 detail cards，作为“内容更丰富仍是正例”
门禁。`scrambled_structure` 继续作为七个结构原子的定向反例。

## 4. 校准与冻结

正确正例为 reference、compact-density、soft-surface、wide-inspector、semantic-cues-cards 五个手写实现
族；定向 mutants 覆盖全部 18 个视觉原子。所有材料都由模型无关的 reference 源码、手写 CSS／DOM
增强和手写破坏生成，不包含待排名模型输出。

正式复评前必须满足：

- 五个正例均为 `45／45`；reference raw 为 `100`；starter 可独立评分；
- 每个 mutant 的目标原子为 `0`，且所有锚点 margin 至少 `0.005`；
- scorer、spec、fixture、合同、源题和正例 reference 全部进入资产锁；
- 同一保存证据重复三次结果 JSON 字节一致；
- 合同哈希冻结后才可读取留出模型结果，且禁止留出后回写阈值。

展示映射继续使用本候选校准得到的 starter raw → `20`、reference raw → `100` 以及指数 `0.5` 的
单调平方根曲线。

## 5. 推广边界

本轮只新增隔离 spec、scorer、校准资产、回归和历史截图复评报告。不更新 catalog、正式题包、App、
Cloudflare、榜单或生产数据，不调用任何模型。
