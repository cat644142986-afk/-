# Product Atelier 智能选物自动定位合同与评测

> 状态：R6 / Phase 7A-B 第二源码检查点。此文档、测试集清单、评测器和适配器均进入 Git；真实照片质量基线与正式便携版提升尚未完成。

## 1. 已落地的产品边界

1. “快速去背景”继续只分离全部前景，不读取目标名称。
2. “智能选物”可以请求本地自动定位候选，但候选只能预填编辑框，永远不能自动确认或直接入队。
3. 本地模型未配置、目录损坏、运行环境缺失、无匹配、低置信度或推理失败时，当前图片仍停留在确认弹窗并可继续手动框选。
4. 用户开始拖框、修改坐标、删除或清空候选后，晚到的模型结果不能覆盖用户修改。
5. 自动定位仅允许读取明确配置的本地模型目录；使用文件系统路径和 local_files_only=True，不会后台下载模型，也不会调用生图 API。
6. 当前正式 PyInstaller sidecar 继续排除 torch / transformers / tokenizers，因此本检查点不会增加已发布便携版体积。真实模型只在开发 Python 环境中做基线，达到门槛后再决定 ONNX、独立可选模型包或其他正式交付方式。

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

该集合只验证候选框、数量、失败恢复、指标计算和跨电脑可复现性，不代表真实电商照片质量。真实质量语料必须另建来源清单，记录许可、SHA-256、人工标注版本和审核人；不得放入用户私人图片后宣称为公共测试集。

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
$env:PRODUCT_ATELIER_GROUNDING_MODEL_PATH = "D:\Models\grounding-dino"
python tools\evaluate_semantic_grounding.py --run-local
~~~

该命令不会下载权重。未配置模型时会得到 unavailable 并以门禁失败退出，这是预期的诚实结果。

## 4. 候选模型判断

第一适配器选择 Grounding DINO，是因为它的正式任务就是图像与文本输入的开放集目标检测，输出可供确认的目标框；Transformers 官方示例也提供 AutoProcessor + AutoModelForZeroShotObjectDetection 路线。官方实现采用 Apache-2.0 许可：

- [Hugging Face Grounding DINO 文档](https://huggingface.co/docs/transformers/model_doc/grounding-dino)
- [IDEA-Research GroundingDINO 官方仓库](https://github.com/IDEA-Research/GroundingDINO)

当前不能据此宣称适合 Product Atelier。用户输入以中文为主，而该路线的中文商品名、相似商品计数、包装小字和透明目标表现仍未实测；这正是适配层和固定评测器必须先落地的原因。

后续对照候选：

- [Microsoft Florence-2](https://huggingface.co/microsoft/Florence-2-base)：MIT 许可，0.23B 基础模型支持短语定位、目标检测和区域描述，适合作为第二条小模型对照，但尚未接入。
- [Meta SAM 2](https://github.com/facebookresearch/sam2)：Apache-2.0，可用于点选/框选后的蒙版修正；它不是文本定位器，不能替代 grounding。

## 5. 下一门禁

1. 选取来源和许可明确的非私人真实电商图片，冻结人工框标注并记录 SHA-256。
2. 在本机 RTX 4060 8GB 上分别测试中文原词和受控英文提示，区分“模型看不见目标”和“语言映射失败”。
3. 记录首次加载、热启动、单图 P50/P95、显存峰值、召回、误候选与数量正确率。
4. 达不到门槛时继续保留人工框选，不把实验模型塞入正式包。
5. 真实框定位稳定后，再实现点选/蒙版增删和 SAM 类边缘修正；最后才进入新的 Windows candidate-first 正式发布门禁。
