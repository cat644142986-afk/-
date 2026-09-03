import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import {
  CaptureUpdateAction,
  Excalidraw,
  convertToExcalidrawElements,
  newElementWith,
} from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';

import {
  buildSpatialNodeBatch,
  mergeSpatialNodeBatch,
  selectedSpatialBusinessElement,
  spatialBusinessKey,
  spatialLineageFocusElements,
  updateSpatialTaskElements,
  uniqueSpatialBusinessItems,
} from './spatial-canvas-items.js';
import {
  createSpatialVideoPlaybackState,
  pauseSpatialVideo,
  playSpatialVideo,
  selectSpatialVideo,
  spatialVideoNodeRuntime,
  stopSpatialVideoPlayback,
  switchSpatialVideoCanvas,
} from './spatial-video.js';

const SPATIAL_VIDEO_LINK = /^product-atelier-video:\/\/[A-Za-z0-9:_-]{3,160}$/;

function videoPresentation(element, value = {}) {
  const metadata = value?.metadata && typeof value.metadata === 'object' ? value.metadata : {};
  const assetId = String(element?.customData?.asset_id || '');
  const duration = Number(
    value?.duration_seconds
      ?? value?.durationSeconds
      ?? metadata.duration_seconds
      ?? metadata.durationSeconds
      ?? 0,
  );
  return {
    name: String(value?.name || `视频 ${assetId.slice(-8) || '结果'}`),
    coverUrl: String(value?.cover_url || value?.coverUrl || value?.thumbnail_url || value?.thumbnailUrl || ''),
    streamUrl: String(value?.stream_url || value?.streamUrl || value?.content_url || value?.contentUrl || ''),
    durationSeconds: Number.isFinite(duration) && duration > 0 ? duration : null,
    width: Number(value?.width || metadata.pixel_width || 0) || null,
    height: Number(value?.height || metadata.pixel_height || 0) || null,
    status: String(value?.status || metadata.status || 'ready'),
  };
}

function videoDuration(value) {
  if (!Number.isFinite(value) || value <= 0) return '时长待读取';
  return `${Number(value).toFixed(Number(value) % 1 ? 1 : 0)} 秒`;
}

function SpatialVideoNode({
  element,
  loaded,
  playing,
  resolveVideoAsset,
  onPlaybackStart,
  onPlaybackPause,
  onPlaybackEnd,
  onPlaybackError,
}) {
  const videoRef = useRef(null);
  const playbackErrorRef = useRef(onPlaybackError);
  const [presentation, setPresentation] = useState(() => videoPresentation(element));
  playbackErrorRef.current = onPlaybackError;
  const failPlayback = useCallback(() => {
    setPresentation((current) => ({ ...current, streamUrl: '' }));
    playbackErrorRef.current?.();
  }, []);

  useEffect(() => {
    let canceled = false;
    const assetId = String(element?.customData?.asset_id || '');
    setPresentation((current) => videoPresentation(element, current));
    if (!assetId || typeof resolveVideoAsset !== 'function') return () => { canceled = true; };
    Promise.resolve(resolveVideoAsset(assetId, { loadStream: loaded }))
      .then((value) => {
        if (canceled) return;
        const nextPresentation = videoPresentation(element, value);
        setPresentation(nextPresentation);
        if (loaded && !nextPresentation.streamUrl) failPlayback();
      })
      .catch(() => {
        if (!canceled && loaded) failPlayback();
      });
    return () => { canceled = true; };
  }, [element, failPlayback, loaded, resolveVideoAsset]);

  const videoSrc = loaded ? presentation.streamUrl : '';
  const playbackActive = playing && Boolean(videoSrc);
  useEffect(() => {
    let canceled = false;
    const video = videoRef.current;
    if (!video || !videoSrc) return undefined;
    if (playing) video.play().catch(() => { if (!canceled) failPlayback(); });
    else video.pause();
    return () => {
      canceled = true;
      video.pause();
    };
  }, [failPlayback, playing, videoSrc]);

  const dimensions = presentation.width && presentation.height
    ? `${presentation.width} × ${presentation.height}`
    : '尺寸待读取';
  const statusCopy = playbackActive ? '播放中' : presentation.status === 'ready' ? '可预览' : presentation.status;
  return (
    <div
      className={`spatial-video-node${loaded ? ' is-loaded' : ''}${playbackActive ? ' is-playing' : ''}`}
      data-video-id={element.id}
      data-video-loaded={loaded ? 'true' : 'false'}
      data-video-playing={playbackActive ? 'true' : 'false'}
    >
      <div className="spatial-video-node__cover" aria-hidden="true">
        {presentation.coverUrl ? <img src={presentation.coverUrl} alt="" draggable={false} /> : <span>VIDEO</span>}
        {videoSrc ? (
          <video
            ref={videoRef}
            src={videoSrc}
            preload="metadata"
            controls
            controlsList="nodownload noremoteplayback"
            disablePictureInPicture
            playsInline
            onPlay={onPlaybackStart}
            onPause={onPlaybackPause}
            onEnded={onPlaybackEnd}
            onError={failPlayback}
          />
        ) : null}
        <i>{playbackActive ? '播放中' : '视频'}</i>
      </div>
      <div className="spatial-video-node__meta">
        <strong>{presentation.name}</strong>
        <span>{videoDuration(presentation.durationSeconds)} · {dimensions}</span>
        <small>{statusCopy}</small>
      </div>
    </div>
  );
}

async function waitForVisibleCanvasViewport(api, host, maxFrames = 60) {
  let readyFrames = 0;
  for (let frame = 0; frame < maxFrames; frame += 1) {
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const bounds = host.getBoundingClientRect();
    const appState = api.getAppState();
    const ready = !host.hidden
      && bounds.width > 1
      && bounds.height > 1
      && Number(appState?.width || 0) > 1
      && Number(appState?.height || 0) > 1;
    readyFrames = ready ? readyFrames + 1 : 0;
    if (readyFrames >= 2) return true;
  }
  return false;
}

const THEMED_CANVAS_BACKGROUNDS = Object.freeze({
  light: '#d4d0cb',
  dark: '#cfd3d7',
});

function themedCanvasBackground(value, theme) {
  const current = String(value || '').trim().toLowerCase();
  const usesProductDefault = !current
    || Object.values(THEMED_CANVAS_BACKGROUNDS).includes(current);
  return usesProductDefault
    ? THEMED_CANVAS_BACKGROUNDS[theme === 'dark' ? 'dark' : 'light']
    : value;
}

function runtimeSceneForTheme(scene, theme) {
  const source = scene && typeof scene === 'object' ? scene : {};
  const appState = source.appState || source.app_state || {};
  return {
    ...source,
    appState: {
      ...appState,
      viewBackgroundColor: themedCanvasBackground(appState.viewBackgroundColor, theme),
    },
  };
}

function persistentAppState(appState) {
  const source = appState && typeof appState === 'object' ? appState : {};
  const current = String(source.viewBackgroundColor || '').trim().toLowerCase();
  if (!Object.values(THEMED_CANVAS_BACKGROUNDS).includes(current)) return source;
  return { ...source, viewBackgroundColor: THEMED_CANVAS_BACKGROUNDS.light };
}

function SpatialCanvas({
  canvasDocument,
  onChange,
  onOpenFineEdit,
  onReady,
  onSelectionChange,
  resolveProxyUrl,
  resolveVideoAsset,
}) {
  const [theme, setTheme] = useState(() => (
    document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
  ));
  const [videoPlayback, setVideoPlayback] = useState(() => (
    createSpatialVideoPlaybackState(canvasDocument?.id)
  ));
  const apiRef = useRef(null);
  const proxyFiles = useRef(new Map());
  const pointerUnsubscribe = useRef(null);
  const readyFrame = useRef(null);
  const selectionKey = useRef('');

  useEffect(() => {
    setVideoPlayback((current) => switchSpatialVideoCanvas(current, canvasDocument?.id));
  }, [canvasDocument?.id]);

  const videoControls = React.useMemo(() => ({
    play: (elementId) => setVideoPlayback((current) => playSpatialVideo(current, elementId)),
    pause: (elementId) => setVideoPlayback((current) => pauseSpatialVideo(current, elementId)),
    stop: () => setVideoPlayback((current) => stopSpatialVideoPlayback(current)),
    toggle: (elementId) => setVideoPlayback((current) => (
      current.playingId === String(elementId || '')
        ? pauseSpatialVideo(current, elementId)
        : playSpatialVideo(current, elementId)
    )),
  }), []);

  useEffect(() => {
    const observer = new MutationObserver(() => {
      const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
      setTheme(nextTheme);
      const api = apiRef.current;
      if (!api) return;
      const appState = api.getAppState();
      const viewBackgroundColor = themedCanvasBackground(appState?.viewBackgroundColor, nextTheme);
      if (viewBackgroundColor !== appState?.viewBackgroundColor) {
        api.updateScene({ appState: { viewBackgroundColor } });
      }
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  const hydrateProxyFiles = useCallback(async (elements) => {
    const api = apiRef.current;
    if (!api || typeof resolveProxyUrl !== 'function') return;
    const candidates = Array.from(elements || []).filter((element) => (
      !element?.isDeleted
      && element.type === 'image'
      && element.fileId
      && element.customData?.asset_id
      && !proxyFiles.current.has(element.fileId)
    ));
    await Promise.all(candidates.map(async (element) => {
      proxyFiles.current.set(element.fileId, null);
      try {
        const url = await resolveProxyUrl(element.customData.asset_id);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        proxyFiles.current.set(element.fileId, objectUrl);
        api.addFiles([{
          id: element.fileId,
          dataURL: objectUrl,
          mimeType: blob.type?.startsWith('image/') ? blob.type : 'image/jpeg',
          created: Date.now(),
        }]);
      } catch (_) {
        proxyFiles.current.delete(element.fileId);
      }
    }));
  }, [resolveProxyUrl]);

  useEffect(() => () => {
    pointerUnsubscribe.current?.();
    if (readyFrame.current !== null) cancelAnimationFrame(readyFrame.current);
    proxyFiles.current.forEach((url) => { if (url) URL.revokeObjectURL(url); });
    proxyFiles.current.clear();
  }, []);

  const synchronizeScene = useCallback(async (elements, appState, { persist = true } = {}) => {
    if (persist) onChange?.({ elements, appState: persistentAppState(appState), files: {} });
    const hydration = hydrateProxyFiles(elements);
    const selected = selectedSpatialBusinessElement(elements, appState);
    const nextKey = selected?.id || '';
    if (selectionKey.current !== nextKey) {
      selectionKey.current = nextKey;
      const selectedVideoId = selected?.type === 'embeddable'
        && SPATIAL_VIDEO_LINK.test(String(selected?.link || ''))
        ? selected.id
        : '';
      setVideoPlayback((current) => (
        selectedVideoId
          ? selectSpatialVideo(current, selectedVideoId)
          : stopSpatialVideoPlayback(current)
      ));
      onSelectionChange?.(selected || null);
    }
    await hydration;
  }, [hydrateProxyFiles, onChange, onSelectionChange]);

  const bindApi = useCallback((api) => {
    if (!api) return;
    apiRef.current = api;
    pointerUnsubscribe.current?.();
    pointerUnsubscribe.current = api.onPointerDown?.((_activeTool, pointerDownState, event) => {
      const element = pointerDownState?.hit?.element;
      if (Number(event?.detail || 0) >= 2 && element?.type === 'image' && spatialBusinessKey(element)) {
        onOpenFineEdit?.(element);
      }
    });
    hydrateProxyFiles(api.getSceneElementsIncludingDeleted());
    const notifyReady = () => {
      if (apiRef.current !== api) return;
      if (api.getAppState()?.isLoading) {
        readyFrame.current = requestAnimationFrame(notifyReady);
        return;
      }
      readyFrame.current = null;
      onReady?.(api, synchronizeScene, videoControls);
    };
    if (readyFrame.current !== null) cancelAnimationFrame(readyFrame.current);
    notifyReady();
  }, [hydrateProxyFiles, onOpenFineEdit, onReady, synchronizeScene, videoControls]);

  const handleChange = useCallback((elements, appState) => {
    synchronizeScene(elements, appState);
  }, [synchronizeScene]);

  const validateVideoEmbeddable = useCallback((link) => (
    SPATIAL_VIDEO_LINK.test(String(link || ''))
  ), []);

  const renderVideoEmbeddable = useCallback((element) => {
    const runtime = spatialVideoNodeRuntime(videoPlayback, element.id);
    return (
      <SpatialVideoNode
        element={element}
        loaded={runtime.loaded}
        playing={runtime.playing}
        resolveVideoAsset={resolveVideoAsset}
        onPlaybackStart={() => videoControls.play(element.id)}
        onPlaybackPause={() => videoControls.pause(element.id)}
        onPlaybackEnd={() => videoControls.pause(element.id)}
        onPlaybackError={() => videoControls.stop()}
      />
    );
  }, [resolveVideoAsset, videoControls, videoPlayback]);

  return (
    <Excalidraw
      initialData={runtimeSceneForTheme(canvasDocument.scene, theme)}
      excalidrawAPI={bindApi}
      onChange={handleChange}
      renderEmbeddable={renderVideoEmbeddable}
      validateEmbeddable={validateVideoEmbeddable}
      langCode="zh-CN"
      name={canvasDocument.name}
      theme={theme}
      aiEnabled={false}
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
        tools: { image: false },
      }}
    />
  );
}

export function mountInfiniteCanvas(host, options) {
  let canvasApi = null;
  let componentReady = false;
  let synchronizeScene = null;
  let videoControls = null;
  const root = createRoot(host);
  root.render(
    <SpatialCanvas
      {...options}
      onReady={(api, synchronize, controls) => {
        canvasApi = api;
        synchronizeScene = synchronize;
        videoControls = controls;
        componentReady = true;
        options.onReady?.(api);
      }}
    />,
  );

  async function insertBusinessItems(items, { once = false } = {}) {
    if (!canvasApi || !componentReady) throw new Error('Infinite canvas runtime is not ready');
    const existing = canvasApi.getSceneElementsIncludingDeleted();
    const normalized = once ? uniqueSpatialBusinessItems(items, existing) : Array.from(items || []);
    if (!normalized.length) {
      return {
        skipped: true,
        skeletons: [],
        proxyRequests: [],
        nodeIds: [],
        lineageBindings: [],
        elements: [],
      };
    }
    const appState = canvasApi.getAppState();
    const batch = buildSpatialNodeBatch(normalized, { elements: existing, appState });
    const additions = convertToExcalidrawElements(batch.skeletons, { regenerateIds: false });
    const selectedElementIds = Object.fromEntries(batch.nodeIds.map((id) => [id, true]));
    const nextElements = mergeSpatialNodeBatch(existing, additions, batch.lineageBindings);
    const boundExisting = nextElements.slice(0, existing.length);
    const boundAdditions = nextElements.slice(existing.length);
    const nextAppState = { ...appState, selectedElementIds };
    canvasApi.updateScene({
      elements: nextElements,
      appState: { selectedElementIds },
      captureUpdate: CaptureUpdateAction.IMMEDIATELY,
    });
    await synchronizeScene?.(nextElements, nextAppState);
    const inserted = boundAdditions.filter((element) => batch.nodeIds.includes(element.id));
    const focusElements = spatialLineageFocusElements(normalized, boundExisting, boundAdditions);
    const viewportReady = focusElements.length
      ? await waitForVisibleCanvasViewport(canvasApi, host)
      : false;
    if (viewportReady) {
      canvasApi.scrollToContent(focusElements, {
        animate: false,
        fitToContent: focusElements.length > inserted.length,
      });
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await synchronizeScene?.(
        canvasApi.getSceneElementsIncludingDeleted(),
        canvasApi.getAppState(),
      );
    }
    return { ...batch, elements: boundAdditions, skipped: false };
  }

  async function updateTask(item) {
    if (!canvasApi || !componentReady) return { changed: false, taskElement: null };
    const current = canvasApi.getSceneElementsIncludingDeleted();
    const update = updateSpatialTaskElements(current, item, (element, changes) => (
      newElementWith(element, changes)
    ));
    if (!update.changed) return update;
    const appState = canvasApi.getAppState();
    canvasApi.updateScene({
      elements: update.elements,
      captureUpdate: CaptureUpdateAction.IMMEDIATELY,
    });
    await synchronizeScene?.(update.elements, appState);
    return update;
  }

  return {
    unmount: () => {
      videoControls?.stop?.();
      root.unmount();
    },
    playVideo: (elementId) => videoControls?.play?.(elementId),
    stopVideo: () => videoControls?.stop?.(),
    toggleVideo: (elementId) => videoControls?.toggle?.(elementId),
    addBusinessItems: (items) => insertBusinessItems(items),
    addBusinessItemsOnce: (items) => insertBusinessItems(items, { once: true }),
    updateTask,
    getBusinessKeys: () => new Set(
      Array.from(canvasApi?.getSceneElementsIncludingDeleted?.() || [])
        .filter((element) => !element?.isDeleted)
        .map(spatialBusinessKey)
        .filter(Boolean),
    ),
    getScene: () => ({
      elements: canvasApi?.getSceneElementsIncludingDeleted?.() || [],
      appState: canvasApi?.getAppState?.() || {},
      files: {},
    }),
    selectBusinessReference: async (references = {}) => {
      if (!canvasApi || !componentReady) return null;
      const elements = canvasApi.getSceneElementsIncludingDeleted();
      const target = elements.find((element) => {
        if (element?.isDeleted) return false;
        const refs = element?.customData || {};
        return (references.task_id && refs.task_id === references.task_id)
          || (references.result_id && refs.result_id === references.result_id)
          || (references.asset_id && refs.asset_id === references.asset_id);
      });
      if (!target) return null;
      const selectedElementIds = { [target.id]: true };
      canvasApi.updateScene({
        appState: { selectedElementIds },
        captureUpdate: CaptureUpdateAction.NEVER,
      });
      canvasApi.scrollToContent([target], { animate: false, fitToContent: false });
      await synchronizeScene?.(elements, { ...canvasApi.getAppState(), selectedElementIds }, { persist: false });
      return target;
    },
    updateScene: async (scene) => {
      videoControls?.stop?.();
      const elements = scene?.elements || [];
      const appState = scene?.appState || {};
      canvasApi?.updateScene({
        elements,
        appState,
        captureUpdate: CaptureUpdateAction.NEVER,
      });
      await synchronizeScene?.(elements, appState, { persist: false });
    },
  };
}
