import {
  HttpErrorResponse,
  HttpInterceptorFn,
  HttpResponse,
  provideHttpClient,
  withInterceptors,
} from '@angular/common/http';
import { of, throwError } from 'rxjs';
import type { components } from './api.generated';

type Asset = components['schemas']['Asset'];
const northstar = { id: 'northstar', label: 'Northstar' };
const summit = { id: 'summit', label: 'Summit' };
const principals: components['schemas']['Principal'][] = [
  { id: 'demo-northstar', label: 'Northstar operator', tenants: [northstar] },
  { id: 'demo-summit', label: 'Summit operator', tenants: [summit] },
  { id: 'demo-partner', label: 'Partner · Harbor', tenants: [northstar] },
];
const sites: Record<string, string[]> = {
  'demo-northstar': ['harbor', 'river'],
  'demo-summit': ['ridge'],
  'demo-partner': ['harbor'],
};
const rows: Pick<Asset, 'id' | 'name' | 'tenant_id' | 'site_id' | 'site_name' | 'running_release' | 'finding' | 'reason'>[] = [
  { id: 'harbor-01', name: 'Harbor access switch 01', tenant_id: 'northstar', site_id: 'harbor', site_name: 'Harbor', running_release: 'lab-affected', finding: 'affected', reason: 'The modeled release is affected and an applicable feature is enabled.' },
  { id: 'harbor-02', name: 'Harbor access switch 02', tenant_id: 'northstar', site_id: 'harbor', site_name: 'Harbor', running_release: 'lab-fixed', finding: 'unaffected', reason: 'The modeled release is outside the affected set.' },
  { id: 'harbor-03', name: 'Harbor access switch 03', tenant_id: 'northstar', site_id: 'harbor', site_name: 'Harbor', running_release: 'lab-affected', finding: 'needs_evidence', reason: 'Configuration evidence is incomplete for an affected modeled release.' },
  { id: 'ridge-01', name: 'Ridge access switch 01', tenant_id: 'summit', site_id: 'ridge', site_name: 'Ridge', running_release: 'lab-affected', finding: 'unaffected', reason: 'The modeled device has none of the applicable features enabled.' },
  { id: 'river-01', name: 'River distribution switch 01', tenant_id: 'northstar', site_id: 'river', site_name: 'River', running_release: 'lab-affected', finding: 'affected', reason: 'The modeled release is affected and an applicable feature is enabled.' },
];
const assets = rows.map<Asset>(row => ({
  ...row,
  product: 'Catalyst 9300',
  observed_at: '2026-11-10T18:00:00Z',
  advisory_id: 'CVE-2025-20311',
  advisory_revision: '1.1',
  advisory_url: 'https://www.cisco.com/c/en/us/support/docs/csa/cisco-sa-cat9k-PtmD7bgy.html',
})).sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
const reply = (body: object) => of(new HttpResponse({ body }));
const fail = (status: number, detail: string) =>
  throwError(() => new HttpErrorResponse({ status, error: { detail } }));

// Local fixture identities exercise the UI; the live API enforces real scope.
const fixtureInventory: HttpInterceptorFn = (request, next) => {
  if (!request.url.startsWith('/api/')) return next(request);
  const { pathname, searchParams } = new URL(request.urlWithParams, 'http://fixture.invalid');
  if (request.method === 'GET' && pathname === '/api/v1/demo/principals') return reply({ items: principals });
  if (request.method === 'POST' && pathname === '/api/v1/demo/session') {
    const id = (request.body as { principal_id?: string } | null)?.principal_id;
    return principals.some(principal => principal.id === id)
      ? reply({ access_token: `fixture-${id}`, token_type: 'bearer' })
      : fail(404, 'Demo identity not found.');
  }
  const detail = pathname.match(/^\/api\/v1\/assets\/([a-z0-9-]+)$/);
  if (request.method !== 'GET' || (!detail && pathname !== '/api/v1/assets')) return next(request);
  const principal = principals.find(item => request.headers.get('Authorization') === `Bearer fixture-${item.id}`);
  if (!principal) return fail(401, 'Sign in with a demo identity.');
  const tenant = searchParams.get('tenant_id');
  if (!principal.tenants.some(item => item.id === tenant)) return fail(403, 'This identity does not have access to that customer.');
  const visible = assets.filter(asset => asset.tenant_id === tenant && sites[principal.id].includes(asset.site_id));
  if (detail) {
    const asset = visible.find(item => item.id === detail[1]);
    return asset ? reply(asset) : fail(404, 'Device not found in the granted sites.');
  }
  const finding = searchParams.get('finding');
  const limit = Number(searchParams.get('limit') ?? '25');
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) return fail(422, 'Limit must be between 1 and 100.');
  const after = searchParams.get('after') ?? '';
  const matching = visible.filter(asset => finding === null || asset.finding === finding);
  const page = matching.filter(asset => asset.id > after);
  const items = page.slice(0, limit);
  return reply({ items, next_cursor: page.length > limit ? items[limit - 1].id : null, total: matching.length });
};

export const inventoryHttp = provideHttpClient(withInterceptors([fixtureInventory]));
export const inventoryFixtureMode = true;
