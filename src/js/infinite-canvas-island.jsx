import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Excalidraw } from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';

function SpatialCanvas({ canvasDocument, onChange, onReady }) {
  const [theme, setTheme] = useState(() => (
    document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
  ));

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  const bindApi = useCallback((api) => {
    if (!api) return;
    onReady?.(api);
  }, [onReady]);

  return (
    <Excalidraw
      initialData={canvasDocument.scene}
      excalidrawAPI={bindApi}
      onChange={(elements, appState) => onChange?.({ elements, appState, files: {} })}
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
  const root = createRoot(host);
  root.render(
    <SpatialCanvas
      {...options}
      onReady={(api) => {
        canvasApi = api;
        options.onReady?.(api);
      }}
    />,
  );
  return {
    unmount: () => root.unmount(),
    updateScene: (scene) => canvasApi?.updateScene({
      elements: scene?.elements || [],
      appState: scene?.appState || {},
    }),
  };
}
