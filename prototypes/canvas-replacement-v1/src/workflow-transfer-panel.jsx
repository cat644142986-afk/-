import React, { useEffect, useRef, useState } from 'react';
import { Button } from 'react-aria-components';
import { exportWorkflow, importWorkflow, workflowScene } from './workflow-transfer.js';

export function WorkflowTransferPanel({ nodes, edges, viewport, metadata, api, canvasAdapter, onImported, onBusy }) {
  const [busy, setBusy] = useState(false), [status, setStatus] = useState(''), [error, setError] = useState('');
  const input = useRef(null);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  const selected = nodes.filter(node => node.selected).map(node => node.id);
  const save = async includeResources => {
    setBusy(true); setError(''); setStatus('正在准备工作流…');
    try {
      const blob = await exportWorkflow({ scene: workflowScene(nodes, edges, viewport, metadata, selected), api, includeResources });
      await api.saveWorkflowBundle(`PA-workflow.${includeResources ? 'zip' : 'json'}`, blob);
      setStatus('已导出；导入不会自动执行任务。');
    } catch (reason) { setError(reason.message); setStatus(''); }
    finally { setBusy(false); }
  };
  return <div className="pa-workflow-transfer">
    <p>{selected.length ? `导出 ${selected.length} 个选中对象及组内对象` : `导出当前画布的 ${nodes.length} 个对象`}；包括连接、草稿与个人模板，不含凭证和执行状态。</p>
    <div><Button isDisabled={busy || !nodes.length} onPress={() => save(false)}>导出 JSON 引用</Button> <Button isDisabled={busy || !nodes.length} onPress={() => save(true)}>导出 ZIP 与原始资源</Button></div>
    <p>JSON 适用于同一 PA 资产库。ZIP 可携带图片、视频、音频；跨库导入为素材，不伪造原 Task / Result / lineage。</p>
    <input ref={input} type="file" accept=".json,.zip" hidden onChange={async event => {
      const file = event.target.files[0]; event.target.value = ''; if (!file) return;
      setBusy(true); setError(''); setStatus('正在校验和导入资源…');
      try {
        const owner = canvasAdapter.get().id;
        const imported = await importWorkflow(file, { api, canvasAdapter, position: { x: 80, y: 80 } });
        await onImported(imported, owner); setStatus(`已追加 ${imported.nodes.length} 个对象；未创建任何 Task。`);
      } catch (reason) { setError(reason.message); setStatus(''); }
      finally { setBusy(false); }
    }} />
    <Button isDisabled={busy} onPress={() => input.current.click()}>追加导入工作流</Button>
    {status && <p role="status">{status}</p>}{error && <p role="alert">{error}</p>}
  </div>;
}
