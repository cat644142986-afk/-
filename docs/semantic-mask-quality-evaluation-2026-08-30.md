# Product Atelier 语义抠图柔边与恢复门禁

> 日期：2026-08-30<br>
> 范围：R6 / Phase 7A-B 固定真实照片烟测与程序合同<br>
> 结论：关闭 rembg 二值后处理，保留 BiRefNet 原生柔和 Alpha；尚不等同于逐像素边缘精度或商业成品质检。

## 1. 本次解决的问题

生产 `remove_bg_hd()` 原先启用 `post_process_mask=True`。真实图片对照证明，该参数会把 BiRefNet 的原生柔和 Alpha 阈值化为只有 `0/255` 两级的硬蒙版，透明容器、阴影和斜边因此更容易出现台阶与锯齿。

本次把生产参数改为 `post_process_mask=False`，继续保持：

- 本地 `birefnet-general`，不调用付费 API；
- `alpha_matting=False`，不新增 pymatting/numba 体积；
- 语义确认区域约束、保留/删除笔画和空蒙版拒绝逻辑不变；
- trace 新增 `alpha_mode=native-soft` 与 `post_process_mask=false`，结果可审计。
- 主 sidecar 合同升级到 `2026-08-30.3`，使候选 manifest 和健康接口能够区分旧硬边行为与新柔边行为。

## 2. 固定语料和声明边界

烟测复用仓库中 3 张已锁定来源、许可、尺寸和 SHA-256 的真实照片：汉堡、透明水瓶、咖啡包装。清单位于 `tests/fixtures/semantic_mask_quality/manifest.json`。

这组数据可以验证：

- 输出非空；
- 保留多级 Alpha 和边缘过渡像素；
- 已确认区域以外无输出；
- 本地执行时间有界；
- 删除笔画能降低 Alpha，随后保留笔画能恢复非空输出。

这组数据没有逐像素真值蒙版，因此不能声称测得边缘准确率、透明材质保真率、毛发精度或“已经达到商业成品质量”。这些结论仍需要人工制作的 Alpha 真值或有记录的盲审协议。

## 3. 同图同模型对照结果

| 指标 | 旧参数：二值后处理开启 | 候选/生产参数：二值后处理关闭 |
|---|---:|---:|
| 通过用例 | 0 / 3 | 3 / 3 |
| 每张 Alpha 级数 | 2、2、2 | 256、256、256 |
| 过渡像素 | 0、0、0 | 7,197、14,119、14,051 |
| 前景平均 Alpha | 1.000、1.000、1.000 | 0.986、0.983、0.984 |
| 修正后恢复 | 3 / 3 | 3 / 3 |
| 平均推理耗时 | 11,378.515 ms | 10,984.005 ms |
| P95 推理耗时 | 11,809.976 ms | 11,374.920 ms |

两组均使用同一个 972,666,916-byte ONNX 文件，SHA-256 为 `58f621f00f5d756097615970a88a791584600dcf7c45b18a0a6267535a1ebd3c`。关闭二值后处理没有引入可观察的性能回退；本次样本反而略快，但 3 张小样不足以把约 0.4 秒差异解释为稳定提速。

当前 ONNX Runtime 只报告 `AzureExecutionProvider` 与 `CPUExecutionProvider`，没有 CUDA provider。热推理约 11 秒/张是真实 CPU 路径数据，不能误报为 RTX GPU 加速结果；正式候选验收必须保留这一性能事实。

## 4. 失败与安全合同

- 评测器缺少本地 `birefnet-general.onnx` 时直接失败，不允许为了跑测试自动下载近 1 GB 权重。
- 清单逐张验证字节数、SHA-256 和画布尺寸；证据漂移时门禁失败。
- 合成回归覆盖硬二值蒙版、空蒙版、笔画顺序和删除后恢复，不依赖真实用户图片。
- `outside_region_nonzero_pixels=0` 只证明确认区域应用合同，不代表模型在原始整图上没有误分割。

## 5. 决策与下一游标

生产链采用 `post_process_mask=False`。不再叠加未经证据支持的羽化、腐蚀、去噪或 Alpha matting，以免用第二层启发式掩盖模型问题。

本检查点完成后仍不直接覆盖正式便携版。下一步从干净 Git 提交构建 Windows 候选，依次验证 PyInstaller sidecar、候选 EXE、真实 PNG Alpha、NTFS 事务提升、正式目录双 smoke、桌面快捷方式和 NSIS；任何候选门禁失败都保持现有正式版不变。

## 6. 可复现入口

本地已有模型时运行：

```powershell
python tools\evaluate_semantic_masks.py --post-process-mask true --report docs\reports\semantic-mask-quality-postprocess-true-rtx4060-2026-08-30.json
python tools\evaluate_semantic_masks.py --post-process-mask false --report docs\reports\semantic-mask-quality-postprocess-false-rtx4060-2026-08-30.json
```

第一条按设计返回失败状态，因为旧参数不满足柔边门禁；第二条必须通过。完整证据保存在 `docs/reports/` 的两份 JSON 中。
