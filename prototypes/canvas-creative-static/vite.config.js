import { defineConfig } from 'vite';

// Isolated visual-evidence server. It intentionally leaves the production
// Vite root and dependency cache untouched.
export default defineConfig({
  root: '.',
  cacheDir: '../../build/canvas-visual-unification-evidence/.vite-cache',
  server: {
    host: '127.0.0.1',
    port: 4180,
    strictPort: true,
    fs: { allow: ['../..'] },
  },
});
