# Product Atelier 智能选物自动定位合同与评测

> 状态：R6 / Phase 7A-B 第五源码检查点。30 张 Open Images V7 真实照片扩展门禁、阈值校准和两级人工确认界面已落地；扩展集证明单一阈值不能同时解决召回与误选，正式便携版仍未提升。

## 1. 已落地的产品边界

1. “快速去背景”继续只分离全部前景，不读取目标名称。
2. “智能选物”可以请求本地自动定位候选，但候选只能预填编辑框，永远不能自动确认或直接入队。
3. 本地模型未配置、目录损坏、运行环境缺失、无匹配、低置信度或推理失败时，当前图片仍停留在确认弹窗并可继续手动框选。
4. 用户开始拖框、修改坐标、删除或清空候选后，晚到的模型结果不能覆盖用户修改。
5. 自动定位仅允许读取明确配置的本地模型目录；使用文件系统路径和 local_files_only=True，不会后台下载模型，也不会调用生图 API。
6. 当前正式 PyInstaller sidecar 继续排除 torch / transformers / tokenizers，因此本检查点不会增加已发布便携版体积。真实模型只在开发 Python 环境中做基线，达到门槛后再决定 ONNX、独立可选模型包或其他正式交付方式。
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

`requirements-grounding.txt` 是明确隔离的开发依赖，不被正式构建或普通应用初始化引用。若目标机器要使用特定 CUDA/Metal 版本，应按 PyTorch 官方平台说明安装对应 wheel 后再安装其余依赖。目标目录必须在源码仓库外；下载后逐文件复算 SHA-256，并只在外置目录写本地 receipt。权重、缓存、receipt 均不进入 Git。macOS/Linux 可省略 `--destination`，默认进入 `~/ProductAtelier-Models/<artifact-id>`，但只有配置环境变量后开发适配器才会读取。

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

实际界面已在隔离数据目录完成：导入授权汉堡图 → 选择“智能选物” → 输入“汉堡”、数量 1 → 首次本地模型加载 → 显示 92% 自动候选 → 人工确认 → 主界面显示“已确认 1 个汉堡”。验证中发现并修复两项只有端到端操作才暴露的问题：15 秒普通请求上限会误杀约 14–17 秒冷启动，现为该离线操作单独保留 60 秒；系统解析出的 `hamburger` 曾串成下一次用户覆盖词，现已分离解析词与用户覆盖词，未知“月球齿轮”会保持 0 个候选并解释手动恢复。

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

后续对照候选：

- [Microsoft Florence-2](https://huggingface.co/microsoft/Florence-2-base)：MIT 许可，0.23B 基础模型支持短语定位、目标检测和区域描述，适合作为第二条小模型对照，但尚未接入。
- [Meta SAM 2](https://github.com/facebookresearch/sam2)：Apache-2.0，可用于点选/框选后的蒙版修正；它不是文本定位器，不能替代 grounding。

## 7. 下一门禁

1. 已完成可审计查询映射、30 张真实照片/35 查询门禁和两级人工建议界面。后续扩展词表或数据集必须带固定用例，不能把联网翻译、自由生成或人工建议指标伪装成自动质量。
2. 下一实现门禁是独立可选模型运行时/模型包：主 sidecar 保持轻量，运行时和权重有独立版本、清单、哈希、健康探测、缺失回退和设置页状态；路径不得硬编码本机盘符。
3. 对照第二存在性验证器或更强定位模型，目标是提高可信层召回并压低难负例建议。必须在同一冻结扩展集上比较，不能只展示成功图片。
4. 为候选增加点选、增删目标和蒙版修正；定位稳定后再评估 SAM 类边缘修正。单图失败继续停留人工确认，不能让整批任务作废。
5. 只有可选运行时/模型包、扩展集可信门禁、正式 sidecar 实测和完整 Windows candidate-first 门禁全部通过，才允许提升新正式便携版。源码界面可用不等于正式便携版已经获得自动定位能力。
