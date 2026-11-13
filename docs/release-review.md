# Security and operations review

Claude Opus 5.5 at high effort reviewed hosted identity, AWS trust, and release artifact verification. Gemini 3.8 Flash at high effort reviewed browser sign-in, keyboard recovery, and the demo sequence. The implementation and automated checks address these findings.

| Finding | Control and evidence |
| --- | --- |
| An identity token or token for a different client could be accepted by the API. | Hosted verification fixes the issuer, API audience, signing algorithm, and authorized SPA client. The local integration suite signs test tokens and checks expired, mismatched, unregistered, and revoked subjects. |
| A dev session could cross the hosted boundary. | Hosted startup requires issuer, audience, and client ID. The demo issuer is unavailable in hosted mode. The browser uses the provider's SDK with PKCE and an in-memory token cache. |
| Tenant permissions could be reused after scope changes. | Tenant and site queries resolve grants from PostgreSQL. Scope rejection occurs before cached write receipts or audit cursors are read. The hosted boundary test checks cross-tenant resources and grant removal. |
| A workflow could publish images with untrusted code or a mutable tag. | The AWS publisher trust requires the immutable repository ID, main ref, release workflow path, and event. Publication checks the aggregate result on that source commit, scans before push, and records immutable registry digests. The AWS role can publish the two repository images only. |
| The operator could select the wrong artifact or promote a modified download. | The promotion script checks the ZIP digest from GitHub, the reviewed source SHA, and each scan report hash. The test rejects a mismatched archive digest, an unapproved source, and mutable image references. |

Verify Auth0 callback and logout URLs against the staging hostname before opening access. During the sandbox run, use the AWS policy simulator and a real GitHub OIDC session to confirm the publisher cannot assume a deploy role or push outside the named repositories. Retain the manifest and verification artifacts with the release candidate. The repository's current private GitHub plan does not support required branch protection, so the maintainer controls merges and the release workflow independently checks the exact source commit.

Trivy reported high-severity Debian base packages for which the scan listed no fixed version. The API runtime now uses Ubuntu 24.04 LTS with distribution-managed Python 3.12 and the same locked application dependencies. The gate blocks all high and critical findings and retains the full scan report for each image it scans.
