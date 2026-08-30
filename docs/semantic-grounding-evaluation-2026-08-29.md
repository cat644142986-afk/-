# Product Atelier 智能选物自动定位合同与评测

> 状态：R6 / Phase 7A-B 第八模型对照检查点。独立可选 Windows GPU 运行包与轻量打包 sidecar 已完成真实识图联调；Grounding DINO、OWLv2、Florence-2 和双模型共识均未同时通过召回与难负例门禁，正式便携版仍未提升。

## 1. 已落地的产品边界

1. “快速去背景”继续只分离全部前景，不读取目标名称。
2. “智能选物”可以请求本地自动定位候选，但候选只能预填编辑框，永远不能自动确认或直接入队。
3. 本地模型未配置、目录损坏、运行环境缺失、无匹配、低置信度或推理失败时，当前图片仍停留在确认弹窗并可继续手动框选。
4. 用户开始拖框、修改坐标、删除或清空候选后，晚到的模型结果不能覆盖用户修改。
5. 自动定位仅允许读取明确配置的本地模型目录；使用文件系统路径和 local_files_only=True，不会后台下载模型，也不会调用生图 API。
6. 主 PyInstaller sidecar 继续排除 torch / transformers / tokenizers；识别运行时与模型权重作为两个独立可选包，由版本、平台、完整文件清单、SHA-256 与健康探测绑定，因此不会把数 GB 依赖塞进主程序。
7. 当前 Grounding DINO 文本编码器不能可靠理解中文；中文字符会落为未知 token。适配器现会在未翻译中文上返回 `query_translation_required` 并保留手动框选，不再把显著物体框误报成中文语义理解。
8. 高频中文商品名先经过源码受控的离线词表解析；映射结果与用户可选英文覆盖词分开保存。未知中文不调用模型、不继承上一次英文词，直接进入可解释的手动恢复。
9. 置信度不低于 0.75 的框可以预填，但仍必须人工确认；0.60–0.75 的框只显示为橙色虚线“待确认建议”，默认选中数保持为 0，必须逐项点击“采用”才进入选区。低于 0.60 的弱候选继续隐藏，避免为了凑数量把错误对象交给用户。
10. 候选建议、已选区域和最终确认是三个独立状态。关闭弹窗、切换抠图方式或恢复已确认选区时不会把未采用建议重新当成已选目标。

## 2. 固定离线合同

源码清单：

- tests/fixtures/semantic_grounding/manifest.json
- python/semantic_grounding_eval.py
- tools/evaluate_semantic_grounding.py

8 个程序绘制用例覆盖：

- 食品
- 多个相似目标
- 包装与小字
- 透明容器
- 毛发/细线
- 阴影
- 遮挡
- 无匹配

该集合只验证候选框、数量、失败恢复、指标计算和跨电脑可复现性，不代表真实电商照片质量。

本轮另建 `tests/fixtures/semantic_grounding_photos/manifest.json`：3 张真实照片、4 个查询，文件均由 Wikimedia Commons 获取，逐项固定来源页、作者、许可、下载 URL、字节数、SHA-256、尺寸和人工框。内容覆盖单商品食品、透明水瓶、纹理目标与无匹配；其中 CC BY-SA 4.0 图片的署名保留在同目录 README 和清单中。它仍只是小样，不覆盖多商品、包装、遮挡、毛发或复杂背景，不得据此宣称生产质量。

第五检查点新增 `tests/fixtures/semantic_grounding_openimages/`：锁定 Open Images V7 validation 的 30 张真实照片、35 个名称查询、49 个存在目标框和 5 个官方人工负标签 no-match 查询。覆盖食品、多相似商品、包装、透明物体、毛发/细线代理、阴影、遮挡、复杂背景、小物体和难负例。图片像素不进入 Git，由 `tools/bootstrap_semantic_grounding_corpus.py` 按清单下载并逐项核验字节数、SHA-256 和尺寸；Git 只保存选择、官方标注、来源页、作者、许可元数据与可复现清单。

官方标注是目标框，不是 alpha 蒙版。因此该扩展集只能证明“按名称和数量定位候选”的质量，不能替代透明边缘、毛发或细线的抠图蒙版验收。Open Images 官方说明 validation/test 的已验证正类框为穷尽标注，负标签可作为可靠缺席类；同时官方仍提示逐张核验图片许可，清单已固定每张图的 CC BY 2.0 元数据和来源页。

## 3. 当前指标

评测器固定计算：

- IoU 0.5 下的目标召回率
- 精确率与误候选
- 目标数量完全正确率
- 无匹配正确率
- 失败后可恢复率
- 匹配框平均 IoU
- 平均耗时与 P95 耗时

程序合同门槛是召回率、精确率、数量正确率至少 0.75，无匹配正确率和恢复率必须为 1.0。它只用于阻止明显回归；真实照片正式门槛需在 Phase 7B 根据人工标注结果单独冻结。

验证合同：

~~~powershell
python tools\evaluate_semantic_grounding.py
~~~

运行已经放在本机的模型目录：

~~~powershell
$env:PRODUCT_ATELIER_GROUNDING_MODEL_PATH = "D:\ProductAtelier-Models\grounding-dino-tiny-a2bb814"
python tools\evaluate_semantic_grounding.py --run-local --query-field model_query_hint
python tools\evaluate_semantic_grounding.py --manifest tests\fixtures\semantic_grounding_photos\manifest.json --run-local --query-field model_query_hint
python tools\evaluate_semantic_grounding.py --manifest tests\fixtures\semantic_grounding_photos\manifest.json --run-local --query-field query --resolve-query
~~~

扩展门禁：

~~~powershell
python tools\bootstrap_semantic_grounding_corpus.py --download
python tools\bootstrap_semantic_grounding_corpus.py --verify
python tools\evaluate_semantic_grounding.py --manifest tests\fixtures\semantic_grounding_openimages\manifest.json --run-local --query-field query --resolve-query
~~~

为避免对 30 张图重复执行模型，可先用 `--confidence-threshold 0.40 --predictions-output <path>` 保存一次低阈值原始候选，再用 `tools/calibrate_semantic_grounding.py` 离线扫描多个阈值。本次 RTX 4060 原始候选已保存为 `docs/reports/semantic-grounding-openimages-zh-mapped-threshold-040-predictions-rtx4060-2026-08-29.json`，另一台电脑无需模型和图片即可重算阈值表。评测报告同时保留“可信自动预填”与“人工采用建议后”的指标，后者不能冒充自动门禁通过。

评测命令不会下载权重。未配置模型时会得到 unavailable 并以门禁失败退出，这是预期的诚实结果。

## 4. 外置模型与可复现下载

模型固定为 Hugging Face `IDEA-Research/grounding-dino-tiny` 提交 `a2bb814dd30d776dcf7e30523b00659f4f141c71`，Apache-2.0。源码清单 `docs/model-artifacts/grounding-dino-tiny.json` 固定 9 个文件的尺寸与 SHA-256，只允许 689,359,096 bytes 的 `model.safetensors`；692MB 的 pickle `pytorch_model.bin` 明确排除。

应用不会调用下载器。开发者必须显式执行：

~~~powershell
python -m pip install -r python\requirements-grounding.txt
python tools\bootstrap_semantic_grounding.py --inspect --destination "D:\ProductAtelier-Models\grounding-dino-tiny-a2bb814"
python tools\bootstrap_semantic_grounding.py --download --destination "D:\ProductAtelier-Models\grounding-dino-tiny-a2bb814"
python tools\bootstrap_semantic_grounding.py --verify --destination "D:\ProductAtelier-Models\grounding-dino-tiny-a2bb814"
~~~

`requirements-grounding.txt` 是明确隔离的可选运行时依赖，不被主 sidecar 构建或普通应用初始化引用。若目标机器要使用特定 CUDA/Metal 版本，应按 PyTorch 官方平台说明安装对应 wheel 后再安装其余依赖。目标目录必须在源码仓库外；下载后逐文件复算 SHA-256，并只在外置目录写本地 receipt。权重、缓存、receipt 均不进入 Git。应用不会自动下载或静默启用模型包。

## 5. 2026-08-29 RTX 4060 实测结论

环境：Windows 11、Python 3.12.10、RTX 4060 8GB、torch 2.6.0+cu124、transformers 5.15.0。没有付费 API 调用。

| 数据 | 查询 | 召回 | 精确率 | 数量正确率 | 无匹配正确率 | 首例冷启动 | 热平均 / P95 | 峰值显存 reserved | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 8 个程序合同 | 受控英文 | 100% | 90.91% | 87.5% | 0% | 15.74s | 316 / 324ms | 2250MB | 未过门禁 |
| 4 个真实照片查询 | 受控英文 | 100% | 75% | 75% | 0% | 14.75s | 310 / 339ms | 2506MB | 未过门禁 |
| 8 个程序合同 | 中文原词 | 0% | 0% | 12.5% | 0% | 无模型加载 | 约 1.4ms 回退 | 0MB | 明确拒绝未翻译中文 |
| 4 个真实照片查询 | 中文原词 | 0% | 0% | 25% | 0% | 无模型加载 | 约 7.6ms 回退 | 0MB | 明确拒绝未翻译中文 |
| 4 个真实照片查询 | 受控英文 + 0.75 安全门槛 | 100% | 100% | 100% | 100% | 14.88s | 321 / 330ms | 2506MB | 小样门禁通过 |
| 4 个真实照片查询 | 中文离线映射 + 0.75 安全门槛 | 100% | 100% | 100% | 100% | 13.84s | 306 / 310ms | 2506MB | 小样门禁通过 |
| 35 个 Open Images 查询 | 中文离线映射 + 0.75 可信门槛 | 46.94% | 100% | 60% | 100% | 67.58s | 587 / 755ms | 2980MB | 安全但漏检严重 |
| 35 个 Open Images 查询 | 中文离线映射 + 0.40 单阈值 | 91.84% | 86.54% | 80% | 0% | 27.30s | 612ms 热平均 | 2980MB | 召回高但 5/5 难负例误报 |
| 35 个 Open Images 查询 | 0.75 可信 + 0.60 人工建议 | 83.67%* | 93.18%* | 77.14%* | 40%* | 复用同一预测 | 复用同一预测 | 复用同一预测 | *人工建议辅助指标，不是自动通过 |

英文路径对存在目标的 3 个真实照片查询全部命中且平均匹配 IoU 0.979，但把咖啡粉照片误判为汉堡，置信度仍达到 0.6631；简单提高 0.4 阈值无法解决。中文在封锁前同一误候选置信度 0.5176、标签为 `[UNK] [UNK]`，证明“框中显著主体”不是理解中文。详细原始报告位于 `docs/reports/semantic-grounding-*-rtx4060-2026-08-29.json`。

第四检查点把自动预填门槛提高到 0.75；咖啡粉照片上的错误“汉堡”框（0.6631）改为 `low_confidence` 且不再进入候选，因此这次的“无匹配正确”是安全弃权，不代表模型已经具备可靠的不存在分类能力。中文“汉堡 / 透明水瓶 / 咖啡粉”通过源码词表映射后，在同一小样中得到与英文路径一致的结果。报告为 `docs/reports/semantic-grounding-photos-en-calibrated-rtx4060-2026-08-29.json` 和 `docs/reports/semantic-grounding-photos-zh-mapped-calibrated-rtx4060-2026-08-29.json`。

实际界面已在隔离数据目录完成：导入授权汉堡图 → 选择“智能选物” → 输入“汉堡”、数量 1 → 首次本地模型加载 → 显示 92% 自动候选 → 人工确认 → 主界面显示“已确认 1 个汉堡”。验证中发现并修复两项只有端到端操作才暴露的问题：普通请求上限会误杀冷启动，独立本地运行时现保留 180 秒预算；系统解析出的 `hamburger` 曾串成下一次用户覆盖词，现已分离解析词与用户覆盖词，未知“月球齿轮”会保持 0 个候选并解释手动恢复。

同一提交还重建了不含实验模型依赖的 PyInstaller sidecar 候选。打包 API 能从随包 JSON 读取离线词表并把“汉堡”解析为 `hamburger`；没有外置运行时时返回 `unavailable` 并保留手动框选。该结果只证明打包合同和回退可用，不代表正式便携版已经具备自动候选；正式目录与桌面入口没有提升。

因此当前模型只适合“离线中文映射后的候选框 + 强制人工确认”，不适合自动确认，也尚不适合进入正式包。3 张照片不足以证明生产质量，不能用本轮 100% 小样成绩代替扩展集门禁。

30 张扩展集进一步推翻了“只调一个阈值即可上线”的假设。0.75 虽然没有误候选，但漏掉 26/49 个目标；0.40 虽找回 45/49 个目标，却让 5/5 官方负标签图都出现候选。所有候选中最低真阳性为 0.43、最高假阳性为 0.74，分数区间明显重叠，没有单一阈值能同时满足召回、精确率、数量和 no-match 门禁。最高风险难负例包括“易拉罐图找瓶子”0.742、“面包图找蛋糕”0.708 和“手提包图找眼镜”0.669。

基于该证据，第五检查点采用保守两级策略：≥0.75 仍可预填，0.60–0.75 只给人工可选建议，<0.60 隐藏。该策略保持可信自动结果精确率与 no-match 安全率为 100%，同时给出 21 个可人工复核的建议；如果人工逐项采用正确建议，覆盖上限可提高到 83.67% 召回，但难负例仍会出现建议，因此不能自动采用。下一步若要减少人工负担，必须评估第二存在性验证器或更强定位模型，而不是继续降低同一模型阈值。

真实界面在隔离数据目录使用公开授权照片完成实测：中文“红酒杯”离线映射为 `wine glass`，72% 结果显示橙色待确认建议；采用前为 0/1 且确认按钮禁用，点击“采用”后才变为 1/1 并允许最终确认。确认后重开恢复已确认选区，切换“快速去背景”后弹窗关闭且建议 DOM 为 0；控制台无 error/warning。没有调用付费 API，也没有读取用户图片。

第五检查点完整源码门禁：Python 188 项（187 通过、1 个平台预期跳过）、前端 98/98、Vite production build、Rust/Tauri custom-protocol check、Python compileall、Git whitespace、30/30 许可照片下载锁验证和真实浏览器界面闭环通过。源码 sidecar 合同升级为 `2026-08-30.1`；正式 sidecar、Windows 正式目录、NSIS 与桌面快捷方式均未重建或提升。

## 6. 候选模型判断

第一适配器选择 Grounding DINO，是因为它的正式任务就是图像与文本输入的开放集目标检测，输出可供确认的目标框；Transformers 官方示例也提供 AutoProcessor + AutoModelForZeroShotObjectDetection 路线。官方实现采用 Apache-2.0 许可：

- [Hugging Face Grounding DINO 文档](https://huggingface.co/docs/transformers/model_doc/grounding-dino)
- [IDEA-Research GroundingDINO 官方仓库](https://github.com/IDEA-Research/GroundingDINO)

当前实测证明它对受控英文的存在目标定位可用，但原生中文与无匹配都不合格，不能据此宣称适合 Product Atelier。

后续对照结果：

- [Google OWLv2](https://huggingface.co/google/owlv2-base-patch16-ensemble)：Apache-2.0、约 0.2B 参数。固定版本已在同一冻结集完成 RTX 4060 对照；热平均 266ms、峰值 reserved 870MB，框和数量优于当前可信层，但单阈值最多只把 no-match 提高到 80%，安全阈值下召回又降至 48.98%，不进入产品。
- [Florence-2](https://huggingface.co/docs/transformers/v5.15.0/en/model_doc/florence2)：官方 Transformers 文档列出开放词汇检测任务。本轮采用 MIT 许可的 native base-ft 固定转换版，以原生类和 `trust_remote_code=False` 评测；热平均 147ms、峰值 reserved 660MB，但召回 75.51%、精度 84.09%、5/5 难负例均返回错误框，不进入产品。
- Grounding DINO + OWLv2 的位置共识扫描可以把 5/5 no-match 全部安全弃权，但此时召回最高只有 69.39%，仍低于 75% 检查点目标。不能用增加第二模型的体积换一个仍不合格的结果。
- [Meta SAM 2](https://github.com/facebookresearch/sam2)：Apache-2.0，可用于点选/框选后的蒙版修正；它不是文本定位器，不能替代 grounding。

三模型和双模型共识的统一结果见 `docs/reports/semantic-grounding-model-comparison-rtx4060-2026-08-30.json`。微软原始 Florence-2 检查点在禁止远程代码时因 tokenizer 缺少原生处理器所需 image token 被加载门禁拦下；随后只使用 Transformers 官方文档采用的 native 转换家族完成对照，没有为跑通模型降低安全要求。

## 7. 下一门禁

1. 已完成可审计查询映射、30 张真实照片/35 查询门禁和两级人工建议界面。后续扩展词表或数据集必须带固定用例，不能把联网翻译、自由生成或人工建议指标伪装成自动质量。
2. 独立可选模型运行时/模型包已经完成真实 Windows candidate：主 sidecar 保持轻量，运行时和权重有独立版本、平台、全文件清单、哈希、健康探测、缺失回退和设置页状态；完整哈希、真实推理和打包 sidecar 联调均已通过。
3. 第二存在性验证器与不同架构对照已经完成，三种模型和双模型共识均未通过。暂停继续堆叠相似模型；后续只有在扩大负例合同或出现有明确拒识机制的候选时才重开模型选型。
4. 当前唯一实施游标是为候选增加点选、增删目标和蒙版修正；先复用现有框约束分割链完成可恢复交互，再决定是否需要 SAM 类边缘修正。单图失败继续停留人工确认，不能让整批任务作废。
5. 只有可选运行时/模型包、扩展集可信门禁、正式 sidecar 实测和完整 Windows candidate-first 门禁全部通过，才允许提升新正式便携版。源码界面可用不等于正式便携版已经获得自动定位能力。

## 8. 2026-08-30 独立可选运行时源码合同

- 新增一目录 PyInstaller 识别 worker，主 sidecar 仅保存运行时管理器与模型合同。worker 只监听随机本机端口，使用进程内随机令牌认证，父进程退出时自停；图片只在本机进程间传递，不调用在线接口。
- 运行时清单锁定合同版本、Windows/处理器架构、入口文件、支持的模型 artifact、Git 提交、源码指纹以及全部文件的尺寸和 SHA-256。真正启动 worker 前会重新哈希完整运行时与模型；设置页的快速状态只用于提示，不能绕过执行门禁。
- 设置页新增“本地智能选物（可选）”：分别选择运行时和模型包、执行完整验证或关闭扩展。软件不自动下载；缺失、损坏、平台/版本不匹配时回退手动框选，关闭扩展会终止已启动的 worker。
- RTX 4060 源码协议实测通过：外置模型 receipt 快速检查、9 文件完整哈希、worker 健康握手和真实本地推理均成功；程序生成的简单瓶形图得到 0.716 `bottle` 候选。没有付费 API 调用，也没有读取用户图片。
- `tools/Build-GroundingRuntime.ps1` 只允许在 Windows 干净工作区构建全新候选目录；旧输出不覆盖，临时构建目录使用唯一事务名并限定在仓库 `build` 下清理。该源码检查点不等于运行时正式候选已经构建，也不更新桌面快捷方式。
- 源码门禁为 Python 197 项（196 通过、1 个平台预期跳过）、前端 100/100、Vite production build、Rust/Tauri custom-protocol check、Python compileall、PowerShell 解析与 Git whitespace。设置页在真实本地浏览器验证只有一层滚动，卡片与禁用态完整可见，控制台 0 error / 0 warning。

## 9. 2026-08-30 Windows 候选与打包联调

- 干净提交 `e62732caa8c7ce40e7164001ff4a393ac8479a0f` 构建出 `grounding-dino-transformers-windows-amd64-v1`：6042 个文件、4,026,031,474 bytes，入口 SHA-256 `55a4181afd5c76e45bfa148a543e008cf8f3d37d6df4b3fcd0102a659fdf9c7d`，清单 SHA-256 `92c83c587a186f8265b6985db7607d237e6d08f58e9192f5dee00e1c5152f014`。
- 严格构建探针真实导入 `torch / transformers / safetensors`。它先发现并拦截了缺失 `httpx` 的不可用包；修复后才发布候选目录。运行时和 9 文件模型完整哈希均通过，CUDA 设备为 RTX 4060。
- 打包 worker 对程序生成瓶形图返回 1 个 `bottle`，置信度 0.7161。轻量主 sidecar 合同 `2026-08-30.2` 通过健康 smoke，并从设置、完整验证、中文 `瓶子 → bottle`、素材导入到外置 worker 推理完成隔离联调；结果按规则进入 1 条待采用建议、默认选中 0 个，退出后 worker 残留为 0。
- 主 sidecar 仍只有 365,152,636 bytes，不包含可选 4GB GPU runtime 或 689MB 模型。全量回归为 Python 198 项（197 通过、1 个平台预期跳过）、前端 100/100、Vite build 与 Rust custom-protocol check 全绿。
- 该结果证明交付结构和真实调度可用，不改变模型质量结论：冻结扩展集上的可信层召回仍为 46.94%，低于 75% 门槛。正式便携目录、NSIS 与桌面快捷方式保持上一正式版本；下一步必须比较更强定位/存在性验证方案并补用户修正能力，不能把“候选能运行”写成“模型已达到生产质量”。

## 10. 2026-08-30 OWLv2 / Florence-2 独立对照

- OWLv2 固定 `cfd3195...`、619,918,824-byte safetensors，9/9 文件 SHA-256 通过；Florence-2 native base-ft 固定 `0b03b6f...`、463,178,864-byte safetensors，12/12 文件 SHA-256 通过。两者均为 Git 外评测包，不会被应用自动下载，也未加入主 sidecar 或可选正式 runtime 支持清单。
- OWLv2 在 0.10 线得到 85.71% 召回、85.71% 精度、88.57% 数量准确率、60% no-match；0.15 线仍有 1/5 难负例，0.50 才达到 100% no-match，但召回只剩 48.98%。最低真框 0.098、最高假框 0.6045，单阈值不可分。
- Grounding DINO + OWLv2 的阈值与位置共识扫描在 no-match 100% 时最高为 69.39% 召回、97.14% 精度、71.43% 数量准确率；没有达到本检查点 75% 可信召回目标。
- Florence-2 生成式开放词汇定位得到 75.51% 召回、84.09% 精度、62.86% 数量准确率、0% no-match。它没有校准检测置信度，且多目标时常只给一个框；速度和显存优势不能抵消拒识与数量失败。
- 结论：OWLv2 和 Florence-2 都保留为可复现实验清单，不接入设置页、worker、正式包或快捷方式。下一步停止相似模型堆叠，转入用户点选、增删目标和蒙版修正；所有模型框继续只是候选，必须人工确认。
