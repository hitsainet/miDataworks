/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs';
import path from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The backend is served under /api/ (ADR-013); Socket.IO answers at /api/ws/socket.io.
// The internal emit route (/internal/...) is deliberately NOT proxied (ADR-008).
// The app version shown in the sidebar foot (src/config/version.ts): package.json is the one
// source of truth, read here rather than written into a component.
const pkg = JSON.parse(readFileSync(path.resolve(__dirname, 'package.json'), 'utf8')) as { version: string };

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    port: 3100,
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: process.env.MIDATAWORKS_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  esbuild: {
    pure: mode === 'production' ? ['console.log', 'console.debug', 'console.info'] : [],
  },
  build: { outDir: 'dist', sourcemap: mode !== 'production' },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
}));
