import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useReactFlow, useViewport } from '@xyflow/react';
import { Button, ComboBox, Dialog, DialogTrigger, Input, Label, ListBox, ListBoxItem, Modal, ModalOverlay, Popover } from 'react-aria-components';
import { ChevronDown, Image, Video, SlidersHorizontal, Sparkles, LoaderCircle, X, RefreshCw } from 'lucide-react';
import { CanvasReferencePicker, CanvasReferenceTray } from '../../../src/js/canvas-retake-reference.jsx';
import { stopComposerKeyboardEvent } from '../../../src/js/canvas-transplant-geometry.js';
import { composerPlacement, composerOwnsEscape } from './overlay-layout.js';
import { eligibleComposerModels, composerAdmissionProblem, composerRecommendation } from '../../../src/js/canvas-model-admission.js';
import { nodeDraft, resolveCreativeInput } from './creative-input.js';
import { mediaIdentity } from './node-scene-codec.js';

export function ModelPicker({ value, models, recommendation, onChange, disabled }) {
  return <ComboBox aria-label="图像模型" className="pa-model-picker" items={models} selectedKey={value || null}
    onSelectionChange={(key) => { if (key !== null) onChange(String(key)); }} menuTrigger="focus" isDisabled={disabled}>
    <div className="pa-model-field"><Input aria-label="搜索模型" placeholder="选择模型" /><Button aria-label="展开模型列表"><ChevronDown size={13} /></Button></div>
    <Popover className="pa-control-popover pa-model-popover" placement="top start" offset={8} shouldFlip>
      <ListBox className="pa-model-list" renderEmptyState={() => <p className="pa-control-empty">没有匹配模型</p>}>
        {(model) => <ListBoxItem id={model.provider_model_id} textValue={model.display_name || model.provider_model_id} className="pa-model-option">
          <span>{model.display_name || model.provider_model_id}</span>
          {recommendation?.recommended_provider_model_id === model.provider_model_id && <small>推荐</small>}
          <i>{model.provider_model_id}</i>
        </ListBoxItem>}
      </ListBox>
    </Popover>
  </ComboBox>;
}

const RATIOS = ['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9'];
export function OutputControls({ draft, onChange }) {
  return <DialogTrigger><Button className="pa-parameter-trigger" aria-label="输出参数"><SlidersHorizontal size={13} /><span>{draft.ratio} · {draft.resolution.toUpperCase()}</span></Button>
    <Popover className="pa-control-popover pa-output-popover" placement="top start" offset={8} shouldFlip>
      <Dialog aria-label="输出参数">
        <div className="pa-output-title"><strong>输出尺寸</strong><small>不兼容组合会阻止提交，不自动降级</small></div>
        <div className="pa-size-columns"><section><strong>比例</strong>{RATIOS.map((ratio) => <button type="button" key={ratio} aria-pressed={draft.ratio === ratio} onClick={() => onChange({ ratio })}>{ratio}</button>)}</section>
          <section><strong>分辨率</strong>{['1k', '2k', '4k'].map((resolution) => <button type="button" key={resolution} aria-pressed={draft.resolution === resolution} onClick={() => onChange({ resolution })}>{resolution.toUpperCase()}<small>{resolution === '1k' ? '1024' : resolution === '2k' ? '2048' : '4096'} px</small></button>)}
            <label>数量<input type="number" min="1" max="4" value={draft.count} onChange={(event) => onChange({ count: Math.max(1, Math.min(4, Number(event.target.value) || 1)) })} /></label>
          </section></div>
      </Dialog>
    </Popover>
  </DialogTrigger>;
}

export function NodeComposer({ activeId, nodes, edges, api, onDraft, onReview, onClose, onStatus, onHeight }) {
  const flow = useReactFlow();
  const viewport = useViewport();
  const rootRef = useRef(null), addRef = useRef(null), composition = useRef(false);
  const [dimensions, setDimensions] = useState({ width: window.innerWidth, height: window.innerHeight, composerHeight: 250 });
  const [pickerAnchor, setPickerAnchor] = useState(null), [pickerSelected, setPickerSelected] = useState('');
  const [admission, setAdmission] = useState(null), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0), [reviewing, setReviewing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const target = nodes.find((node) => node.id === activeId);
  const draft = nodeDraft(target);
  const input = target ? resolveCreativeInput(nodes, edges, activeId) : null;
  const models = eligibleComposerModels(admission), recommendation = composerRecommendation(admission), problem = composerAdmissionProblem(admission);

  useEffect(() => {
    if (!target) return undefined;
    if (input.taskKind === 'image-to-video') { setAdmission(null); setLoading(false); setError(''); return undefined; }
    let live = true; setLoading(true); setError('');
    api.getProviderModelAdmission({ taskKind: input.taskKind, ratio: draft.ratio, resolution: draft.resolution })
      .then((payload) => { if (live) setAdmission(payload); })
      .catch((reason) => { if (live) { setAdmission(null); setError(reason.message); } })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [api, input?.taskKind, draft.ratio, draft.resolution, refresh, Boolean(target)]);

  useEffect(() => {
    // Initialize from PA's explicit routing recommendation, never the first
    // arbitrary list item. Preserve a previously selected incompatible draft.
    if (!draft.model && recommendation?.recommended_provider_model_id) onDraft(activeId, { model: recommendation.recommended_provider_model_id });
  }, [activeId, draft.model, recommendation?.recommended_provider_model_id, onDraft]);

  useLayoutEffect(() => {
    const measure = () => { const height = rootRef.current?.getBoundingClientRect().height || 250;
      setDimensions({ width: window.innerWidth, height: window.innerHeight, composerHeight: height }); onHeight?.(height); };
    measure(); const observer = new ResizeObserver(measure);
    if (rootRef.current) observer.observe(rootRef.current);
    window.addEventListener('resize', measure);
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, [onHeight]);

  if (!target || target.type === 'group' || target.type === 'prompt') return null;
  const bounds = flow.getNodesBounds([activeId]);
  const frame = { left: bounds.x * viewport.zoom + viewport.x, top: bounds.y * viewport.zoom + viewport.y, width: bounds.width * viewport.zoom, height: bounds.height * viewport.zoom };
  const placement = composerPlacement(frame, dimensions.width, dimensions.height, dimensions.composerHeight);
  const references = input.references;
  const reference = references[0];
  const referenceNode = nodes.find((node) => node.id === reference?.sourceNodeId);
  const referenceView = reference && { elementId: reference.sourceNodeId, assetId: reference.asset_id, resultId: reference.result_id, title: reference.name || referenceNode?.data.name || '参考图', previewUrl: referenceNode?.data.previewUrl || referenceNode?.data.src };
  const images = nodes.filter((node) => node.type === 'image' && node.data.media && !node.data.mediaError).map((node) => ({
    elementId: node.id, assetId: node.data.media.asset_id, resultId: node.data.media.result_id, title: node.data.name, previewUrl: node.data.previewUrl || node.data.src,
  }));
  const selectedReference = images.find((image) => image.elementId === pickerSelected);
  const modelSelected = models.some((model) => model.provider_model_id === draft.model);
  const reason = input.taskKind === 'image-to-video' ? error : error || (loading ? '正在核对模型' : problem?.message) || (!modelSelected ? '请选一个兼容模型' : '');
  const explicitUnsupported = references.some(ref => !ref.sourceNodeId) ? '参考对象已移除或存在同素材副本；请重新选择精确参考，草稿保留' : input.text.length > 1200 ? '当前消息与上游提示词合计超过 1200 字；请精简输入，草稿保留' : references.length > 1 ? '当前执行合同只验证了单参考；请移除多余输入' : draft.count !== 1 ? '多方案执行需要显式预算核对；当前单次合同仅支持 1 张' : '';
  const syncCatalog = async () => {
    if (syncing) return;
    setSyncing(true); setError('');
    try { await api.syncProvider(); setRefresh(value => value + 1); onStatus('目录已同步 · 未调用生成接口'); }
    catch (failure) { setError(`目录同步未完成，草稿与上次快照保留：${failure.message}`); }
    finally { setSyncing(false); }
  };

  const review = async () => {
    if (composition.current || reviewing || reason || explicitUnsupported || !input.text.trim()) return;
    setReviewing(true); setError('');
    try { await onReview(input, admission); } catch (failure) { setError(failure.message); }
    finally { setReviewing(false); }
  };

  return <>
    <section ref={rootRef} className="pa-node-composer nodrag nopan nowheel" style={{ left: placement.left, top: placement.top, width: placement.width }} aria-label="当前节点创作"
      onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}
      onKeyDown={(event) => { stopComposerKeyboardEvent(event); if (composerOwnsEscape(event, composition.current)) { event.preventDefault(); onClose(); } else if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); review(); } }}>
      <header><div role="group" aria-label="输出类型"><button type="button" aria-pressed={draft.mode === 'image'} onClick={() => onDraft(activeId, { mode: 'image' })}><Image size={13} />图片</button><button type="button" aria-pressed={draft.mode === 'video'} onClick={() => onDraft(activeId, { mode: 'video' })}><Video size={13} />视频</button></div><button type="button" aria-label="收起创作输入（保留草稿）" onClick={onClose}><X size={14} /></button></header>
      <CanvasReferenceTray image={referenceView} addButtonRef={addRef} onAdd={() => { const rect = addRef.current.getBoundingClientRect(); setPickerAnchor({ x: rect.left, y: rect.top - 350 }); }}
        onRemove={() => { if (reference?.sourceNodeId && edges.some((edge) => edge.source === reference.sourceNodeId && edge.target === activeId)) onStatus('这张参考来自连线，请断开对应输入连接'); else onDraft(activeId, { references: [], useCurrentMedia: false }); }} />
      {input.prompts.length > 0 && <div className="pa-upstream-prompt">已连接 {input.prompts.length} 个提示词节点 <details><summary>查看输入</summary>{input.prompts.map((prompt) => <p key={prompt.nodeId}>{prompt.text}</p>)}</details></div>}
      <textarea aria-label="描述生成或修改要求" placeholder="描述想生成或修改的画面…" value={draft.prompt} maxLength={1200}
        onCompositionStart={() => { composition.current = true; }} onCompositionEnd={() => { composition.current = false; }}
        onChange={(event) => onDraft(activeId, { prompt: event.target.value })} />
      <footer>{draft.mode === 'video' ? <><span className="pa-video-contract-label">离线视频预览 · 非真实 AI 生成</span><select aria-label="视频时长" value={draft.duration_seconds} onChange={(event) => onDraft(activeId, { duration_seconds: Number(event.target.value) })}>{[3, 5, 8, 10].map((value) => <option key={value} value={value}>{value} 秒</option>)}</select><select aria-label="视频比例" value={draft.ratio} onChange={(event) => onDraft(activeId, { ratio: event.target.value })}>{['1:1', '16:9', '9:16', '4:3', '3:4'].map((value) => <option key={value}>{value}</option>)}</select></> : <><ModelPicker value={draft.model} models={models} recommendation={recommendation} disabled={loading || !models.length} onChange={(model) => onDraft(activeId, { model })} />
        <OutputControls draft={draft} onChange={(patch) => onDraft(activeId, patch)} /></>}
        <button type="button" className="pa-review-button" onClick={review} disabled={Boolean(reason || explicitUnsupported || reviewing || !input.text.trim())}>{reviewing ? <LoaderCircle size={13} /> : <Sparkles size={13} />}核对</button></footer>
      {(reason || explicitUnsupported) && <div className="pa-composer-problem" role="status"><span>{explicitUnsupported || reason}</span><button type="button" onClick={() => setRefresh((value) => value + 1)} aria-label="重新核对目录"><RefreshCw size={13} /></button>
        {problem?.recoveryAction && <button type="button" disabled={syncing} onClick={syncCatalog}>{syncing ? '同步中…' : '同步模型目录'}</button>}
        {input.taskKind === 'text-to-image' && <span>从零生成尚需独立准入证据；可添加参考图继续创作，不会用假参考图绕过合同。</span>}
        {(draft.ratio !== '1:1' || draft.resolution !== '2k' || draft.count !== 1) && <button type="button" onClick={() => onDraft(activeId, { ratio: '1:1', resolution: '2k', count: 1 })}>使用已验证参数</button>}
      </div>}
    </section>
    {pickerAnchor && <CanvasReferencePicker anchor={pickerAnchor} images={images} selectedImage={selectedReference} onSelectImage={setPickerSelected}
      onCancel={() => { setPickerAnchor(null); setPickerSelected(''); requestAnimationFrame(() => addRef.current?.focus()); }}
      onConfirm={() => {
        const node = nodes.find((candidate) => candidate.id === pickerSelected);
        if (node?.data.media) onDraft(activeId, { references: [{ ...mediaIdentity(node.data.media), source_element_id: node.id }], useCurrentMedia: false });
        setPickerAnchor(null); setPickerSelected(''); requestAnimationFrame(() => addRef.current?.focus());
      }} />}
  </>;
}

export function GovernorDialog({ review, stale, onCancel, onConfirm, busy, error }) {
  return <ModalOverlay className="pa-dialog-overlay" isOpen={Boolean(review)} isDismissable={!busy} onOpenChange={(open) => { if (!open) onCancel(); }}>
    <Modal className="pa-governor-modal"><Dialog aria-label="核对此次创作">{({ close }) => review && <>
      <header><h2>核对此次创作</h2><Button aria-label="取消核对" isDisabled={busy} onPress={close}><X size={17} /></Button></header>
      <div className="pa-governor-body"><p className="pa-review-intent">{review.input.text}</p><dl><dt>输入</dt><dd>{review.input.references.map((ref) => ref.name || ref.asset_id).join('、') || '从零生成'}</dd><dt>模型</dt><dd>{review.input.model}</dd><dt>输出</dt><dd>{review.localOnly ? `${review.input.ratio} · ${review.input.duration_seconds} 秒 · 离线预置片段` : `${review.input.ratio} · ${review.input.resolution.toUpperCase()} · ${review.input.count} 张`}</dd></dl>
        {review.localOnly ? <p>这是现有离线视频 Task / Result / 回填链验证：输出为预置片段，不代表按这张参考图生成视频。Provider 调用 0，付费 0。</p> : <><details><summary>执行上下文与设计约束</summary><pre>{JSON.stringify(review.bundle?.execution_context?.summary || {}, null, 2)}</pre><small>{review.bundle?.execution_context?.context_sha256}</small></details>
        <details><summary>Governor 编译约束</summary><pre>{review.bundle?.prompt_text || review.bundle?.compiled_prompt || review.bundle?.prompt || review.bundle?.prompt_addition || '此处展示冻结上下文；Provider 最终 Prompt 记录在 Task trace。'}</pre></details></>}
        {stale && <p role="alert">输入或相关上下文已变化，请重新核对；当前草稿已保留。</p>}{error && <p role="alert">{error}</p>}
      </div><footer><Button isDisabled={busy} onPress={close}>取消</Button><Button className="is-primary" isDisabled={stale || busy} onPress={onConfirm}>{busy ? '正在创建任务' : '确认并执行'}</Button></footer>
    </>}</Dialog></Modal>
  </ModalOverlay>;
}
