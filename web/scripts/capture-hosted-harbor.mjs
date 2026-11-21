import { createHmac, randomBytes } from 'node:crypto';
import { createInterface } from 'node:readline/promises';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';

const siteApi = '/api/v1/tenants/northstar/sites/harbor';
const slug = /^[a-z0-9][a-z0-9-]{0,63}$/;
const requestId = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;

class CaptureError extends Error {}

function expectedActions(passingPlan, blockedPlan) {
  const actions = new Map();
  for (const plan of [passingPlan, blockedPlan]) {
    const path = `${siteApi}/plans/${plan}/revisions`;
    const healthy = plan === passingPlan;
    actions.set(path, { plan, operation: 'preview', status: 201, role: 'partner_proposer',
      actor: 'demo-partner', kind: 'plan_previewed' });
    actions.set(`${path}/1/approval`, { plan, operation: 'approval', status: 201, role: 'customer_approver',
      actor: 'demo-approver', kind: 'plan_approved' });
    actions.set(`${path}/1/waves/1/runs`, { plan, operation: 'start', status: healthy ? 202 : 409,
      role: 'operator', actor: 'demo-northstar', kind: healthy ? 'wave_reserved' : 'wave_blocked' });
  }
  return actions;
}

export function recordHarbor(page, { origin, issuer, sourceCommit, passingPlan, blockedPlan }) {
  const plans = [passingPlan, blockedPlan].sort();
  const actions = expectedActions(passingPlan, blockedPlan);
  const key = randomBytes(32);
  const pending = [];
  const requests = [];
  let operatorRead;

  async function record(response, action) {
    const authorization = (await response.request().allHeaders()).authorization;
    const jwt = /^Bearer ([\w-]+)\.([\w-]+)\.([\w-]+)$/.exec(authorization ?? '');
    if (!jwt) return;
    const { iss, sub } = JSON.parse(Buffer.from(jwt[2], 'base64url').toString('utf8'));
    if (iss !== issuer || typeof sub !== 'string' || !sub) return;
    const claims = { iss, sub_redacted: createHmac('sha256', key).update(sub).digest('hex') };
    if (!action) {
      operatorRead = { authorization, claims };
      return;
    }
    const correlation = (await response.allHeaders())['x-request-id'];
    if (requestId.test(correlation ?? '')) requests.push({ action, correlation, claims });
  }

  const onResponse = response => {
    const { origin: responseOrigin, pathname } = new URL(response.url());
    const request = response.request();
    if (responseOrigin !== origin || request.redirectedFrom()) return;
    const action = request.method() === 'POST' ? actions.get(pathname) : undefined;
    const auditRead = request.method() === 'GET' && pathname === `${siteApi}/audit` && response.status() === 200;
    if (auditRead || (action && response.status() === action.status)) {
      pending.push(record(response, action).catch(() => {}));
    }
  };
  page.on('response', onResponse);

  return async () => {
    page.off('response', onResponse);
    try {
      await Promise.all(pending);
      if (!operatorRead) throw new CaptureError('Load the audit in the signed-in operator session before exporting.');
      const { context, audit_events, runs } = await page.evaluate(async ({ origin, siteApi, plans, authorization }) => {
        if (location.origin !== origin) throw new Error('Return to the hosted app before exporting.');
        const read = (path, authenticated = true) => fetch(siteApi + path, {
          credentials: 'omit', redirect: 'error', cache: 'no-store',
          headers: authenticated ? { Authorization: authorization } : {},
        });
        const session = await read('/rehearsal/context');
        if (!session.ok || (await read('/rehearsal/context', false)).status !== 401) {
          throw new Error('The hosted authentication checks failed.');
        }
        const context = await session.json();
        if (context.role !== 'operator') throw new Error('Export requires an operator session.');
        const audit_events = [];
        const cursors = new Set();
        const query = new URLSearchParams({ limit: '25' });
        while (true) {
          const response = await read(`/audit?${query}`);
          if (!response.ok) throw new Error('The audit request failed.');
          const { items, next_cursor } = await response.json();
          audit_events.push(...items.filter(event => plans.includes(event.plan_id)));
          if (next_cursor === null) break;
          if (!next_cursor || cursors.has(next_cursor) || cursors.size >= 999) throw new Error('Invalid audit pagination.');
          cursors.add(next_cursor);
          query.set('after', next_cursor);
        }
        const runs = [];
        for (const id of new Set(audit_events.map(event => event.run_id).filter(Boolean))) {
          if (!/^run-[a-z0-9-]+$/.test(id)) throw new Error('Invalid run ID.');
          const response = await read(`/runs/${id}`);
          if (!response.ok) throw new Error('A run request failed.');
          const run = await response.json();
          if (run.run_id !== id || !plans.includes(run.plan_id)) throw new Error('Run outside the capture scope.');
          runs.push(run);
        }
        return { context, audit_events, runs };
      }, { origin, siteApi, plans, authorization: operatorRead.authorization });

      const browser_requests = audit_events.flatMap(event => {
        const matches = requests.filter(({ action, correlation }) =>
          correlation === event.correlation_id && action.plan === event.plan_id &&
          action.actor === event.actor_id && action.kind === event.kind);
        if (matches.length !== 1) return [];
        const [{ action, claims, correlation }] = matches;
        return [{ plan_id: action.plan, operation: action.operation, role: action.role, http_status: action.status,
          correlation_id: correlation, ...claims, audit_id: event.audit_id }];
      });
      if (browser_requests.length !== actions.size ||
          new Set(browser_requests.map(request => request.correlation_id)).size !== actions.size ||
          new Set(browser_requests.map(request => `${request.plan_id}/${request.operation}`)).size !== actions.size) {
        throw new CaptureError('Expected six unambiguous request-to-audit matches, one per action.');
      }
      const subjectsByRole = new Map();
      for (const request of browser_requests) {
        const prior = subjectsByRole.get(request.role);
        if (prior && prior !== request.sub_redacted) throw new CaptureError('The browser identity changed during a role.');
        subjectsByRole.set(request.role, request.sub_redacted);
      }
      if (new Set(subjectsByRole.values()).size !== 3 ||
          subjectsByRole.get('operator') !== operatorRead.claims.sub_redacted) {
        throw new CaptureError('Expected three Auth0 subjects and the same operator for the starts and export.');
      }
      const canaryEvents = audit_events.filter(event => event.plan_id === passingPlan);
      const blockedEvents = audit_events.filter(event => event.plan_id === blockedPlan);
      if (canaryEvents.map(event => event.kind).join() !== 'plan_previewed,plan_approved,wave_reserved,simulator_started,run_updated' ||
          blockedEvents.map(event => event.kind).join() !== 'plan_previewed,plan_approved,wave_blocked' ||
          canaryEvents.slice(3).some(event => event.correlation_id !== canaryEvents[2].correlation_id) ||
          blockedEvents.at(-1).reason !== 'alternate_path_unavailable' || blockedEvents.some(event => event.run_id) ||
          runs.length !== 1 || runs[0].plan_id !== passingPlan || runs[0].state !== 'succeeded' ||
          runs[0].verification?.seed !== 1 || runs[0].verification?.unreachable_services?.length !== 0) {
        throw new CaptureError('The captured plan outcomes differ from the healthy-canary and peer-outage rehearsal.');
      }
      return {
        source: 'site_api', capture_method: 'browser_same_origin', captured_at_utc: new Date().toISOString(),
        api_base: `${origin}/api`, tenant_id: 'northstar', site_id: 'harbor', plan_ids: plans,
        source_commit: sourceCommit, context, auth0_claims: operatorRead.claims, audit_events, runs, browser_requests,
      };
    } finally {
      operatorRead = undefined;
      key.fill(0);
    }
  };
}

function httpsOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new CaptureError('Use a bare HTTPS origin for the host and Auth0 issuer.');
  }
  return url.origin;
}

async function main() {
  const { values } = parseArgs({ options: {
    host: { type: 'string' }, issuer: { type: 'string' }, 'source-commit': { type: 'string' },
    'passing-plan': { type: 'string' }, 'blocked-plan': { type: 'string' },
  } });
  const passingPlan = values['passing-plan'];
  const blockedPlan = values['blocked-plan'];
  const sourceCommit = values['source-commit'];
  if (!slug.test(passingPlan ?? '') || !slug.test(blockedPlan ?? '') || passingPlan === blockedPlan ||
      !/^[a-f0-9]{40}$/.test(sourceCommit ?? '')) {
    throw new CaptureError('Use two distinct new plan IDs and the full deployment Git SHA.');
  }
  const origin = httpsOrigin(values.host);
  const issuer = `${httpsOrigin(values.issuer)}/`;
  const { chromium } = await import('@playwright/test');
  const browser = await chromium.launch({ headless: false });
  const terminal = createInterface({ input: process.stdin, output: process.stderr });
  try {
    const page = await browser.newPage();
    const finish = recordHarbor(page, { origin, issuer, sourceCommit, passingPlan, blockedPlan });
    await page.goto(origin);
    await terminal.question('Rehearse both plans in this window. As the operator, load the audit and press Enter here to export. ');
    process.stdout.write(`${JSON.stringify(await finish(), null, 2)}\n`);
  } finally {
    terminal.close();
    await browser.close();
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch(error => {
    console.error(error instanceof CaptureError ? error.message :
      'Capture failed. Check Chromium, the hosted app connection, and the final operator sign-in.');
    process.exitCode = 1;
  });
}
