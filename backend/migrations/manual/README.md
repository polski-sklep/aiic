# Manual, one-off corrections

**Nothing in this directory runs automatically.** The migration runner
(`app/database.py::discover_migrations`) walks `backend/migrations/` and skips
every entry whose suffix is not `.sql`; a *directory* has no suffix, so this
one is never opened. Verified: after adding this directory,
`python3 -m app.database` reported `applied=[] skipped=['0001'..'0005']`.

That is deliberate. The files here rewrite **existing production data**, which
is not something a deploy should do as a side effect of `git pull`. They are
run by hand, by a person who has read them, after taking a dump.

## Files

| File | What it corrects |
| --- | --- |
| `backfill_report_failed_status.sql` | The five evaluations recorded `completed` whose Report Writer produced no report, plus a `run_health` record for every historical run. |

## Procedure

```bash
# 1. Dump first. Non-negotiable — this is the only copy of the ledger.
ssh root@100.95.239.105 \
  "docker exec committee-postgres pg_dump -U committee -d committee | gzip" \
  > ~/aiic-backups/aiic-db-backup-$(date +%Y%m%d).sql.gz

# 2. Preview. The file's first two statements are SELECTs and change nothing.
#    Read the output. It names every row that would move and what it would
#    become.
ssh root@100.95.239.105 \
  "docker exec -i committee-postgres psql -U committee -d committee" \
  < backend/migrations/manual/backfill_report_failed_status.sql

# 3. The file's writes are inside a transaction that ends with ROLLBACK.
#    Nothing is committed on the first run. To apply, change the final
#    ROLLBACK to COMMIT and run it again.
```

Applying it twice is a no-op: the UPDATEs are guarded on the state they change.
