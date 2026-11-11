-- Also adopts local demo databases created before migration tracking.
CREATE TABLE IF NOT EXISTS tenants (
    id text PRIMARY KEY,
    label text NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    tenant_id text REFERENCES tenants(id),
    id text,
    label text NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE TABLE IF NOT EXISTS principals (
    id text PRIMARY KEY,
    label text NOT NULL
);
CREATE TABLE IF NOT EXISTS grants (
    principal_id text REFERENCES principals(id),
    tenant_id text,
    site_id text,
    PRIMARY KEY (principal_id, tenant_id, site_id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id)
);
CREATE TABLE IF NOT EXISTS assets (
    tenant_id text,
    id text,
    site_id text NOT NULL,
    name text NOT NULL,
    product text NOT NULL,
    running_release text NOT NULL,
    release_status text NOT NULL CHECK (release_status IN ('affected', 'unaffected', 'unknown')),
    trunk_port boolean,
    trustsec boolean,
    macsec boolean,
    observed_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id)
);
CREATE INDEX IF NOT EXISTS assets_by_site ON assets (tenant_id, site_id, id);
CREATE TABLE IF NOT EXISTS demo_sessions (
    token_hash bytea PRIMARY KEY,
    principal_id text NOT NULL REFERENCES principals(id),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS demo_sessions_expiry ON demo_sessions (expires_at);

CREATE OR REPLACE VIEW inventory_evidence AS
SELECT a.*, s.label AS site_name,
    CASE
        WHEN release_status = 'unaffected' THEN 'unaffected'
        WHEN release_status = 'unknown' THEN 'needs_evidence'
        WHEN trunk_port OR trustsec OR macsec THEN 'affected'
        WHEN trunk_port IS NULL OR trustsec IS NULL OR macsec IS NULL THEN 'needs_evidence'
        ELSE 'unaffected'
    END AS finding,
    CASE
        WHEN release_status = 'unaffected' THEN 'The modeled release is outside the affected set.'
        WHEN release_status = 'unknown' THEN 'Release applicability evidence is missing.'
        WHEN trunk_port OR trustsec OR macsec THEN
            'The modeled release is affected and an applicable feature is enabled.'
        WHEN trunk_port IS NULL OR trustsec IS NULL OR macsec IS NULL THEN
            'Configuration evidence is incomplete for an affected modeled release.'
        ELSE 'The modeled device has none of the applicable features enabled.'
    END AS reason
FROM assets a
JOIN sites s ON (s.tenant_id, s.id) = (a.tenant_id, a.site_id);
