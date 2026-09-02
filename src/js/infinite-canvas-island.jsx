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
} from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';

import {
  buildSpatialNodeBatch,
  mergeSpatialNodeBatch,
  selectedSpatialBusinessElement,
  spatialBusinessKey,
  spatialLineageFocusElements,
} from './spatial-canvas-items.js';

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

function SpatialCanvas({
  canvasDocument,
  onChange,
  onOpenFineEdit,
  onReady,
  onSelectionChange,
  resolveProxyUrl,
}) {
  const [theme, setTheme] = useState(() => (
    document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
  ));
  const apiRef = useRef(null);
  const proxyFiles = useRef(new Map());
  const pointerUnsubscribe = useRef(null);
  const readyFrame = useRef(null);
  const selectionKey = useRef('');

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
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
    if (persist) onChange?.({ elements, appState, files: {} });
    const hydration = hydrateProxyFiles(elements);
    const selected = selectedSpatialBusinessElement(elements, appState);
    const nextKey = selected?.id || '';
    if (selectionKey.current !== nextKey) {
      selectionKey.current = nextKey;
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
      if (Number(event?.detail || 0) >= 2 && spatialBusinessKey(element)) {
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
      onReady?.(api, synchronizeScene);
    };
    if (readyFrame.current !== null) cancelAnimationFrame(readyFrame.current);
    notifyReady();
  }, [hydrateProxyFiles, onOpenFineEdit, onReady, synchronizeScene]);

  const handleChange = useCallback((elements, appState) => {
    synchronizeScene(elements, appState);
  }, [synchronizeScene]);

  return (
    <Excalidraw
      initialData={canvasDocument.scene}
      excalidrawAPI={bindApi}
      onChange={handleChange}
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
  const root = createRoot(host);
  root.render(
    <SpatialCanvas
      {...options}
      onReady={(api, synchronize) => {
        canvasApi = api;
        synchronizeScene = synchronize;
        componentReady = true;
        options.onReady?.(api);
      }}
    />,
  );
  return {
    unmount: () => root.unmount(),
    addBusinessItems: async (items) => {
      if (!canvasApi || !componentReady) throw new Error('Infinite canvas runtime is not ready');
      const existing = canvasApi.getSceneElementsIncludingDeleted();
      const appState = canvasApi.getAppState();
      const batch = buildSpatialNodeBatch(items, { elements: existing, appState });
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
      const focusElements = spatialLineageFocusElements(items, boundExisting, boundAdditions);
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
      return { ...batch, elements: boundAdditions };
    },
    updateScene: async (scene) => {
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
