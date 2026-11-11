CREATE TABLE rehearsal_snapshots (
    tenant_id text, site_id text, revision bigint CHECK (revision > 0), graph jsonb NOT NULL,
    PRIMARY KEY (tenant_id, site_id, revision),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id)
);
CREATE TABLE rehearsal_policies (
    tenant_id text, site_id text, revision bigint CHECK (revision > 0), policy jsonb NOT NULL,
    PRIMARY KEY (tenant_id, site_id, revision),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id)
);
CREATE TABLE rehearsal_sites (
    tenant_id text, site_id text, snapshot_revision bigint NOT NULL, policy_revision bigint NOT NULL,
    clock_at timestamptz NOT NULL, fail_canary boolean NOT NULL DEFAULT false,
    PRIMARY KEY (tenant_id, site_id),
    FOREIGN KEY (tenant_id, site_id, snapshot_revision) REFERENCES rehearsal_snapshots,
    FOREIGN KEY (tenant_id, site_id, policy_revision) REFERENCES rehearsal_policies
);
CREATE TABLE plan_revisions (
    tenant_id text, site_id text, plan_id text, revision bigint CHECK (revision > 0),
    snapshot_revision bigint NOT NULL, policy_revision bigint NOT NULL,
    proposer text NOT NULL REFERENCES principals, fingerprint text NOT NULL, document jsonb NOT NULL,
    PRIMARY KEY (tenant_id, site_id, plan_id, revision),
    FOREIGN KEY (tenant_id, site_id, snapshot_revision) REFERENCES rehearsal_snapshots,
    FOREIGN KEY (tenant_id, site_id, policy_revision) REFERENCES rehearsal_policies
);
CREATE TABLE rehearsal_audit (
    id bigint, tenant_id text NOT NULL, site_id text NOT NULL,
    actor_id text NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), correlation_id uuid NOT NULL,
    kind text NOT NULL, context jsonb NOT NULL,
    PRIMARY KEY (tenant_id, site_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id)
);
CREATE TABLE plan_approvals (
    tenant_id text, site_id text, plan_id text, revision bigint, fingerprint text NOT NULL,
    approved_by text NOT NULL REFERENCES principals, approved_at timestamptz NOT NULL DEFAULT now(),
    audit_id bigint NOT NULL,
    PRIMARY KEY (tenant_id, site_id, plan_id, revision),
    FOREIGN KEY (tenant_id, site_id, plan_id, revision) REFERENCES plan_revisions,
    FOREIGN KEY (tenant_id, site_id, audit_id) REFERENCES rehearsal_audit
);
CREATE TABLE rehearsal_runs (
    tenant_id text, site_id text, run_id text, plan_id text NOT NULL, revision bigint NOT NULL,
    wave_number integer NOT NULL CHECK (wave_number > 0), snapshot_revision bigint NOT NULL,
    actor_id text NOT NULL REFERENCES principals, correlation_id uuid NOT NULL, started_at timestamptz,
    state text NOT NULL CHECK (state IN ('queued', 'blocked', 'running', 'succeeded', 'paused',
        'partial_failure', 'rolled_back', 'failed')),
    reason text, audit_id bigint, devices jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (tenant_id, site_id, run_id),
    FOREIGN KEY (tenant_id, site_id, plan_id, revision) REFERENCES plan_approvals,
    FOREIGN KEY (tenant_id, site_id, snapshot_revision) REFERENCES rehearsal_snapshots,
    FOREIGN KEY (tenant_id, site_id, audit_id) REFERENCES rehearsal_audit
);
CREATE UNIQUE INDEX rehearsal_one_active_site ON rehearsal_runs (tenant_id, site_id)
    WHERE state IN ('queued', 'running', 'paused');
CREATE UNIQUE INDEX rehearsal_one_started_wave ON rehearsal_runs (tenant_id, site_id, plan_id, revision, wave_number)
    WHERE started_at IS NOT NULL;
CREATE TABLE rehearsal_receipts (
    tenant_id text REFERENCES tenants, actor_id text REFERENCES principals, operation text, request_key text,
    digest text NOT NULL, status integer, body jsonb, location text,
    PRIMARY KEY (tenant_id, actor_id, operation, request_key)
);
