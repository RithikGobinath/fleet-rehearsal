import {
  Component,
  ChangeDetectionStrategy,
  OnInit,
  OnDestroy,
  signal,
  computed,
  inject
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Subscription } from 'rxjs';
import type { components } from './api.generated';
import { inventoryFixtureMode } from './inventory-http';
import { RehearsalPanel } from './rehearsal-panel';

type Schemas = components['schemas'];
type PrincipalTenant = Schemas['Tenant'];
type Principal = Schemas['Principal'];
type PrincipalsResponse = Schemas['PrincipalList'];
type SessionResponse = Schemas['SessionResponse'];
type Asset = Schemas['Asset'];
type FindingType = Asset['finding'];
type AssetsResponse = Schemas['InventoryPage'];

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RehearsalPanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="app-container">
      <!-- Accessible status announcement region -->
      <div class="sr-only" role="status" aria-live="polite">
        {{ liveStatusMessage() }}
      </div>

      <!-- Compact Header -->
      <header class="console-header" role="banner">
        <div class="brand-section">
          <h1 class="brand-title">Fleet Rehearsal</h1>
          <span class="brand-badge">Inventory Console</span>
          @if (fixtureMode) {
            <span class="brand-badge">Local fixtures</span>
          }
        </div>

        <div class="header-controls">
          <!-- Synthetic Identity Selection -->
          <div class="identity-control-group">
            <label for="identity-selector" class="control-label">Identity:</label>
            <select
              id="identity-selector"
              class="control-select"
              [disabled]="isLoadingPrincipals() || isLoadingSession()"
              [value]="selectedPrincipalId()"
              (change)="onPrincipalChange($any($event.target).value)"
              aria-label="Select synthetic operator identity"
            >
              @if (isLoadingPrincipals()) {
                <option value="">Loading identities...</option>
              } @else {
                @for (principal of principals(); track principal.id) {
                  <option [value]="principal.id">{{ principal.label }} ({{ principal.id }})</option>
                }
              }
            </select>
          </div>

          <!-- Permitted Tenant Selection -->
          <div class="identity-control-group">
            <label for="tenant-selector" class="control-label">Tenant:</label>
            <select
              id="tenant-selector"
              class="control-select"
              [disabled]="isLoadingSession() || availableTenants().length <= 1"
              [value]="activeTenantId()"
              (change)="onTenantChange($any($event.target).value)"
              aria-label="Select authorized tenant"
            >
              @for (tenant of availableTenants(); track tenant.id) {
                <option [value]="tenant.id">{{ tenant.label }}</option>
              }
            </select>
          </div>
        </div>
      </header>

      @if (!fixtureMode) {
        <nav aria-label="Rehearsal navigation">
          <button class="btn btn-secondary" type="button" [attr.aria-expanded]="showRehearsal()"
            (click)="showRehearsal.update(togglePanel)">Rehearsal</button>
        </nav>
        @if (showRehearsal()) {
          <rehearsal-panel [token]="bearerToken()" [tenant]="activeTenantId()" />
        }
      }

      <!-- Main Workspace -->
      <main class="console-main" id="main-content">
        <!-- Error Banner -->
        @if (error()) {
          <div class="error-banner" role="alert">
            <div class="error-text">
              <strong>Error:</strong> {{ error() }}
            </div>
            <button
              type="button"
              class="btn btn-secondary"
              (click)="retryCurrentAction()"
              aria-label="Retry failed request"
            >
              Retry
            </button>
          </div>
        }

        <!-- Operations Toolbar -->
        <section class="operations-toolbar" aria-label="Inventory filters and controls">
          <div class="toolbar-filters">
            <!-- Finding Status Filter -->
            <div class="filter-group">
              <span class="control-label" id="filter-label">Finding Status:</span>
              <div class="filter-pills" role="radiogroup" aria-labelledby="filter-label">
                <button
                  type="button"
                  role="radio"
                  class="pill-button"
                  [class.active]="findingFilter() === ''"
                  [attr.aria-checked]="findingFilter() === ''"
                  [disabled]="isLoadingAssets() || isLoadingSession()"
                  (click)="setFindingFilter('')"
                >
                  All
                </button>
                <button
                  type="button"
                  role="radio"
                  class="pill-button"
                  [class.active]="findingFilter() === 'affected'"
                  [attr.aria-checked]="findingFilter() === 'affected'"
                  [disabled]="isLoadingAssets() || isLoadingSession()"
                  (click)="setFindingFilter('affected')"
                >
                  Affected
                </button>
                <button
                  type="button"
                  role="radio"
                  class="pill-button"
                  [class.active]="findingFilter() === 'unaffected'"
                  [attr.aria-checked]="findingFilter() === 'unaffected'"
                  [disabled]="isLoadingAssets() || isLoadingSession()"
                  (click)="setFindingFilter('unaffected')"
                >
                  Unaffected
                </button>
                <button
                  type="button"
                  role="radio"
                  class="pill-button"
                  [class.active]="findingFilter() === 'needs_evidence'"
                  [attr.aria-checked]="findingFilter() === 'needs_evidence'"
                  [disabled]="isLoadingAssets() || isLoadingSession()"
                  (click)="setFindingFilter('needs_evidence')"
                >
                  Needs Evidence
                </button>
              </div>
            </div>
          </div>

          <div class="toolbar-actions">
            <button
              type="button"
              class="btn btn-secondary"
              [disabled]="isLoadingAssets() || isLoadingSession()"
              (click)="refreshAssets()"
              aria-label="Refresh asset inventory"
            >
              @if (isLoadingAssets()) {
                <span class="spinner spinner-sm" aria-hidden="true"></span>
                <span style="margin-left: 0.375rem">Refreshing...</span>
              } @else {
                <span>Refresh</span>
              }
            </button>
          </div>
        </section>

        <!-- Summary Metrics Cards -->
        <section class="summary-bar" aria-label="Inventory summary metrics">
          <div class="summary-card">
            <span class="summary-card-label">Total Authorized Assets</span>
            <span class="summary-card-value">{{ totalAssets() }}</span>
            <span class="summary-card-subtext">Matching current filter</span>
          </div>

          <div class="summary-card">
            <span class="summary-card-label">Current Page Count</span>
            <span class="summary-card-value">{{ assets().length }}</span>
            <span class="summary-card-subtext">Rows loaded on page {{ currentPage() }}</span>
          </div>

          <div class="summary-card">
            <span class="summary-card-label">Active Filter</span>
            <span class="summary-card-value" style="font-size: 1.125rem;">
              {{ filterDisplayName() }}
            </span>
            <span class="summary-card-subtext">Scope: {{ activeTenantLabel() }}</span>
          </div>
        </section>

        <!-- Operations Split Workspace: Table & Evidence Panel -->
        <div class="operations-workspace">
          <!-- Assets Table Section -->
          <section class="panel-card" aria-label="Assets inventory table">
            <div class="panel-header">
              <h2 class="panel-title">Asset Inventory</h2>
              <span class="panel-meta">Tenant: {{ activeTenantLabel() }}</span>
            </div>

            <!-- Loading State -->
            @if (isLoadingSession() || (isLoadingAssets() && assets().length === 0)) {
              <div class="state-container" aria-live="polite">
                <div class="spinner" aria-hidden="true"></div>
                <h3 class="state-title">Loading inventory...</h3>
                <p class="state-description">
                  Retrieving authorized devices and advisory classifications for {{ activeTenantLabel() }}.
                </p>
              </div>
            } @else if (!isLoadingSession() && assets().length === 0) {
              <!-- Empty State -->
              <div class="state-container" aria-live="polite">
                <h3 class="state-title">No assets found</h3>
                <p class="state-description">
                  No assets match the current filter criteria for {{ activeTenantLabel() }}.
                </p>
                @if (findingFilter() !== '') {
                  <button
                    type="button"
                    class="btn btn-secondary"
                    (click)="setFindingFilter('')"
                  >
                    Reset Filter to All
                  </button>
                }
              </div>
            } @else {
              <!-- Data Table -->
              <div class="table-wrapper">
                <table class="inventory-table" aria-label="Device inventory list">
                  <thead>
                    <tr>
                      <th scope="col">Asset Name</th>
                      <th scope="col">Asset ID</th>
                      <th scope="col">Site</th>
                      <th scope="col">Product</th>
                      <th scope="col">Running Release</th>
                      <th scope="col">Finding</th>
                      <th scope="col"><span class="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (asset of assets(); track asset.id) {
                      <tr [class.row-selected]="selectedAsset()?.id === asset.id">
                        <td style="font-weight: 600;">{{ asset.name }}</td>
                        <td class="mono-cell">{{ asset.id }}</td>
                        <td>{{ asset.site_name }} ({{ asset.site_id }})</td>
                        <td>{{ asset.product }}</td>
                        <td class="mono-cell">{{ asset.running_release }}</td>
                        <td>
                          <span
                            class="finding-badge"
                            [ngClass]="'badge-' + asset.finding"
                          >
                            <span class="sr-only">Finding status:</span>
                            {{ formatFinding(asset.finding) }}
                          </span>
                        </td>
                        <td style="text-align: right;">
                          <button
                            type="button"
                            class="btn btn-secondary"
                            style="height: 26px; padding: 0 0.5rem; font-size: 0.75rem;"
                            (click)="selectAsset(asset)"
                            [attr.aria-label]="'View evidence for ' + asset.name"
                          >
                            Details
                          </button>
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>

              <!-- Keyset Pagination Controls -->
              <div class="table-pagination" aria-label="Pagination">
                <span class="pagination-info">
                  Page {{ currentPage() }} · Total: {{ totalAssets() }} assets
                </span>
                <div class="pagination-controls">
                  <button
                    type="button"
                    class="btn btn-secondary"
                    [disabled]="isLoadingAssets() || cursorHistory().length === 0"
                    (click)="goToPreviousPage()"
                    aria-label="Go to previous page"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    class="btn btn-secondary"
                    [disabled]="isLoadingAssets() || !nextCursor()"
                    (click)="goToNextPage()"
                    aria-label="Go to next page"
                  >
                    Next
                  </button>
                </div>
              </div>
            }
          </section>

          <!-- Evidence Detail Panel -->
          <aside
            class="panel-card evidence-panel"
            aria-labelledby="evidence-panel-heading"
          >
            <div class="panel-header">
              <h2 id="evidence-panel-heading" class="panel-title">Asset Evidence</h2>
              @if (selectedAsset()) {
                <button
                  type="button"
                  class="btn-close-panel"
                  (click)="closeEvidencePanel()"
                  aria-label="Close evidence panel"
                >
                  &times;
                </button>
              }
            </div>

            @if (selectedAsset()) {
              <div class="evidence-body">
                <!-- Selected Device Header -->
                <div class="evidence-device-header">
                  <h3 class="device-name">{{ selectedAsset()?.name }}</h3>
                  <div class="device-id-tag">ID: {{ selectedAsset()?.id }}</div>
                </div>

                <!-- Device-level grant error if 404 or forbidden -->
                @if (selectedAssetDetailError()) {
                  <div class="error-banner" role="alert">
                    <div class="error-text">
                      <strong>Access check:</strong> {{ selectedAssetDetailError() }}
                    </div>
                  </div>
                }

                <!-- Status Badge -->
                <div>
                  <div class="evidence-section-title">Finding Status</div>
                  <span
                    class="finding-badge"
                    [ngClass]="'badge-' + selectedAsset()?.finding"
                  >
                    {{ formatFinding(selectedAsset()!.finding) }}
                  </span>
                </div>

                <!-- Reason Section -->
                <div>
                  <div class="evidence-section-title">Evaluation Reason</div>
                  <div class="evidence-reason-box">
                    {{ selectedAsset()?.reason }}
                  </div>
                </div>

                <!-- Observation Section -->
                <div>
                  <div class="evidence-section-title">Observation</div>
                  <div class="evidence-grid">
                    <span class="grid-key">Observed at:</span>
                    <span class="grid-val" [title]="selectedAsset()?.observed_at">
                      {{ formatObservationDate(selectedAsset()?.observed_at) }}
                    </span>
                    <span class="grid-key">Timestamp:</span>
                    <span class="grid-val mono-cell" style="font-size: 0.75rem;">
                      {{ selectedAsset()?.observed_at }}
                    </span>
                  </div>
                </div>

                <!-- Advisory Source Section -->
                <div>
                  <div class="evidence-section-title">Advisory Source</div>
                  <div class="evidence-grid">
                    <span class="grid-key">Advisory ID:</span>
                    <span class="grid-val mono-cell">
                      {{ selectedAsset()?.advisory_id }}
                    </span>

                    <span class="grid-key">Revision:</span>
                    <span class="grid-val mono-cell">
                      {{ selectedAsset()?.advisory_revision }}
                    </span>

                    <span class="grid-key">Reference:</span>
                    <span class="grid-val">
                      <a
                        [href]="selectedAsset()?.advisory_url"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="advisory-link"
                        [attr.aria-label]="'Open Cisco advisory for ' + selectedAsset()?.advisory_id + ' (opens in new tab)'"
                      >
                        {{ selectedAsset()?.advisory_id }} Advisory &nearr;
                      </a>
                    </span>
                  </div>
                </div>

                <!-- Modeled Device Attributes -->
                <div>
                  <div class="evidence-section-title">Modeled Device Info</div>
                  <div class="evidence-grid">
                    <span class="grid-key">Product:</span>
                    <span class="grid-val">{{ selectedAsset()?.product }}</span>

                    <span class="grid-key">Running Rel:</span>
                    <span class="grid-val mono-cell">{{ selectedAsset()?.running_release }}</span>

                    <span class="grid-key">Site:</span>
                    <span class="grid-val">{{ selectedAsset()?.site_name }} ({{ selectedAsset()?.site_id }})</span>

                    <span class="grid-key">Tenant ID:</span>
                    <span class="grid-val mono-cell">{{ selectedAsset()?.tenant_id }}</span>
                  </div>
                </div>
              </div>
            } @else {
              <!-- Empty Evidence Panel State -->
              <div class="state-container" style="padding: 2.5rem 1rem;">
                <h3 class="state-title" style="font-size: 0.875rem;">No asset selected</h3>
                <p class="state-description" style="font-size: 0.75rem;">
                  Open Details for an asset to see its classification reason, observation timestamp, and source advisory.
                </p>
              </div>
            }
          </aside>
        </div>
      </main>
    </div>
  `
})
export class App implements OnInit, OnDestroy {
  private readonly http = inject(HttpClient);
  protected readonly fixtureMode = inventoryFixtureMode;
  protected readonly showRehearsal = signal(false);
  protected readonly togglePanel = (open: boolean) => !open;

  // Sequence guards to prevent late responses from race conditions
  private identitySequence = 0;
  private assetRequestSequence = 0;

  // Active RxJS subscriptions for prompt cancellation
  private principalsSub?: Subscription;
  private sessionSub?: Subscription;
  private assetsSub?: Subscription;
  private assetDetailSub?: Subscription;

  // Component state signals
  readonly principals = signal<Principal[]>([]);
  readonly selectedPrincipalId = signal<string>('');
  readonly activeTenantId = signal<string>('');

  // Bearer token strictly stored only in component state (not in storage)
  protected readonly bearerToken = signal<string | null>(null);

  readonly findingFilter = signal<string>('');
  readonly assets = signal<Asset[]>([]);
  readonly totalAssets = signal<number>(0);
  readonly selectedAsset = signal<Asset | null>(null);

  // Pagination signals
  readonly currentCursor = signal<string | null>(null);
  readonly nextCursor = signal<string | null>(null);
  readonly cursorHistory = signal<(string | null)[]>([]);

  // Loading and error states
  readonly isLoadingPrincipals = signal<boolean>(false);
  readonly isLoadingSession = signal<boolean>(false);
  readonly isLoadingAssets = signal<boolean>(false);
  readonly isLoadingAssetDetail = signal<boolean>(false);

  readonly error = signal<string | null>(null);
  readonly selectedAssetDetailError = signal<string | null>(null);
  readonly liveStatusMessage = signal<string>('Console initialized');

  // Computed state
  readonly currentPage = computed(() => this.cursorHistory().length + 1);

  readonly selectedPrincipal = computed(() =>
    this.principals().find((p) => p.id === this.selectedPrincipalId()) || null
  );

  readonly availableTenants = computed<PrincipalTenant[]>(() => {
    const principal = this.selectedPrincipal();
    return principal?.tenants || [];
  });

  readonly activeTenantLabel = computed(() => {
    const tenantId = this.activeTenantId();
    const found = this.availableTenants().find((t) => t.id === tenantId);
    return found ? found.label : tenantId || 'None';
  });

  readonly filterDisplayName = computed(() => {
    switch (this.findingFilter()) {
      case 'affected':
        return 'Affected';
      case 'unaffected':
        return 'Unaffected';
      case 'needs_evidence':
        return 'Needs Evidence';
      default:
        return 'All Findings';
    }
  });

  ngOnInit(): void {
    this.fetchPrincipals();
  }

  ngOnDestroy(): void {
    this.cancelAllRequests();
  }

  private cancelAllRequests(): void {
    this.principalsSub?.unsubscribe();
    this.sessionSub?.unsubscribe();
    this.assetsSub?.unsubscribe();
    this.assetDetailSub?.unsubscribe();
  }

  /**
   * Fetch synthetic principals from GET /api/v1/demo/principals
   */
  fetchPrincipals(): void {
    this.cancelAllRequests();
    this.isLoadingPrincipals.set(true);
    this.error.set(null);
    this.liveStatusMessage.set('Loading synthetic identities...');

    this.principalsSub = this.http
      .get<PrincipalsResponse>('/api/v1/demo/principals')
      .subscribe({
        next: (res) => {
          this.isLoadingPrincipals.set(false);
          const items = res.items || [];
          this.principals.set(items);

          if (items.length > 0) {
            // Automatically select the first principal to establish a session
            this.switchPrincipal(items[0].id);
          } else {
            this.liveStatusMessage.set('No synthetic identities available');
          }
        },
        error: (err) => {
          this.isLoadingPrincipals.set(false);
          this.error.set(this.extractErrorMessage(err));
          this.liveStatusMessage.set('Failed to load identities');
        }
      });
  }

  /**
   * Handle user-initiated switch of synthetic identity
   */
  onPrincipalChange(principalId: string): void {
    if (!principalId || principalId === this.selectedPrincipalId()) {
      return;
    }
    this.switchPrincipal(principalId);
  }

  /**
   * Switch principal with strict sequence guard and state clearing
   */
  private switchPrincipal(principalId: string): void {
    // 1. Unsubscribe any in-flight requests immediately (RxJS cancellation)
    this.sessionSub?.unsubscribe();
    this.assetsSub?.unsubscribe();
    this.assetDetailSub?.unsubscribe();

    // 2. Increment sequence guard to ensure late responses are dropped
    const currentSeq = ++this.identitySequence;

    // 3. Clear earlier data completely
    this.bearerToken.set(null);
    this.assets.set([]);
    this.selectedAsset.set(null);
    this.selectedAssetDetailError.set(null);
    this.totalAssets.set(0);
    this.nextCursor.set(null);
    this.cursorHistory.set([]);
    this.currentCursor.set(null);
    this.error.set(null);

    this.selectedPrincipalId.set(principalId);
    this.isLoadingSession.set(true);
    this.liveStatusMessage.set(`Switching identity to ${principalId}...`);

    // 4. POST the selected principal to /api/v1/demo/session
    this.sessionSub = this.http
      .post<SessionResponse>('/api/v1/demo/session', { principal_id: principalId })
      .subscribe({
        next: (session) => {
          // Verify sequence guard
          if (this.identitySequence !== currentSeq) {
            return;
          }
          this.isLoadingSession.set(false);

          // Store bearer token ONLY in component state
          this.bearerToken.set(session.access_token);

          // Resolve permitted tenants
          const principal = this.principals().find((p) => p.id === principalId);
          const firstTenant = principal?.tenants?.[0]?.id || '';
          this.activeTenantId.set(firstTenant);

          this.liveStatusMessage.set(`Authenticated as ${principal?.label || principalId}`);

          if (firstTenant) {
            this.fetchAssets(currentSeq);
          }
        },
        error: (err) => {
          if (this.identitySequence !== currentSeq) {
            return;
          }
          this.isLoadingSession.set(false);
          this.error.set(this.extractErrorMessage(err));
          this.liveStatusMessage.set('Session authentication failed');
        }
      });
  }

  /**
   * Handle user-initiated switch of tenant
   */
  onTenantChange(tenantId: string): void {
    if (!tenantId || tenantId === this.activeTenantId()) {
      return;
    }
    this.assetDetailSub?.unsubscribe();
    this.assets.set([]);
    this.totalAssets.set(0);
    this.nextCursor.set(null);
    this.activeTenantId.set(tenantId);
    this.selectedAsset.set(null);
    this.selectedAssetDetailError.set(null);
    this.currentCursor.set(null);
    this.cursorHistory.set([]);
    this.fetchAssets();
  }

  /**
   * Set finding filter (affected, unaffected, needs_evidence, or all)
   */
  setFindingFilter(filter: string): void {
    if (this.findingFilter() === filter) {
      return;
    }
    this.findingFilter.set(filter);
    this.currentCursor.set(null);
    this.cursorHistory.set([]);
    this.fetchAssets();
  }

  /**
   * Refresh the asset list
   */
  refreshAssets(): void {
    this.fetchAssets();
  }

  /**
   * Fetch inventory assets with sequence guard
   */
  private fetchAssets(seq?: number): void {
    const token = this.bearerToken();
    const tenantId = this.activeTenantId();
    if (!token || !tenantId) {
      return;
    }

    const currentIdentitySeq = seq ?? this.identitySequence;
    const currentAssetReqSeq = ++this.assetRequestSequence;

    this.assetsSub?.unsubscribe();
    this.isLoadingAssets.set(true);
    this.error.set(null);

    let params = new HttpParams()
      .set('tenant_id', tenantId)
      .set('limit', '25');

    const finding = this.findingFilter();
    if (finding) {
      params = params.set('finding', finding);
    }

    const cursor = this.currentCursor();
    if (cursor) {
      params = params.set('after', cursor);
    }

    const headers = new HttpHeaders({
      Authorization: `Bearer ${token}`
    });

    this.assetsSub = this.http
      .get<AssetsResponse>('/api/v1/assets', { headers, params })
      .subscribe({
        next: (res) => {
          // Ensure no late response from previous identity or superseded request
          if (
            this.identitySequence !== currentIdentitySeq ||
            this.assetRequestSequence !== currentAssetReqSeq
          ) {
            return;
          }
          this.isLoadingAssets.set(false);
          this.assets.set(res.items || []);
          this.nextCursor.set(res.next_cursor || null);
          this.totalAssets.set(res.total ?? 0);

          this.liveStatusMessage.set(
            `Loaded ${res.items?.length || 0} assets for tenant ${this.activeTenantLabel()}`
          );

          // If a selected asset is present, update or retain its match
          const currentSelected = this.selectedAsset();
          if (currentSelected) {
            const updated = res.items?.find((a) => a.id === currentSelected.id);
            if (updated) {
              this.selectedAsset.set(updated);
            }
          }
        },
        error: (err) => {
          if (
            this.identitySequence !== currentIdentitySeq ||
            this.assetRequestSequence !== currentAssetReqSeq
          ) {
            return;
          }
          this.isLoadingAssets.set(false);
          this.error.set(this.extractErrorMessage(err));
          this.liveStatusMessage.set('Failed to load asset inventory');
        }
      });
  }

  /**
   * Keyset pagination: Go to next page
   */
  goToNextPage(): void {
    const next = this.nextCursor();
    if (!next || this.isLoadingAssets()) {
      return;
    }
    this.cursorHistory.update((stack) => [...stack, this.currentCursor()]);
    this.currentCursor.set(next);
    this.fetchAssets();
  }

  /**
   * Keyset pagination: Go to previous page
   */
  goToPreviousPage(): void {
    const history = [...this.cursorHistory()];
    if (history.length === 0 || this.isLoadingAssets()) {
      return;
    }
    const previousCursor = history.pop() ?? null;
    this.cursorHistory.set(history);
    this.currentCursor.set(previousCursor);
    this.fetchAssets();
  }

  /**
   * Select an asset and fetch its detailed evidence from GET /api/v1/assets/:id?tenant_id=...
   */
  selectAsset(asset: Asset): void {
    this.selectedAsset.set(asset);
    this.selectedAssetDetailError.set(null);
    this.liveStatusMessage.set(`Selected asset ${asset.name}`);

    const token = this.bearerToken();
    const tenantId = this.activeTenantId();
    if (!token || !tenantId) {
      return;
    }

    this.assetDetailSub?.unsubscribe();
    this.isLoadingAssetDetail.set(true);

    const headers = new HttpHeaders({
      Authorization: `Bearer ${token}`
    });
    const params = new HttpParams().set('tenant_id', tenantId);

    // Call GET /api/v1/assets/:id to verify site grants and fetch latest evidence
    this.assetDetailSub = this.http
      .get<Asset>(`/api/v1/assets/${asset.id}`, { headers, params })
      .subscribe({
        next: (detail) => {
          this.isLoadingAssetDetail.set(false);
          this.selectedAsset.set(detail);
        },
        error: (err) => {
          this.isLoadingAssetDetail.set(false);
          this.selectedAssetDetailError.set(this.extractErrorMessage(err));
        }
      });
  }

  /**
   * Close the evidence panel
   */
  closeEvidencePanel(): void {
    this.selectedAsset.set(null);
    this.selectedAssetDetailError.set(null);
    this.liveStatusMessage.set('Closed evidence panel');
  }

  /**
   * Retry the current failing action
   */
  retryCurrentAction(): void {
    if (!this.bearerToken()) {
      const principalId = this.selectedPrincipalId();
      if (principalId) {
        this.switchPrincipal(principalId);
      } else {
        this.fetchPrincipals();
      }
    } else {
      this.fetchAssets();
    }
  }

  /**
   * Format finding key to user-facing text
   */
  formatFinding(finding?: FindingType): string {
    switch (finding) {
      case 'affected':
        return 'Affected';
      case 'unaffected':
        return 'Unaffected';
      case 'needs_evidence':
        return 'Needs Evidence';
      default:
        return finding || 'Unknown';
    }
  }

  /**
   * Format observation ISO timestamp to readable UTC date
   */
  formatObservationDate(isoDate?: string): string {
    if (!isoDate) {
      return 'N/A';
    }
    try {
      const d = new Date(isoDate);
      return d.toUTCString();
    } catch {
      return isoDate;
    }
  }

  /**
   * Extract human-readable error detail from backend API error responses
   */
  private extractErrorMessage(error: any): string {
    if (error?.error?.detail) {
      return typeof error.error.detail === 'string'
        ? error.error.detail
        : JSON.stringify(error.error.detail);
    }
    if (error?.status === 401) {
      return 'Session expired or unauthenticated. Please select an identity.';
    }
    if (error?.status === 403) {
      return 'Access forbidden: identity lacks grants for the requested tenant.';
    }
    if (error?.status === 404) {
      return 'Asset not found or device is outside caller site grants.';
    }
    if (error?.message) {
      return error.message;
    }
    return 'An unexpected service error occurred. Please check network and proxy.';
  }
}

export default App;
