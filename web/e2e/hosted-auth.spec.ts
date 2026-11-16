import { expect, test } from '@playwright/test';

test('hosted sign-in recovers from configuration failure using the keyboard', async ({ page }) => {
  test.skip(process.env.FLEET_EXPECT_FIXTURES === '1');
  let requests = 0;
  let demoRequests = 0;
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/demo/')) demoRequests++;
  });
  await page.route('**/api/v1/auth/config', route => {
    requests++;
    return requests === 1 ? route.fulfill({ status: 503 }) : route.fulfill({
      json: { mode: 'hosted', issuer: 'https://identity.example.invalid/', client_id: 'test-client',
        audience: 'https://fleet.example.invalid' },
    });
  });
  await page.route('https://identity.example.invalid/authorize?**', route => route.fulfill({ body: 'Test identity provider' }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Sign-in could not be restored');
  const retry = page.getByRole('button', { name: 'Retry failed request' });
  await retry.focus();
  await page.keyboard.press('Enter');
  const login = page.getByRole('button', { name: 'Sign in', exact: true });
  await expect(login).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Select synthetic operator identity' })).toHaveCount(0);
  await login.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Test identity provider')).toBeVisible();
  const redirect = new URL(page.url());
  expect(redirect.searchParams.get('code_challenge_method')).toBe('S256');
  expect(redirect.searchParams.get('code_challenge')).toBeTruthy();
  expect(redirect.searchParams.get('state')).toBeTruthy();
  expect(redirect.searchParams.get('audience')).toBe('https://fleet.example.invalid');
  expect(demoRequests).toBe(0);
});
