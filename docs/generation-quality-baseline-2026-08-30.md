# 生图质量与速度零成本基线（2026-08-30）

## 本检查点关闭的缺口

- 生图 Prompt 明确冻结为 `prompt_v1`，每个阶段同时保存知识注入前的 `base_prompt`、最终 `compiled_prompt`、负向词以及四组 SHA-256；知识证据使用规范化 JSON 指纹，历史任务不会因知识库后来变化而漂移。
- 实际发送给供应商的参考图记录编码方式、JPEG 质量、Alpha 处理、尺寸、字节数和 SHA-256。该指纹对应真实请求字节，不把素材路径或图像内容写入报告。
- VLM、参考图编码、供应商提交、轮询、下载、解码、本地增强、抠图、暂存、正式发布和端到端完成分别记录耗时；失败 trace 记录失败阶段和错误类型。
- 当前 LK `/v1/media/generate` 适配器没有返回逐调用费用。trace 明确写入 `billing.status=unavailable`，不再用猜测价格冒充实际成本。模型能力合同只声明当前代码和测试已经核对的请求形状；未知模型标记为 `compatibility-only`。
- 新增只读导出器 `tools/export_generation_baseline.py`。默认对 job ID 做 SHA-256 截断，不导出 Prompt、用户输入、图片或 API Key；导出的 `workflow.complete` 才是端到端时间，各阶段相加只作诊断，不能与端到端时间重复计算。

## 当前历史账本事实

在不打开图片、不读取 Prompt、不调用外部接口的前提下，对本机现有账本做了一次隐私化只读盘点：

- 10 个历史任务；
- 7 条可能计费的 VLM/生图 trace，但 0 条带实际价格；
- 2 条已提交结果评价，其中采用 1、拒绝 1；
- 0 个任务带新 `workflow.complete` 计时，0 个带 `prompt_v1` 快照。

因此旧数据只能证明“发生过调用和评价”，不能反推可靠的 P50/P95、单张费用或每张可采用成品成本。新版 trace 只从后续任务开始积累，绝不伪造旧任务数据。本地盘点 JSON 位于忽略的 `build/` 目录，不进入 Git。

## 固定输入与盲评合同

`tests/fixtures/generation_quality/manifest.json` 冻结 9 个输入：3 张已有来源/许可收据的真实照片和 6 张确定性程序化 PNG。覆盖食品、复杂纹理、包装文字、多产品数量、品牌色、透明/反光材质、器皿保留、截断补全和复杂阴影。

盲评固定 10 个 0–5 分轴：主体保真、结构/数量、包装文字、品牌色、构图、材质、光影、白底洁净、边缘和商业可用性。至少两轮复评，随机隐藏方案名称，并把“看起来更好”与“可直接交付”分开记录。程序化文件可用 `python tools/render_generation_quality_fixtures.py` 验证，任何字节漂移都会失败。

这些输入只证明评估合同可复现，不证明任何云端模型质量已经达标。

## 默认关闭的 Prompt v2 与实验编排（2026-08-31）

- `prompt_v1` 继续逐字节返回原有 Prompt，仍是所有正常任务的默认值；不会因为本轮升级静默改变已有出图行为。
- `prompt_v2` 把旧模板编排为“任务目标、不可破坏项、允许修改项、场景/光线/构图、输出约束、基线细节”六段自然语言任务说明。它只是假设，不代表已经优于 v1。
- `PRODUCT_ATELIER_ENABLE_PROMPT_V2` 未显式启用时，带 `prompt_v2` 的任务会在入队和供应商调用前被拒绝。启用后，Prompt 版本会冻结到任务、generation 和 trace；模板、编译器输出、知识注入后的最终 Prompt 分开保存，可按任务复现和回退。
- `tests/fixtures/generation_quality/experiment-template.json` 默认 `paid_execution_authorized=false` 且付费调用预算为 0。实验校验器强制单变量、唯一方案、调用上限、失败/废片停止条件；若声明金额上限但拿不到实际累计账单，会拒绝继续扩量。
- `tools/build_generation_blind_review.py` 只生成随机化的评审包与独立私有映射。评审包隐藏 Prompt/方案身份，私有映射带种子和映射 SHA-256；默认拒绝覆盖旧证据，也不会调用任何供应商。

trace 合同因此升级为 `generation-baseline-2026-08-31.1`。截至本节写入时仍未进行付费 A/B，不能宣称质量、速度或成本改善。

## Prompt v3 与单次生成零付费候选（2026-08-31）

后续只读审计确认，当前已记录 Prompt 阶段的正向文本为 655–799 个字符、负向文本为 303–371 个字符，并且普通单产品/多文件会无条件执行两次完整云端生成。这里的核心风险不是“超过上下文”，而是硬约束被重复软要求稀释，以及第二次完整生成再次解释正确主体。旧 `prompt_v2` 会继续携带原模板，因此不再作为“缩短 Prompt”的主候选。

- `prompt_v3` 不携带旧词堆，按模型档案生成紧凑 Render Plan；原模板仍单独冻结到 trace，确保对照可复现。
- 知识注入限制为最多 5 个意图锁、3 条正向知识、1 条负向知识，并有字符预算、冲突裁剪和 ignored evidence。
- 包装文字与 Logo 被锁定时，负向词明确禁止“文字数字或标志错误”，不再同时发送笼统“文字、logo”。
- `single_pass` 与 Prompt 版本分离；v1/v3、单/双阶段可以分别做单变量实验。
- `PRODUCT_ATELIER_ENABLE_PROMPT_V3` 和 `PRODUCT_ATELIER_ENABLE_SINGLE_PASS` 默认关闭；关闭时当前 v1/双阶段行为不变，启用状态冻结到任务参数和 trace。
- `experiment-template.json` 现在比较 v1/v3且固定双阶段；新增 `experiment-single-pass-template.json` 比较单/双阶段且固定 v1。两个模板均未授权、调用预算为 0。

trace 合同由此升级为 `generation-baseline-2026-08-31.2`。这一节只代表源码候选和离线可验证合同，不代表真实出图更好，也没有提升当前正式便携版。

离线回归结果为 Python 共运行 240 项（239 通过、1 项平台预期跳过）、前端 115/115、Vite production build 与 Rust/Tauri locked check 全绿。门禁关闭的候选在 provider 调用前失败；测试门禁开启的单次策略只产生 1 次图片调用，并把跳过精修及零适用账单写入 trace。整个验证过程未连接供应商。

## 本地质量检查 trace（2026-08-31）

新增 `generation-quality-gate-2026-08-31.1` 本地合同。它只使用生成结果像素和冻结输出规格，检查比例、最低像素、白底边界、画布碰边/疑似裁切和空白漏生成；主生成与最终生成分别保存报告，因此可以在真实 A/B 中观察第二阶段是否改善确定性缺陷。

该检查器不判断美学，不声称能识别包装文字、Logo、数量、参考图保真或真正的边缘质量；这些字段进入 `unverified_axes`。报告固定 `retry.authorized=false`，质量失败不会中止现有任务，也不会自行调用供应商。程序化用例与生成 API trace 集成测试通过后，Python 共运行 245 项（244 通过、1 项平台预期跳过）。本节仍是零付费源码证据。

## 尚未执行的付费门禁

本检查点没有调用付费 VLM 或生图接口。下一步只有在用户确认预算后才执行小批 A/B：先冻结模型快照、调用数、最高费用和停止条件，再比较 `prompt_v1` 基线与默认关闭的候选策略。供应商实际账单仍需以 LK 返回或账单回执为准；在取得真实费用前，“每张可采用成品成本”保持未知，不能套用 OpenAI 官方直连价格。

## Windows 正式便携门禁

源码提交 `1b5947502af00ac57f273510bf4a26a72d7c577a` 已以 sidecar contract `2026-08-31.1` 进入正式便携目录。Python 231 项通过（另 1 项平台预期跳过）、前端 101/101、Vite、Rust/Tauri、PyInstaller 候选和正式应用双 smoke 均通过；事务 `b3c0c1da03bc4f4faf029ecaf844508a` 已 finalize，上一 `.5` 正式目录完整备份位于 `D:\ProductAtelier-Backups\release-before-20260831-001837-1b5947502af0`。

正式 EXE、sidecar、manifest 与目录 tree SHA-256 分别为 `A2736D7FC053C5EAB20E04214499586C24A5E4458F60ADEFCD11DD0E8E30A5DC`、`26CF20FF286EFEE74096BF18C8FF89A9904FF2FB369B2D099C5AA7F80DA53381`、`9C163ADD7C8A87657E6ADB538E3EC9F3FE455AC58B595953D1715833678D5223` 和 `CF07D4317D6FEEC5EE936C851ABA0F5F5BE54A45F4405BC7E3A480CFAF8BEDBA`；source fingerprint 为 `AA08DDF0762CF3AF71F8AE687DAF34AC4133C1AC8D44CCD679B8EF28BFCFF34C`。promotion evidence SHA-256 为 `BCF7D27AB8A3D64B5E776BB6FBC5929E69AC5AD4080CCC83FEDBE4D647EA9F7F`。正式应用和单一 sidecar 正从桌面正式入口运行，动态端口 `64731` 的健康接口返回 `ok`、manifest `ok`、schema v3；桌面快捷方式目标和工作目录均为正式便携目录。最终截图 SHA-256 为 `24580B7F510569D28794C26C3459243A2C82F2D965AFD84A97DDD686CD631510`。

本轮未重建 NSIS；现存未签名安装候选仍属于 `.4`。不要把它描述为 `2026-08-31.1` 安装包或公开分发包。
