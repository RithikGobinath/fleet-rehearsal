CREATE TABLE hosted_identities (
    issuer text, subject text,
    principal_id text NOT NULL REFERENCES principals(id),
    PRIMARY KEY (issuer, subject)
);
