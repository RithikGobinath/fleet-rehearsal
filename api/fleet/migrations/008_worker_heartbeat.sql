CREATE TABLE worker_heartbeats (
    worker text PRIMARY KEY CHECK (worker IN ('rehearsal', 'observation-relay', 'observation-projector')),
    last_seen timestamptz NOT NULL
);
