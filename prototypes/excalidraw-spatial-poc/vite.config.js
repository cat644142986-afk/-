import { defineConfig } from 'vite';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  publicDir: fileURLToPath(new URL('./node_modules/@excalidraw/excalidraw/dist/prod/fonts', import.meta.url)),
  build: {
    target: 'es2022',
    sourcemap: false,
    reportCompressedSize: true,
  },
});
