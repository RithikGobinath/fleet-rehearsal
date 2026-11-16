import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';

const scope = '/api/v1/tenants/northstar/sites/harbor';

function control(...args: string[]): void {
  if (process.env.FLEET_TEST_CONTROL) {
    execFileSync(process.execPath, [process.env.FLEET_TEST_CONTROL, ...args], { timeout: 20_000 });
  } else {
    execFileSync('docker', ['compose', 'exec', '-T', '-e', 'FLEET_KAFKA_BOOTSTRAP=kafka:9092',
      'api', 'python', '-m', ...args], { timeout: 25_000 });
  }
}

async function role(page: Page, principal: string, label: string): Promise<void> {
  await page.getByRole('combobox', { name: 'Select synthetic operator identity' }).selectOption(principal);
  await expect(page.getByText(`Role: ${label}`, { exact: false })).toBeVisible();
}

async function approve(page: Page, planId: string): Promise<void> {
  await role(page, 'demo-partner', 'partner proposer');
  await page.getByRole('textbox', { name: 'Plan ID' }).fill(planId);
  await page.getByRole('button', { name: 'Create preview' }).click();
  await expect(page.getByRole('heading', { name: `Plan ${planId} · revision 1` })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Wave 1 · Canary' })).toBeVisible();
  await expect(page.getByText('harbor-03 · missing_evidence', { exact: true })).toBeVisible();
  await role(page, 'demo-approver', 'customer approver');
  await page.getByRole('button', { name: 'Load revision' }).click();
  await page.getByRole('button', { name: 'Approve revision' }).click();
  await expect(page.getByText('Approved by demo-approver', { exact: false })).toBeVisible();
  await role(page, 'demo-northstar', 'operator');
  await page.getByRole('button', { name: 'Load revision' }).click();
  await expect(page.getByRole('button', { name: 'Start wave 1', exact: true })).toBeEnabled();
}

test('approves and rehearses a canary, then blocks a separate approved wave after its peer goes down', async ({ page }, info) => {
  test.skip(process.env.FLEET_EXPECT_FIXTURES === '1', 'Uses live PostgreSQL and the simulated worker.');
  if (process.env.FLEET_E2E_EVENTS === '1') test.setTimeout(90_000);
  const success = `passing-${randomUUID().slice(0, 12)}`;
  const blocked = `blocked-${randomUUID().slice(0, 12)}`;
  const snap = async (name: string) => {
    const path = info.outputPath(`${name}.png`);
    await page.screenshot({ path });
    await info.attach(name, { path, contentType: 'image/png' });
  };
  control('fleet.scenario', 'healthy');
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Rehearsal', exact: true }).click();
    await approve(page, success);
    await page.getByRole('button', { name: 'Start wave 1', exact: true }).click();
    await expect(page.getByText('State: queued', { exact: true })).toBeVisible();
    control('fleet.worker', '--once');
    control('fleet.worker', '--once');
    await page.getByRole('button', { name: 'Refresh run' }).click();
    await expect(page.getByText('State: succeeded', { exact: true })).toBeVisible();
    await expect(page.getByText('harbor-01 · succeeded', { exact: true })).toBeVisible();
    await snap('passing-wave');

    await approve(page, blocked);
    const viaBroker = process.env.FLEET_E2E_EVENTS === '1';
    if (viaBroker) {
      const login = await page.request.post('/api/v1/demo/session', { data: { principal_id: 'demo-northstar' } });
      const { access_token } = await login.json();
      control('fleet.scenario', 'minute-later');
      const accepted = await page.request.post(`${scope}/observations`, {
        headers: { Authorization: `Bearer ${access_token}` },
        data: { schema_version: 1, event_id: randomUUID(), asset_id: 'harbor-02', source_id: 'synthetic-health',
          source_revision: Date.now(), observed_at: '2026-11-10T19:00:30Z', evidence: { healthy: false } },
      });
      expect(accepted.status()).toBe(202);
      await page.getByRole('button', { name: 'Refresh evidence' }).click();
      await expect(page.getByText(/Evidence is pending/)).toBeVisible();
      await page.getByRole('button', { name: 'Start wave 1', exact: true }).click();
      await expect(page.getByRole('alert').filter({ hasText: 'pending_evidence' })).toBeVisible();
      await snap('pending-observation');
      control('fleet.event_worker', 'relay', '--once');
      control('fleet.event_worker', 'project', '--once');
      await page.getByRole('button', { name: 'Refresh evidence' }).click();
      await expect(page.getByText(/Last observation: 2026-11-10T19:00:30Z/)).toBeVisible();
    } else {
      control('fleet.scenario', 'peer-down');
    }
    const start = page.getByRole('button', { name: 'Start wave 1', exact: true });
    await start.focus();
    await page.keyboard.press('Enter');
    const blockedAlert = page.getByRole('alert').filter({ hasText: 'alternate_path_unavailable' });
    await expect(blockedAlert).toBeFocused();
    expect(await blockedAlert.textContent()).toMatch(/Audit audit-\d+/);
    await expect(page.locator('time[datetime="2026-11-10T19:00:00Z"]')).toBeVisible();
    await expect(page.getByRole('heading', { name: /^Run run-/ })).toHaveCount(0);
    await page.getByRole('button', { name: 'Load audit' }).click();
    await expect(page.getByText(/wave_blocked · alternate_path_unavailable/)).toBeVisible();
    await snap('blocked-wave');
    const session = await page.request.post('/api/v1/demo/session', { data: { principal_id: 'demo-approver' } });
    const { access_token } = await session.json();
    const response = await page.request.get(`${scope}/audit?limit=100`, { headers: { Authorization: `Bearer ${access_token}` } });
    expect(response.ok()).toBe(true);
    const { items } = await response.json();
    expect(items.filter(event => event.plan_id === success && event.kind === 'simulator_started')).toHaveLength(1);
    expect(items.filter(event => event.plan_id === blocked).map(event => event.kind))
      .toEqual(['plan_previewed', 'plan_approved', 'wave_blocked', ...(viaBroker ? ['wave_blocked'] : [])]);
  } finally {
    control('fleet.scenario', 'healthy');
  }
});
