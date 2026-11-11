import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, Input, OnChanges, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import type { components } from './api.generated';

type Schemas = components['schemas'];
type Intent = { path: string; body: object; key: string; complete: (value: unknown) => void };

@Component({
  selector: 'rehearsal-panel',
  standalone: true,
  templateUrl: './rehearsal-panel.html',
  styles: [`
    :host { display: block; margin: 1rem; }
    section { padding: 1rem; }
    .actions { display: flex; flex-wrap: wrap; gap: .75rem; margin: .75rem 0; align-items: end; }
    label { display: grid; gap: .25rem; }
    input { padding: .45rem; max-width: 12rem; }
    article { border-block-start: 1px solid #ddd; padding: .65rem 0; }
  `],
})
export class RehearsalPanel implements OnChanges {
  @Input() tenant = '';
  @Input() token: string | null = null;
  protected readonly site = signal('harbor');
  protected readonly planId = signal('dispatch');
  protected readonly revision = signal(0);
  protected readonly context = signal<Schemas['RehearsalContext'] | null>(null);
  protected readonly plan = signal<Schemas['PlanRevision'] | null>(null);
  protected readonly run = signal<Schemas['Run'] | null>(null);
  protected readonly audit = signal<Schemas['AuditPage'] | null>(null);
  protected readonly error = signal<Schemas['Error'] | null>(null);
  protected readonly busy = signal(false);
  protected readonly canRetry = signal(false);
  private readonly http = inject(HttpClient);
  private scopeVersion = 0;
  private intent: Intent | null = null;

  ngOnChanges(): void { this.resetScope(); }

  protected chooseSite(value: string): void {
    this.site.set(value);
    this.resetScope();
  }

  protected choosePlan(value: string): void {
    this.planId.set(value);
    this.revision.set(0);
    this.plan.set(null);
    this.run.set(null);
    this.error.set(null);
    this.intent = null;
    this.canRetry.set(false);
  }

  protected chooseRevision(value: string): void {
    this.revision.set(Number(value));
    this.plan.set(null);
    this.run.set(null);
    this.error.set(null);
    this.intent = null;
    this.canRetry.set(false);
  }

  private resetScope(): void {
    this.scopeVersion++;
    this.context.set(null);
    this.plan.set(null);
    this.run.set(null);
    this.audit.set(null);
    this.error.set(null);
    this.busy.set(false);
    this.intent = null;
    this.canRetry.set(false);
    if (this.token && this.tenant && this.site()) this.refreshContext();
  }

  private get root(): string {
    return `/api/v1/tenants/${encodeURIComponent(this.tenant)}/sites/${encodeURIComponent(this.site())}`;
  }

  private get plans(): string {
    return `${this.root}/plans/${encodeURIComponent(this.planId())}/revisions`;
  }

  private async submit(path: string, apply: (value: unknown) => void, intent?: Intent, keepError = false): Promise<void> {
    if (!this.token || this.busy()) return;
    const version = this.scopeVersion;
    const headers = { Authorization: `Bearer ${this.token}` };
    if (!keepError) this.error.set(null);
    this.busy.set(true);
    try {
      const value = await firstValueFrom(intent
        ? this.http.post(path, intent.body, { headers: { ...headers, 'Idempotency-Key': intent.key } })
        : this.http.get(path, { headers }));
      if (version !== this.scopeVersion) return;
      if (intent) this.canRetry.set(false);
      apply(value);
    } catch (failure) {
      if (version !== this.scopeVersion) return;
      const http = failure instanceof HttpErrorResponse ? failure : null;
      this.canRetry.set(Boolean(intent && (!http || http.status === 0 || http.status >= 500)));
      if (http && [401, 403, 404].includes(http.status)) {
        this.plan.set(null);
        this.run.set(null);
        this.audit.set(null);
        if (http.status !== 404) this.context.set(null);
      }
      this.error.set(http?.error?.code ? http.error : { code: 'invalid_request', detail: 'The request could not be completed.' });
    } finally {
      if (version === this.scopeVersion) this.busy.set(false);
    }
  }

  private write(path: string, body: object, complete: (value: unknown) => void): void {
    this.intent = { path, body, key: crypto.randomUUID(), complete };
    void this.submit(path, complete, this.intent);
  }

  protected refreshContext(): void {
    void this.submit(`${this.root}/rehearsal/context`, value => this.context.set(value as Schemas['RehearsalContext']));
  }

  protected loadPlan(): void {
    void this.submit(`${this.plans}/${this.revision()}`, value => this.plan.set(value as Schemas['PlanRevision']));
  }

  protected preview(): void {
    this.run.set(null);
    this.write(this.plans, { base_revision: this.revision() }, value => {
      const plan = value as Schemas['PlanRevision'];
      this.plan.set(plan);
      this.revision.set(plan.revision);
    });
  }

  protected approve(): void {
    const plan = this.plan();
    if (!plan) return;
    this.write(`${this.plans}/${plan.revision}/approval`, { expected_fingerprint: plan.fingerprint },
      value => this.plan.set({ ...plan, approval: value as Schemas['Approval'] }));
  }

  protected start(number: number): void {
    const plan = this.plan();
    if (!plan) return;
    this.run.set(null);
    this.write(`${this.plans}/${plan.revision}/waves/${number}/runs`, { expected_fingerprint: plan.fingerprint },
      value => this.run.set(value as Schemas['Run']));
  }

  protected refreshRun(): void {
    const run = this.run();
    if (run) void this.submit(`${this.root}/runs/${run.run_id}`, value => this.run.set(value as Schemas['Run']));
  }

  protected loadAudit(after?: string | null): void {
    const suffix = after ? `?after=${encodeURIComponent(after)}` : '';
    void this.submit(`${this.root}/audit${suffix}`, value => this.audit.set(value as Schemas['AuditPage']), undefined, true);
  }

  protected retryWrite(): void {
    if (this.intent) void this.submit(this.intent.path, this.intent.complete, this.intent);
  }
}
