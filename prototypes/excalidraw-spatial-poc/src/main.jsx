import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Excalidraw, convertToExcalidrawElements } from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';
import './styles.css';
import {
  POC_STORAGE_KEY,
  createPocSceneSkeletons,
  restorePocScene,
  sceneContentFingerprint,
  sceneMetrics,
  serializePocScene,
} from './scene-model.js';

const startedAt = performance.now();

function loadInitialScene() {
  if (new URLSearchParams(window.location.search).get('reset') === '1') {
    localStorage.removeItem(POC_STORAGE_KEY);
  }
  const stored = localStorage.getItem(POC_STORAGE_KEY);
  if (stored) {
    try {
      return { ...restorePocScene(stored), restored: true };
    } catch (error) {
      console.warn('Discarding invalid PoC scene', error);
      localStorage.removeItem(POC_STORAGE_KEY);
    }
  }
  const model = createPocSceneSkeletons();
  return {
    elements: convertToExcalidrawElements(model.skeletons, { regenerateIds: false }),
    files: model.files,
    appState: {
      viewBackgroundColor: '#d4d0cb',
      currentItemRoughness: 0,
      currentItemStrokeStyle: 'solid',
      currentItemFillStyle: 'solid',
      gridSize: 20,
      gridStep: 5,
      gridModeEnabled: false,
      zoom: { value: 0.2 },
      scrollX: 80,
      scrollY: 80,
    },
    restored: false,
  };
}

function VideoNode({ element, selected, playing }) {
  const data = element.customData || {};
  const size = `${data.pixel_width || 0} × ${data.pixel_height || 0}`;
  return (
    <div
      className={`video-node${selected ? ' is-selected' : ''}`}
      data-video-id={element.id}
      data-video-loaded={selected || playing ? 'true' : 'false'}
      data-video-playing={playing ? 'true' : 'false'}
    >
      <div className="video-node__cover" aria-hidden="true">
        <span>{playing ? 'PREVIEW' : 'VIDEO'}</span>
      </div>
      <div className="video-node__meta">
        <strong>视频 {String(data.asset_id || '').slice(-3)}</strong>
        <span>{data.duration_seconds}s · {size}</span>
        <span className={`video-node__status is-${data.status || 'ready'}`}>
          {playing ? '播放中' : data.status === 'queued' ? '排队中' : '可预览'}
        </span>
      </div>
    </div>
  );
}

function App() {
  const initial = useMemo(loadInitialScene, []);
  const apiRef = useRef(null);
  const latestRef = useRef({ elements: initial.elements, appState: initial.appState, files: initial.files });
  const saveTimerRef = useRef(null);
  const metricTimerRef = useRef(null);
  const lastObservedFingerprintRef = useRef(sceneContentFingerprint(initial.elements, initial.appState));
  const [playingIds, setPlayingIds] = useState(() => new Set());
  const [selectedVideoId, setSelectedVideoId] = useState(null);
  const [saveState, setSaveState] = useState(initial.restored ? '已恢复' : '未保存');
  const [metrics, setMetrics] = useState(() => sceneMetrics(initial.elements, initial.files));
  const [readyMs, setReadyMs] = useState(null);

  const persist = useCallback(() => {
    const current = latestRef.current;
    lastObservedFingerprintRef.current = sceneContentFingerprint(current.elements, current.appState);
    const payload = serializePocScene(current.elements, current.appState);
    const serialized = JSON.stringify(payload);
    localStorage.setItem(POC_STORAGE_KEY, serialized);
    setSaveState(`已保存 · ${(serialized.length / 1024).toFixed(1)} KB`);
    return payload;
  }, []);

  const schedulePersist = useCallback((elements, appState, files) => {
    latestRef.current = { elements, appState, files };
    const selectedVideo = elements.find((element) => (
      appState.selectedElementIds?.[element.id]
      && element.type === 'embeddable'
      && element.customData?.node_kind === 'video'
    ));
    setSelectedVideoId(selectedVideo?.id || null);
    const fingerprint = sceneContentFingerprint(elements, appState);
    if (fingerprint === lastObservedFingerprintRef.current) return;
    lastObservedFingerprintRef.current = fingerprint;
    setSaveState('保存中');
    window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(persist, 420);
    window.clearTimeout(metricTimerRef.current);
    metricTimerRef.current = window.setTimeout(() => setMetrics(sceneMetrics(elements, files)), 160);
  }, [persist]);

  const bindApi = useCallback((api) => {
    apiRef.current = api;
    window.setTimeout(() => {
      setReadyMs(performance.now() - startedAt);
      document.documentElement.dataset.pocReady = 'true';
    }, 240);
  }, []);

  const toggleVideo = useCallback((id) => {
    setPlayingIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const renderEmbeddable = useCallback((element, appState) => (
    <VideoNode
      element={element}
      selected={Boolean(appState.selectedElementIds?.[element.id])}
      playing={playingIds.has(element.id)}
    />
  ), [playingIds]);

  const reset = useCallback(() => {
    localStorage.removeItem(POC_STORAGE_KEY);
    window.location.replace(`${window.location.pathname}?reset=1`);
  }, []);

  const selectVideoSample = useCallback(() => {
    setSelectedVideoId('video-poc-001');
    apiRef.current?.updateScene({
      appState: { selectedElementIds: { 'video-poc-001': true } },
    });
  }, []);

  const toggleSelectedVideo = useCallback(() => {
    if (selectedVideoId) toggleVideo(selectedVideoId);
  }, [selectedVideoId, toggleVideo]);

  useEffect(() => {
    const flush = () => {
      window.clearTimeout(saveTimerRef.current);
      try { persist(); } catch (error) { console.error(error); }
    };
    window.addEventListener('beforeunload', flush);
    return () => {
      window.removeEventListener('beforeunload', flush);
      window.clearTimeout(saveTimerRef.current);
      window.clearTimeout(metricTimerRef.current);
    };
  }, [persist]);

  useEffect(() => {
    window.__PA_POC__ = {
      forceSave: persist,
      reset,
      getMetrics: () => ({
        ...sceneMetrics(latestRef.current.elements, latestRef.current.files),
        readyMs,
        storageBytes: localStorage.getItem(POC_STORAGE_KEY)?.length || 0,
        loadedVideos: document.querySelectorAll('[data-video-loaded="true"]').length,
        playingVideos: document.querySelectorAll('[data-video-playing="true"]').length,
        renderedVideos: document.querySelectorAll('[data-video-id]').length,
        viewport: [window.innerWidth, window.innerHeight],
        overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        runtime: 'excalidraw-0.18.1',
      }),
      selectVideo: (id = 'video-poc-001') => apiRef.current?.updateScene({
        appState: { selectedElementIds: { [id]: true } },
      }),
    };
    return () => { delete window.__PA_POC__; };
  }, [persist, readyMs, reset]);

  const sampleElement = latestRef.current.elements.find((element) => element.id === 'image-poc-001');
  const dragSampleElement = latestRef.current.elements.find((element) => element.id === 'image-poc-046');
  const interactiveRectangle = latestRef.current.elements.findLast?.((element) => element.type === 'rectangle' && !element.isDeleted)
    || [...latestRef.current.elements].reverse().find((element) => element.type === 'rectangle' && !element.isDeleted);
  const isSelectedVideoPlaying = Boolean(selectedVideoId && playingIds.has(selectedVideoId));

  return (
    <main className="poc-shell">
      <header className="poc-header">
        <div className="poc-title">
          <span className="poc-mark" aria-hidden="true"><i /></span>
          <div>
            <p>PRODUCT ATELIER · ISOLATED POC</p>
            <h1>无限画布工作区</h1>
          </div>
        </div>
        <div className="poc-actions">
          <span className="poc-runtime">Excalidraw 0.18.1 · {readyMs == null ? '加载中' : `${readyMs.toFixed(0)} ms`}</span>
          <button type="button" className="is-secondary" onClick={selectVideoSample}>选择视频样例</button>
          <button type="button" className="is-secondary" onClick={toggleSelectedVideo} disabled={!selectedVideoId}>
            {isSelectedVideoPlaying ? '停止视频样例' : '播放视频样例'}
          </button>
          <button type="button" onClick={persist}>保存</button>
          <button type="button" className="is-secondary" onClick={reset}>重置</button>
        </div>
      </header>

      <section className="poc-stage" aria-label="无限画布隔离验证">
        <Excalidraw
          initialData={{ elements: initial.elements, appState: initial.appState, files: initial.files }}
          excalidrawAPI={bindApi}
          onChange={schedulePersist}
          renderEmbeddable={renderEmbeddable}
          validateEmbeddable={(link) => String(link).startsWith('product-atelier-video://')}
          langCode="zh-CN"
          name="Product Atelier Spatial PoC"
          theme="light"
          autoFocus
          handleKeyboardGlobally={false}
          objectsSnapModeEnabled
          UIOptions={{
            canvasActions: {
              changeViewBackgroundColor: false,
              clearCanvas: false,
              export: false,
              loadScene: false,
              saveToActiveFile: false,
              saveAsImage: false,
              toggleTheme: false,
            },
            tools: { image: true },
          }}
        />
      </section>

      <footer className="poc-status" aria-live="polite">
        <span>{metrics.images} 张代理图</span>
        <span>{metrics.videos} 个视频封面</span>
        <span>{metrics.frames} 个 Frame</span>
        <span>{metrics.lineage} 条结果血缘</span>
        <strong>{saveState}</strong>
        <output
          className="sr-only"
          data-testid="poc-metrics"
          data-restored={initial.restored ? 'true' : 'false'}
          data-metrics={JSON.stringify({
            ...metrics,
            readyMs,
            sampleX: sampleElement?.x ?? null,
            sampleY: sampleElement?.y ?? null,
            dragSampleX: dragSampleElement?.x ?? null,
            dragSampleY: dragSampleElement?.y ?? null,
            selectedElementIds: Object.keys(latestRef.current.appState.selectedElementIds || {}),
            interactiveRectangle: interactiveRectangle ? {
              id: interactiveRectangle.id,
              x: interactiveRectangle.x,
              y: interactiveRectangle.y,
              width: interactiveRectangle.width,
              height: interactiveRectangle.height,
            } : null,
            viewportState: {
              scrollX: latestRef.current.appState.scrollX,
              scrollY: latestRef.current.appState.scrollY,
              zoom: latestRef.current.appState.zoom?.value,
              offsetLeft: latestRef.current.appState.offsetLeft,
              offsetTop: latestRef.current.appState.offsetTop,
            },
            selectedVideoId,
            playingVideos: playingIds.size,
          })}
        />
      </footer>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
