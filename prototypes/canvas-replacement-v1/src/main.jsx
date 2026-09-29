import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlowProvider } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './styles.css';
import { CanvasFoundationPrototype } from './prototype.jsx';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ReactFlowProvider>
      <CanvasFoundationPrototype />
    </ReactFlowProvider>
  </React.StrictMode>,
);
