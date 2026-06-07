/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@backend-types': path.resolve(__dirname, '../src/types')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/analysis': 'http://127.0.0.1:3000'
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './vitest.setup.ts',
  }
});
