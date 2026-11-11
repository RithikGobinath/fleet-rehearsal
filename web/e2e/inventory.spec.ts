import { expect, test } from '@playwright/test';

test('browses findings and switches between granted scopes', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Fleet Rehearsal' })).toBeVisible();
  await expect(page.getByText('River distribution switch 01', { exact: true })).toBeVisible();

  const identity = page.getByRole('combobox', { name: 'Select synthetic operator identity' });
  await identity.selectOption('demo-partner');
  await expect(page.getByText('River distribution switch 01', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Harbor access switch 01', { exact: true })).toBeVisible();
  await expect(page.getByText('Harbor access switch 02', { exact: true })).toBeVisible();
  await expect(page.getByText('Harbor access switch 03', { exact: true })).toBeVisible();

  await page.getByRole('radio', { name: 'Needs Evidence' }).click();
  await expect(page.getByText('Harbor access switch 01', { exact: true })).toHaveCount(0);
  const evidenceRow = page.getByRole('row').filter({ hasText: 'Harbor access switch 03' });
  const details = evidenceRow.getByRole('button', { name: 'View evidence for Harbor access switch 03' });
  await details.focus();
  await details.press('Enter');
  await expect(page.getByText('Configuration evidence is incomplete for an affected modeled release.', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: /CVE-2025-20311/ })).toHaveAttribute('href', /cisco\.com/);

  await page.getByRole('radio', { name: 'All', exact: true }).click();
  await identity.selectOption('demo-summit');
  await expect(page.getByText('Ridge access switch 01', { exact: true })).toBeVisible();
  await expect(page.getByText('Harbor access switch 03', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Configuration evidence is incomplete for an affected modeled release.', { exact: true })).toHaveCount(0);
});
