import React, { lazy, Suspense, useEffect, useState } from 'react';
import { Button, Dialog, Modal, ModalOverlay } from 'react-aria-components';
import { X, Download, LoaderCircle } from 'lucide-react';
import { PromptTemplatePanel } from './prompt-template-panel.jsx';
import { WorkflowTransferPanel } from './workflow-transfer-panel.jsx';

const CanvasEditDialog = lazy(() => import('./canvas-edit-dialog.jsx'));
const titles = { canvases: '画布', assets: '资产库', logs: '任务日志', shortcuts: '快捷键', templates: '提示词模板', 'split-prompt': '提示词分段', preview: '预览', edit: '精细编辑', workflow: '工作流导入 / 导出' };

function AssetTile({ asset, api, onImport, disabled }) {
  const [src, setSrc] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { let live = true; api.getAssetThumbnailUrl(asset.id, 320).then((url) => { if (live) setSrc(url); }); return () => { live = false; }; }, [asset.id, api]);
  return <button type="button" className="pa-asset-tile" disabled={busy || disabled} onClick={async () => {
    setBusy(true); setError(''); try { await onImport(asset); } catch (reason) { setError(reason.message); } finally { setBusy(false); }
  }}><div>{src && <img src={src} alt="" />} {busy && <LoaderCircle size={18} />}</div><strong>{asset.name}</strong><small>{error || (asset.kind === 'audio' ? `音频 · ${asset.duration_seconds || asset.metadata?.duration_seconds || '?'} 秒` : `${asset.width} × ${asset.height}${asset.kind === 'video' ? ' · 视频' : ''}`)}</small></button>;
}

export function WorkbenchDialog({ dialog, nodes, edges, jobs, api, onClose, onNodes, onImportAsset, onEdited, canvasAdapter, onOpenCanvas, onCreateCanvas, metadata, onMetadata, viewport, onImportWorkflow, onRestoreJob, onBusyChange }) {
  const node = nodes.find((item) => item.id === dialog.nodeId);
  const [assets, setAssets] = useState([]), [filter, setFilter] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [separator, setSeparator] = useState(node?.data.separator || '\n---\n');
  const [editingBusy, setEditingBusy] = useState(false);
  useEffect(() => { onBusyChange?.(editingBusy); return () => onBusyChange?.(false); }, [editingBusy, onBusyChange]);
  const [canvases, setCanvases] = useState([]), [newName, setNewName] = useState('');
  useEffect(() => {
    if (dialog.type !== 'canvases') return undefined;
    let live = true; setBusy(true);
    api.listSpatialCanvases().then((response) => { if (live) setCanvases((response.canvases || []).filter((canvas) => canvas.thumbnail?.scene_kind === 'pa-node-canvas')); })
      .catch((reason) => { if (live) setError(reason.message); }).finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [api, dialog.type]);
  useEffect(() => {
    if (dialog.type !== 'assets') return undefined;
    let live = true; setBusy(true);
    api.getAssets(500).then((response) => { if (live) setAssets(response.assets || []); }).catch((reason) => { if (live) setError(reason.message); }).finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [api, dialog.type]);
  return <ModalOverlay className="pa-dialog-overlay" isOpen isDismissable={!editingBusy} onOpenChange={(open) => { if (!open && !editingBusy) onClose(); }}>
    <Modal className={`pa-workbench-modal pa-dialog-${dialog.type}`}><Dialog aria-label={titles[dialog.type] || '画布'}>
      <header><h2>{titles[dialog.type]}</h2><Button aria-label="关闭" isDisabled={editingBusy} onPress={onClose}><X size={18} /></Button></header>
      <div className="pa-workbench-body">
        {dialog.type === 'canvases' && <>
          <div className="pa-canvas-create"><input aria-label="新画布名称" placeholder="未命名画布" value={newName} onChange={(event) => setNewName(event.target.value)} />
            <Button isDisabled={busy} onPress={async () => { setBusy(true); try { await onCreateCanvas(newName.trim() || '未命名画布'); } catch (reason) { setError(reason.message); } finally { setBusy(false); } }}>新建画布</Button></div>
          {error && <p role="alert">{error}</p>}
          <div className="pa-canvas-list">{canvases.map((canvas) => <Button key={canvas.id} isDisabled={busy} onPress={async () => { setBusy(true); try { await onOpenCanvas(canvas.id); } catch (reason) { setError(reason.message); } finally { setBusy(false); } }}>
            <strong>{canvas.name}</strong><small>{canvas.thumbnail?.element_count || 0} 个对象 · 版本 {canvas.current_revision}</small></Button>)}</div>
          {!busy && !canvases.length && <p>这里仅列出新节点画布，旧 Excalidraw 文档保留在原工作区，不会被转换或覆盖。</p>}
        </>}
        {dialog.type === 'assets' && <><input aria-label="搜索素材" placeholder="搜索素材名称" value={filter} onChange={(event) => setFilter(event.target.value)} />
          {busy && <p role="status">正在读取资产…</p>}{error && <p role="alert">{error}</p>}
          <div className="pa-asset-grid">{assets.filter((asset) => asset.name?.toLowerCase().includes(filter.toLowerCase())).map((asset) => <AssetTile key={asset.id} asset={asset} api={api} disabled={editingBusy} onImport={async asset => {
            setEditingBusy(true); try { await onImportAsset(asset); } finally { setEditingBusy(false); }
          }} />)}</div>
          {!busy && !assets.length && <p>暂无素材。关闭后将图片或视频拖入画布即可导入。</p>}</>}
        {dialog.type === 'logs' && <>{!jobs.length && <p>当前画布没有任务。输入与核对预览不会创建 Task。</p>}{jobs.map((job) => <details className="pa-log-entry" key={job.id}>
          <summary><strong>{job.parameters?.user_request || job.snapshot?.parameters?.user_request || job.title || '创作任务'}</strong><span>{job.status}</span></summary>
          <dl><dt>Task</dt><dd>{job.id}</dd><dt>模型</dt><dd>{job.parameters?.model || job.snapshot?.parameters?.model}</dd></dl>
          {job.error && <p role="alert">{String(job.error)}</p>}<pre>{JSON.stringify(job.items || [], null, 2)}</pre>
          {['completed', 'partial'].includes(job.status) && <Button isDisabled={editingBusy} onPress={async () => {
            setEditingBusy(true); setError('');
            try { const response = await api.getJob(job.id); await onRestoreJob(response.job || response); }
            catch (reason) { setError(reason.message); }
            finally { setEditingBusy(false); }
          }}>将已有结果找回画布</Button>}
        </details>)}{error && <p role="alert">{error}</p>}</>}
        {dialog.type === 'shortcuts' && <dl className="pa-shortcuts"><dt>平移</dt><dd>滚轮 / 中键 / 右键拖动</dd><dt>缩放</dt><dd>Ctrl + 滚轮；左下角 + / −</dd><dt>多选</dt><dd>Shift / Ctrl + 点击；拖动框选</dd><dt>复制 / 粘贴</dt><dd>Ctrl+C / Ctrl+V</dd><dt>组合 / 取消组合</dt><dd>Ctrl+G / Ctrl+Shift+G</dd><dt>撤销 / 重做</dt><dd>Ctrl+Z / Ctrl+Shift+Z</dd><dt>删除</dt><dd>Delete；对象悬停删除按钮</dd><dt>核对当前草稿</dt><dd>输入区 Ctrl+Enter（不直接执行）</dd><dt>关闭弹层</dt><dd>Escape</dd></dl>}
        {dialog.type === 'workflow' && <WorkflowTransferPanel nodes={nodes} edges={edges} viewport={viewport} metadata={metadata} api={api} canvasAdapter={canvasAdapter} onImported={onImportWorkflow} onBusy={setEditingBusy} />}
        {dialog.type === 'templates' && <PromptTemplatePanel metadata={metadata} onMetadata={onMetadata} text={node?.data.text || ''} onApply={(text) => {
          onNodes((current) => current.map((item) => item.id === node?.id ? { ...item, data: { ...item.data, text } } : item)); onClose();
        }} />}
        {dialog.type === 'split-prompt' && <><label>分隔符<input value={separator} onChange={(event) => setSeparator(event.target.value)} aria-label="提示词分隔符" /></label>
          {(node?.data.text || '').split(separator || '\n---\n').map((part, index) => <pre key={index}>{part || '（空段）'}</pre>)}
          <Button onPress={() => { onNodes((current) => current.map((item) => item.id === node?.id ? { ...item, data: { ...item.data, separator } } : item)); onClose(); }}>保存分隔符</Button></>}
        {dialog.type === 'preview' && node && <div className="pa-original-preview">{node.type === 'video' ? <video src={node.data.src} controls autoPlay={false} /> : node.type === 'audio' ? <audio src={node.data.src} controls /> : <img src={node.data.src} alt={node.data.name} />}<Button onPress={() => api.downloadAsset(node.data.media.asset_id, node.data.name)}><Download size={15} />下载原文件</Button></div>}
        {dialog.type === 'edit' && node && <Suspense fallback={<p>正在打开精修引擎…</p>}><CanvasEditDialog node={node} mode={dialog.mode} api={api} canvasAdapter={canvasAdapter} onCancel={onClose} onApplied={onEdited} onBusy={setEditingBusy} /></Suspense>}
        {['edit', 'preview'].includes(dialog.type) && !node && <p role="alert">对象已移除。关闭此窗口后可从任务日志找回已提交结果。</p>}
      </div>
    </Dialog></Modal>
  </ModalOverlay>;
}
