CREATE TABLE observation_events (
    ordinal bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    tenant_id text, event_id uuid, site_id text NOT NULL, asset_id text NOT NULL,
    source_id text NOT NULL, source_revision bigint NOT NULL CHECK (source_revision > 0),
    body jsonb NOT NULL, published_at timestamptz, processed_at timestamptz,
    PRIMARY KEY (tenant_id, event_id),
    UNIQUE (tenant_id, site_id, asset_id, source_id, source_revision),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES assets(tenant_id, id)
);
CREATE INDEX observation_outbox ON observation_events (ordinal) WHERE published_at IS NULL;
CREATE TABLE observation_watermarks (
    tenant_id text, site_id text, asset_id text, source_id text,
    accepted bigint NOT NULL CHECK (accepted > 0), applied bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, site_id, asset_id, source_id),
    CHECK (applied >= 0 AND applied <= accepted),
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES assets(tenant_id, id)
);
CREATE TABLE observation_quarantine (
    topic text, partition integer, message_offset bigint,
    tenant_id text, site_id text, event_id uuid, payload_hash text NOT NULL,
    reason text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(), repaired_at timestamptz,
    PRIMARY KEY (topic, partition, message_offset),
    FOREIGN KEY (tenant_id, event_id) REFERENCES observation_events,
    FOREIGN KEY (tenant_id, site_id) REFERENCES sites(tenant_id, id)
);
