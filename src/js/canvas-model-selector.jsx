import React from 'react';
import { admittedComposerModel, eligibleComposerModels } from './canvas-model-admission.js';

function seconds(milliseconds) {
  const value = Number(milliseconds || 0) / 1000;
  return value > 0 ? `${value.toFixed(1)} 秒` : '—';
}

export function CanvasModelSelector({ admission, value, onChange, loading = false, error = '' }) {
  const models = eligibleComposerModels(admission);
  const selected = admittedComposerModel(admission, value);
  const unsupported = !loading && models.length === 0;
  return <div className="pa-model-selector" aria-label="生成模型">
    <label>
      <span className="sr-only">生成模型</span>
      <select
        aria-label="生成模型"
        value={selected ? value : ''}
        disabled={loading || unsupported}
        onChange={(event) => onChange(event.target.value)}
      >
        {loading && <option value="">正在核对模型…</option>}
        {!loading && unsupported && <option value="">当前参数暂无可用模型</option>}
        {!loading && !unsupported && !selected && <option value="">选择模型</option>}
        {models.map((model) => <option key={model.provider_model_id} value={model.provider_model_id}>
          {model.display_name || model.provider_model_id}
        </option>)}
      </select>
    </label>
    {selected && <details className="pa-model-selector__details">
      <summary aria-label="查看模型证据">详情</summary>
      <div>
        <strong>{selected.display_name || selected.provider_model_id}</strong>
        <dl>
          <div><dt>模型 ID</dt><dd>{selected.provider_model_id}</dd></div>
          <div><dt>适配器</dt><dd>{selected.adapter?.contract} · {selected.adapter?.version}</dd></div>
          <div><dt>Provider</dt><dd>LK / AI模型中心</dd></div>
          <div><dt>验证路由</dt><dd>{selected.evidence?.provider_canary?.provider_route?.submit} → {selected.evidence?.provider_canary?.provider_route?.poll}</dd></div>
          <div><dt>单次 canary</dt><dd>{seconds(selected.telemetry?.provider_elapsed_ms)} · {selected.telemetry?.billing?.cost ?? '—'} {selected.telemetry?.billing?.unit || ''}</dd></div>
          <div><dt>通道证据</dt><dd>{selected.telemetry?.channel_group || '—'}</dd></div>
        </dl>
        <small>以上为单次验证证据，不代表质量、速度或成本排名。</small>
      </div>
    </details>}
    {error && <small className="pa-model-selector__error" role="status">{error}</small>}
  </div>;
}
