ALTER TABLE assets ADD COLUMN source_revision bigint NOT NULL DEFAULT 1
    CHECK (source_revision > 0);
