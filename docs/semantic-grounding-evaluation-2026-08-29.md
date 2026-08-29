# Product Atelier 智能选物自动定位合同与评测

> 状态：R6 / Phase 7A-B 第三源码检查点。固定程序合同、许可明确的真实照片小样、外置模型获取/校验和 RTX 4060 基线均已进入 Git；结果未达到正式门槛，正式便携版没有提升。

## 1. 已落地的产品边界

1. “快速去背景”继续只分离全部前景，不读取目标名称。
2. “智能选物”可以请求本地自动定位候选，但候选只能预填编辑框，永远不能自动确认或直接入队。
3. 本地模型未配置、目录损坏、运行环境缺失、无匹配、低置信度或推理失败时，当前图片仍停留在确认弹窗并可继续手动框选。
4. 用户开始拖框、修改坐标、删除或清空候选后，晚到的模型结果不能覆盖用户修改。
5. 自动定位仅允许读取明确配置的本地模型目录；使用文件系统路径和 local_files_only=True，不会后台下载模型，也不会调用生图 API。
6. 当前正式 PyInstaller sidecar 继续排除 torch / transformers / tokenizers，因此本检查点不会增加已发布便携版体积。真实模型只在开发 Python 环境中做基线，达到门槛后再决定 ONNX、独立可选模型包或其他正式交付方式。
7. 当前 Grounding DINO 文本编码器不能可靠理解中文；中文字符会落为未知 token。适配器现会在未翻译中文上返回 `query_translation_required` 并保留手动框选，不再把显著物体框误报成中文语义理解。

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
~~~

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

英文路径对存在目标的 3 个真实照片查询全部命中且平均匹配 IoU 0.979，但把咖啡粉照片误判为汉堡，置信度仍达到 0.6631；简单提高 0.4 阈值无法解决。中文在封锁前同一误候选置信度 0.5176、标签为 `[UNK] [UNK]`，证明“框中显著主体”不是理解中文。详细原始报告位于 `docs/reports/semantic-grounding-*-rtx4060-2026-08-29.json`。

因此当前模型只适合“英文语义映射后的候选框 + 强制人工确认”，不适合自动判定不存在、不适合自动确认，也尚不适合进入正式包。下一阶段必须同时解决中文查询映射和 no-match 校准；不能用降低门槛来掩盖。

本检查点完整源码门禁：Python 173 项（172 通过、1 个平台预期跳过）、前端 96/96、Vite production build、Rust/Tauri custom-protocol check、Python compileall、Git whitespace 与外置模型逐文件 SHA-256 复验通过。

## 6. 候选模型判断

第一适配器选择 Grounding DINO，是因为它的正式任务就是图像与文本输入的开放集目标检测，输出可供确认的目标框；Transformers 官方示例也提供 AutoProcessor + AutoModelForZeroShotObjectDetection 路线。官方实现采用 Apache-2.0 许可：

- [Hugging Face Grounding DINO 文档](https://huggingface.co/docs/transformers/model_doc/grounding-dino)
- [IDEA-Research GroundingDINO 官方仓库](https://github.com/IDEA-Research/GroundingDINO)

当前实测证明它对受控英文的存在目标定位可用，但原生中文与无匹配都不合格，不能据此宣称适合 Product Atelier。

后续对照候选：

- [Microsoft Florence-2](https://huggingface.co/microsoft/Florence-2-base)：MIT 许可，0.23B 基础模型支持短语定位、目标检测和区域描述，适合作为第二条小模型对照，但尚未接入。
- [Meta SAM 2](https://github.com/facebookresearch/sam2)：Apache-2.0，可用于点选/框选后的蒙版修正；它不是文本定位器，不能替代 grounding。

## 7. 下一门禁

1. 建立可替换查询映射合同：中文原词必须经过可审计的英文受控提示或本地多语模型，映射失败立即回退，不允许静默送入英文 BERT。先评估小词典 + 用户可编辑提示能否覆盖高频电商名词，再决定是否增加本地翻译权重。
2. 扩充至少 20 张真实照片，补多相似商品、包装、遮挡、毛发、复杂背景和更多 no-match；冻结双人复核人工框后再校准阈值或二级存在性判断。
3. 为候选增加点选、增删目标和蒙版修正；定位稳定后再评估 SAM 类边缘修正。单图失败继续停留人工确认，不能让整批任务作废。
4. 评估模型常驻或独立可选模型服务，把 14–16 秒首次加载从用户第一次操作移出；正式 sidecar 继续不含实验依赖。
5. 只有中文映射、no-match、真实照片扩展集、UI 实测和完整 Windows candidate-first 门禁全部通过，才允许提升新正式便携版。
