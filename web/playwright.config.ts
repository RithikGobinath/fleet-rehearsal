import { defineConfig } from '@playwright/test';

const fixtures = process.env.FLEET_EXPECT_FIXTURES === '1';
const baseURL = fixtures ? 'http://127.0.0.1:4200' : process.env.FLEET_BASE_URL ?? 'http://127.0.0.1:8080';

export default defineConfig({
  testDir: './e2e',
  reporter: 'line',
  webServer: fixtures ? { command: 'npm run dev:fixtures', url: baseURL, timeout: 60_000 } : undefined,
  use: {
    baseURL,
    channel: process.env.FLEET_BROWSER_CHANNEL,
    trace: process.env.FLEET_RECORD_DEMO === '1' ? 'on' : 'retain-on-failure',
    video: process.env.FLEET_RECORD_DEMO === '1' ? 'on' : 'off'
  }
});
