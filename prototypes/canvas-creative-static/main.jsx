import React, { useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../../src/css/stable-ui.css';
import { CanvasTransplantShell } from '../../src/js/canvas-transplant-shell.jsx';
import './preview.css';

// Screenshot fixture only: actual transplanted components, synthetic content,
// no Task, Provider, packaged runtime or durable asset writes.
function thumbnail(color, label) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600"><rect width="600" height="600" fill="#f4f0e9"/><rect x="180" y="88" width="240" height="424" rx="8" fill="${color}"/><text x="300" y="295" fill="white" font-family="Arial" font-size="44" font-weight="700" text-anchor="middle">${label}</text><text x="300" y="340" fill="white" font-family="Arial" font-size="18" text-anchor="middle">VISUAL STUDY</text></svg>`)}`;
}

const images = [
  { id: 'source-element', type: 'image', x: 270, y: 185, width: 225, height: 225, angle: 0, isDeleted: false, fileId: 'source-file', customData: { asset_id: 'source-asset', result_id: '' } },
  { id: 'result-element', type: 'image', x: 665, y: 185, width: 225, height: 225, angle: 0, isDeleted: false, fileId: 'result-file', customData: { asset_id: 'result-asset', result_id: 'result-asset' } },
];
const files = {
  'source-file': { dataURL: thumbnail('#70889b', 'SOURCE') },
  'result-file': { dataURL: thumbnail('#c56f4d', 'RESULT') },
};
const assets = {
  'source-element': { id: 'source-asset', name: '包装原始素材', role: 'workspace_source', mime: 'image/png' },
  'result-element': { id: 'result-asset', name: '方案 A · Result', role: 'result_image', mime: 'image/png' },
};

function Fixture() {
  const [selectedId, setSelectedId] = useState('source-element');
  const [tool, setTool] = useState('selection');
  const appState = { scrollX: 0, scrollY: 0, offsetLeft: 20, offsetTop: 62, zoom: { value: 1 }, activeTool: { type: tool }, selectedElementIds: { [selectedId]: true } };
  const api = useMemo(() => ({
    getFiles: () => files,
    getSceneElementsIncludingDeleted: () => images,
    getAppState: () => appState,
    setActiveTool: ({ type }) => setTool(type),
    updateScene: ({ appState: patch }) => {
      const nextId = Object.keys(patch?.selectedElementIds || {})[0];
      if (nextId) setSelectedId(nextId);
    },
  }), [selectedId, tool]);
  const selected = images.filter((element) => element.id === selectedId);
  const business = { elementId: selectedId, asset: assets[selectedId], conversationInput: '', reviewing: false };
  return <div className="fixture-window">
    <div className="fixture-titlebar"><strong>Product Atelier</strong><span>Canvas / 创作现场</span><small>静态组件预览 · 非 packaged</small></div>
    <div id="spatial-canvas-host" className="fixture-canvas">
      <div className="fixture-grid" />
      {images.map((element) => <img key={element.id} src={files[element.fileId].dataURL} alt="合成示例素材" className={`fixture-object${element.id === selectedId ? ' is-selected' : ''}`} style={{ left: element.x, top: element.y, width: element.width, height: element.height }} />)}
      <CanvasTransplantShell api={api} view={{ selected, appState }} business={business} onTool={setTool} onReferenceReview={() => {}} />
    </div>
  </div>;
}

createRoot(document.getElementById('root')).render(<Fixture />);

const state = new URLSearchParams(location.search).get('state');
if (state === 'picker' || state === 'tray' || state === 'controls') {
  setTimeout(() => document.querySelector('.pa-reference-tray__add')?.click(), 500);
  if (state === 'tray' || state === 'controls') {
    setTimeout(() => document.querySelector('.pa-reference-picker__list button:nth-child(2)')?.click(), 850);
    setTimeout(() => document.querySelector('.pa-reference-picker__confirm')?.click(), 1100);
    setTimeout(() => document.querySelector('.pa-transplant__composer textarea')?.focus(), 1450);
  }
  if (state === 'controls') setTimeout(() => document.querySelector('.pa-reference-controls__button')?.click(), 1850);
}
