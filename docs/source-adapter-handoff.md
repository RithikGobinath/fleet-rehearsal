# Read-only evidence source

The local import test consumes [a synthetic export](../fixtures/synthetic-inventory-export.json) with an explicit provider, export time, and format version. It sends the `batch` to the existing tenant and site import route. The same grant checks and database transaction apply to every row. The test verifies successful import, identical replay, stale revision rejection, denial for the Harbor partner, and rollback of a batch containing a conflicting site record.

To run just this check against a disposable local Compose database, start the application and run:

```sh
docker compose exec -T api python -m unittest discover -s tests -p test_inventory.py -k import -v
```

For an authorized Cisco data source, record the provider API and version, tenant, endpoint allowlist, permitted fields, export timestamp, upstream revision, and SHA-256 of the source export. Agree the read-only identity and rate limit with the data owner. Translate the allowlisted fields into the import schema and preserve that provenance with the release evidence. Keep the connector opt-in and verify the same import tests with it disabled. Any external credential or connector is pending the data owner's decision.

## Real-device pilot decision

Keep the portfolio synthetic. A separate pilot proposal needs the asset owner's written scope, agreed lab assets, approved evidence retention, an operator responsible for change review, a rollback owner, and exit criteria for connectivity and recovery. The owner decides whether to proceed once the local evidence, authorized source and isolated environment are available.
