import { defineConfig, devices } from '@playwright/test';

// The shell walk (task 12.12): every registered screen in light and dark mode against the built
// app, with the API answered by route fixtures (e2e/fixtures.ts) so the run needs no backend.
// E2E_PORT lets a second checkout run beside another (the standalone audit ran on 4213).
const PORT = process.env.E2E_PORT ?? '4173';

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'off' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npx vite build && npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
