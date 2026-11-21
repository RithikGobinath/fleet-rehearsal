import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { recordHarbor } from '../scripts/capture-hosted-harbor.mjs';

const fixture = JSON.parse(readFileSync(new URL(
  '../../docs/evidence/azure-harbor/harbor-nov20-api-evidence.json', import.meta.url,
), 'utf8'));
const origin = 'https://harbor.example.invalid';
const issuer = 'https://identity.example.invalid/';
const siteApi = '/api/v1/tenants/northstar/sites/harbor';
const sessions = Object.fromEntries(['partner_proposer', 'customer_approver', 'operator'].map(role => [
  role, `Bearer fixture.${Buffer.from(JSON.stringify({ iss: issuer, sub: `synthetic-${role}` })).toString('base64url')}.fixture`,
]));
const mutationPath = ({ plan_id, operation }) =>
  `${siteApi}/plans/${plan_id}/revisions${{ preview: '', approval: '/1/approval', start: '/1/waves/1/runs' }[operation]}`;
const mutations = new Map(fixture.browser_requests.map(action => [mutationPath(action), action]));

test.use({ trace: 'off', video: 'off' });

for (const scenario of ['complete', 'redirected', 'duplicate']) {
  test(`hosted evidence collector checks ${scenario} browser requests`, async ({ page }) => {
    let anonymousChecks = 0;
    let readSecondPage = false;
    await page.route(`${origin}/**`, async route => {
      const { pathname, searchParams } = new URL(route.request().url());
      const authorization = (await route.request().allHeaders()).authorization;
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<title>Harbor evidence fixture</title>' });
      if (pathname === `${siteApi}/rehearsal/context` && !authorization) {
        anonymousChecks++;
        return route.fulfill({ status: 401 });
      }
      const action = mutations.get(pathname);
      expect(authorization).toBe(sessions[action?.role ?? 'operator']);
      if (action) return route.fulfill({ status: action.http_status, headers: { 'X-Request-ID': action.correlation_id }, json: {} });
      if (pathname === `${siteApi}/rehearsal/context`) return route.fulfill({ json: fixture.context });
      if (pathname === `${siteApi}/audit`) {
        const after = searchParams.get('after');
        readSecondPage ||= after === 'page-2';
        return route.fulfill({ json: after ? { items: fixture.audit_events.slice(4), next_cursor: null }
          : { items: fixture.audit_events.slice(0, 4), next_cursor: 'page-2' } });
      }
      expect(pathname).toBe(`${siteApi}/runs/${fixture.runs[0].run_id}`);
      return route.fulfill({ json: fixture.runs[0] });
    });
    await page.goto(origin);
    const finish = recordHarbor(page, { origin, issuer, sourceCommit: fixture.source_commit,
      passingPlan: fixture.runs[0].plan_id, blockedPlan: fixture.audit_events.find(event => event.kind === 'wave_blocked').plan_id });
    const send = (path, role, method) => page.evaluate(
      ({ path, authorization, method }) => fetch(path, { headers: { Authorization: authorization }, method }).then(response => response.status),
      { path, authorization: sessions[role], method },
    );

    const [first, ...remaining] = fixture.browser_requests;
    if (scenario === 'redirected') {
      page.emit('response', { url: () => origin + mutationPath(first), status: () => 201,
        request: () => ({ method: () => 'POST', redirectedFrom: () => ({}),
          allHeaders: async () => ({ authorization: sessions[first.role] }) }),
        allHeaders: async () => ({ 'x-request-id': first.correlation_id }),
      });
    }
    const actions = scenario === 'redirected' ? remaining : fixture.browser_requests;
    for (const action of actions) expect(await send(mutationPath(action), action.role, 'POST')).toBe(action.http_status);
    if (scenario === 'duplicate') await send(mutationPath(first), first.role, 'POST');
    expect(await send(`${siteApi}/audit?limit=25`, 'operator', 'GET')).toBe(200);

    if (scenario === 'complete') {
      const evidence = await finish();
      expect(evidence.audit_events).toEqual(fixture.audit_events);
      expect(evidence.runs).toEqual(fixture.runs);
      expect(evidence.browser_requests.map(({ sub_redacted, iss, ...action }) => action)).toEqual(
        fixture.browser_requests.map(({ sub_redacted, iss, ...action }) => action),
      );
      expect(new Set(evidence.browser_requests.map(action => action.sub_redacted)).size).toBe(3);
      expect(evidence.auth0_claims.sub_redacted).toBe(evidence.browser_requests.at(-1).sub_redacted);
      expect(JSON.stringify(evidence)).not.toMatch(/synthetic-|Bearer |fixture\./);
    } else {
      await expect(finish()).rejects.toThrow('Expected six unambiguous request-to-audit matches');
    }
    expect(anonymousChecks).toBe(1);
    expect(readSecondPage).toBe(true);
  });
}
