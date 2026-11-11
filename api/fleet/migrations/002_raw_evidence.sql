ALTER TABLE assets ADD COLUMN raw_evidence jsonb;

UPDATE assets SET raw_evidence = jsonb_build_object(
    'running_release', running_release,
    'release_status', release_status,
    'trunk_port', trunk_port,
    'trustsec', trustsec,
    'macsec', macsec,
    'observed_at', observed_at
);

ALTER TABLE assets
    ALTER COLUMN raw_evidence SET NOT NULL,
    ADD CHECK (jsonb_typeof(raw_evidence) = 'object');
