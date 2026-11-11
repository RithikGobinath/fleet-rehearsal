import { expect, test } from '@playwright/test';

test('inventory shows loading, empty, error, and recovered evidence states', async ({ page }, info) => {
  test.skip(process.env.FLEET_EXPECT_FIXTURES === '1', 'Exercises HTTP state transitions.');
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await page.route(url => url.pathname === '/api/v1/assets', async route => {
    requests++;
    if (requests === 1) {
      await gate;
      await route.fulfill({ json: { items: [], next_cursor: null, total: 0 } });
    } else if (requests === 2) {
      await route.fulfill({ status: 503, json: { detail: 'Inventory temporarily unavailable.' } });
    } else {
      await route.continue();
    }
  });
  const screenshot = async (name: string) => {
    const path = info.outputPath(`${name}.png`);
    await page.screenshot({ path });
    await info.attach(name, { path, contentType: 'image/png' });
  };
  try {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Loading inventory...' })).toBeVisible();
    await screenshot('loading');
  } finally {
    release();
  }
  await expect(page.getByRole('heading', { name: 'No assets found' })).toBeVisible();
  await screenshot('empty');
  await page.getByRole('button', { name: 'Refresh asset inventory' }).click();
  await expect(page.getByRole('alert')).toContainText('Inventory temporarily unavailable.');
  await screenshot('error');
  await page.getByRole('button', { name: 'Retry failed request' }).click();
  const table = page.getByRole('table', { name: 'Device inventory list' });
  for (const label of ['Affected', 'Unaffected', 'Needs Evidence']) {
    await expect(table.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByRole('alert')).toHaveCount(0);
  await screenshot('recovered');
});
