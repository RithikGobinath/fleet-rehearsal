ALTER TABLE grants ADD COLUMN role text NOT NULL DEFAULT 'viewer'
    CHECK (role IN ('viewer', 'partner_proposer', 'customer_approver', 'operator'));

-- Preserve the intended roles of the original synthetic identities.
UPDATE grants SET role = 'operator'
WHERE (principal_id, tenant_id, site_id) IN (
    ('demo-northstar', 'northstar', 'harbor'),
    ('demo-northstar', 'northstar', 'river'),
    ('demo-summit', 'summit', 'ridge')
);
UPDATE grants SET role = 'partner_proposer'
WHERE (principal_id, tenant_id, site_id) = ('demo-partner', 'northstar', 'harbor');
