import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  reporter: 'line',
  use: {
    baseURL: process.env.FLEET_BASE_URL ?? 'http://127.0.0.1:8080',
    channel: process.env.FLEET_BROWSER_CHANNEL,
    trace: 'retain-on-failure'
  }
});
