# Product sync single-file service 1.2.4

Deploy only `product_sync_service.py`. Preserve the existing `.env` and `sync_state`.
The existing Python 3.10 environment, dependencies and scheduled `--apply` command remain usable.

## Changes

- Retains SQL timestamp fractional-second compatibility on Python 3.10 and newer.
- Separates upstream business changes (`changed`), quality-flag changes (`quality_only`) and version/provenance updates (`metadata_only`).
- Metadata plans write only dates, provenance and synchronization records. Quality-only plans additionally write quality and review fields; neither rewrites business parameters.
- Newly detected quality risks still require review; removing an automatic flag does not automatically approve products already awaiting review.
- Keeps both source values when they disagree. No arbitrary source priority or guessed engineering equivalence.
- Produces `review_items.csv` (UTF-8 BOM, with spreadsheet formula protection) and detailed JSONL explanations inside each run folder.
- Explains version skips, reports Python/service versions and checks the plan hash before product writes.
- Preserves manual ownership, concurrency checks, change limits, source completeness checks, write verification, missing-product preservation and separate price data.

## Commands

```bash
python product_sync_service.py --version
python -u product_sync_service.py --reuse-snapshot SAVED_RUN_ID --state-dir /path/to/sync_state
python -u product_sync_service.py --apply --state-dir /path/to/sync_state
```

`--reuse-snapshot` performs a read-only database comparison from an existing complete snapshot and creates local reports. It does not request the source API. Historical snapshots remain preview-only.
`--apply` obtains a fresh complete source snapshot, applies guarded changes, and verifies the writes.

## Verification

Python 3.10 and Python 3.12 unit regressions, real MongoDB integration, and private uploaded-plan replay were performed. Private source records, reports, connection strings and keys are not included in this delivery.

For optional local regression checks, run `python delivery/run_sync124_tests.py` from the downloaded branch. This uses the actual single-file service and makes no network requests. CSV review codes are text-protected; JSONL and database codes remain unchanged.
