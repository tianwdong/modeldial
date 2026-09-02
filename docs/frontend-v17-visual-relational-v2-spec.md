# Frontend V17 Visual Relational V2 Spec

状态：本轮隔离复评候选。不得替换正式 Frontend V17、现有 balanced v1、榜单或生产数据。

候选身份：`case_stream_explorer_v17_visual_relational_v2`。

源题身份：`frontend-case-stream-explorer-v17@v2`。逐字复用源 prompt、starter、浏览器行为证据、
trace 证据和七张最终状态截图；不重新请求模型，不改写历史 HTML。

## 1. 目标与非目标

本候选修复 balanced v1 的单一参考图过绑定：视觉质量必须由最终截图的状态表达、响应式构图、
可读层级和多参考结构关系共同决定，而不是由候选像素是否比 starter 更接近唯一 reference 决定。

视觉仲裁不调用 LLM／VLM，不读取模型身份，不使用候选自报分。浏览器只负责在冻结环境中产生七张
标准截图；质量判断只读取这些 PNG 像素。行为与 workflow 继续复用 balanced v1 已冻结的原子证据。

## 2. 总分合同

总分仍为 100：

- behavior：33；维度和映射逐字复用 balanced v1；
- workflow：22；维度和映射逐字复用 balanced v1；
- visual relational：45；不再使用 starter-anchored `progress²`。

任何 visual 或 workflow 缺失只损失对应分值，不触发总分归零。

## 3. Visual 45 分

### 3.1 状态辨识度：15

比较同一候选自己的状态截图，不比较唯一参考布局：

- default desktop → selected／saving：4；
- default desktop → failure：4；
- default desktop → desktop inspector：3；
- default mobile → mobile inspector：4。

每项使用冻结 crop 内的 `8×8` block RGB 距离和边缘变化，计算 meaningful-change ratio。仅整体换色、
全屏亮度漂移或截图噪声不得冒充局部状态表达。

### 3.2 响应式构图：12

- desktop split composition：4；
- tablet composition：3；
- mobile composition：3；
- desktop／tablet／mobile intentional-reflow relation：2。

从最终截图计算 desktop／tablet 工作区内部持续分栏线、mobile 表面在视口右侧的完整收口，以及三端
结构信号的几何均值。该维度判断“是否形成可用的分栏／重排／全屏 sheet”，不要求 reference 的
精确坐标。

### 3.3 可读性与层级：10

- luminance contrast and dark-direction separation：4；
- useful content occupancy／blank-area control：3；
- top-level type-scale hierarchy：3。

指标来自像素亮度分位数、主背景分离度、非背景 block 覆盖，以及顶部主标题与辅助文字的亮像素
行带尺度差。颜色序列化、DOM wrapper、字体抗锯齿小抖动不得改变结论。

### 3.4 多参考结构相似度：8

七个状态分别为 `2／1／1／1／1／1／1`。每个状态取候选与所有异构正例的最大结构相似度：

`structure = 0.35 × grayscale_ssim + 0.65 × edge_f1`

颜色距离只保留为诊断，不计该 8 分。正例集合至少包含 reference、compact-density、soft-surface、
wide-inspector 三种布局／视觉实现族；不得加入 K3、Gemini、Sol、Luna、Opus、Fable 或任何待排名
模型截图。

## 4. 归一化与曲线

每个原子指标只使用独立手写正例和定向 mutant 定标：

`u = clamp((metric - mutant_ceiling) / (positive_floor - mutant_ceiling), 0, 1)`

指标方向相反时等价反转。一个检查包含多个指标时取算术平均；不得用单项失败清空其它检查。
最终 points 为 `max_points × u`，使用线性曲线，不再平方。所有 `positive_floor`、
`mutant_ceiling`、crop、block size、颜色／边缘阈值和资产哈希必须在模型复评前写入合同并冻结。

## 5. 校准资产与门禁

正例由同一正确 reference HTML 通过手写 CSS 变体产生，必须保持功能、信息架构和七个状态可见：

1. compact-density：调整留白、字号层级和面板比例；
2. soft-surface：调整色板、边框、圆角和表面层级；
3. wide-inspector：调整桌面 inspector 比例及 tablet composition。

定向 mutants 至少覆盖 saving flatten、failure hidden、desktop inspector collapsed、mobile sheet
inset、desktop／tablet panel removed、mobile fixed-width overflow、low contrast、blank content、flat
hierarchy 和 scrambled structure。

正式复评前必须满足：

- reference 和三种异构正例 visual 均为 45；
- starter 可评分且不会因视觉门控归零；
- 每个 mutant 只需杀死其目标检查，但目标检查必须低于对应正例；
- 同一截图包重复三次逐项字节一致；
- scorer／合同／fixture／源题／正例源 HTML 全部进入资产锁。

## 6. 展示映射与模型留出

完成校准后，以新 scorer 下的 starter raw 映射为 display 20，reference raw 映射为 100；中间沿用
balanced v1 的单调平方根展示曲线。raw 和 display 同时保留。

阈值、权重和展示锚点冻结后，才离线复评已有 K3、Gemini、Sol、Luna、Opus、Fable 截图。模型
结果只能验收分布，不能回写公式；若分布不合理，淘汰整个 v2 或另建 candidate id，不能事后调参。

## 7. 推广边界

本轮只产生隔离 spec、scorer、校准报告和历史截图复评报告。不更新正式题包、catalog、App、
Cloudflare、榜单或生产数据，不调用任何模型。
