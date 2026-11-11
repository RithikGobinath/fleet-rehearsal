# Observation event contract

An observation event has a UUID event ID, tenant ID, site ID, asset ID, source ID, positive source revision, UTC observation and receipt timestamps, a schema version, and the new evidence. The first schema is version 1. Source revisions are comparable only within the same tenant, asset, and source. The Kafka partition key combines the tenant and asset IDs.

The API will accept evidence and write the outbox row in the same transaction. The relay publishes after commit and retries if broker acknowledgement is uncertain. A consumer deduplicates on event ID and applies a newer source revision in the transaction that updates evidence. It acknowledges the broker offset after commit. A duplicate or older revision leaves the projection unchanged.

| Sequence | Accepted watermark | Applied watermark | Rehearsal effect |
| --- | --- | --- | --- |
| Revision 4 accepted and projected | 4 | 4 | Evaluate freshness and policy against revision 4. |
| Revision 5 accepted, relay pending | 5 | 4 | Block with pending evidence for this dependency. |
| Revision 5 applied | 5 | 5 | Revalidate using the new observation. |
| The revision 5 event is delivered again | 5 | 5 | Preserve the same result. |
| A different event for revision 3 arrives late | 5 | 5 | Preserve the newer evidence. |

The start transaction will read both watermarks under the same site guard used for relevant evidence updates. The device observation time determines freshness. Receipt time and source revision provide audit context and ordering.
