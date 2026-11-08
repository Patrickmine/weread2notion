# Daily reading records

Independent entry point: `python -m weread2notion.reading_records`. Existing bookshelf code and `weread.yml` are unchanged. Uses the existing API Key, NOTION_TOKEN and the current Notion API version; no Cookie, old Pro initialization, schema migration or database recreation.

## Confirmed template contract

Read-only inspection confirmed the selected original template, not its duplicate:

- 日: 标题 (title), 日期 (date), 时长 and 时间戳 (numbers), 周/月/年 reciprocal relations.
- 周/月/年: 标题 and date range; 时长 is a SUM rollup of 日.时长 through 每日阅读统计.
- The monthly chart sums 月.阅读时长（小时） grouped by month; the annual chart sums 年.阅读时长小时 grouped by year. Maintain daily relations so existing rollups and formulas can recalculate. Existing chart settings and formulas are never modified.
- The books-completed chart depends on bookshelf status and year relations, not daily duration; this module does not repair unrelated book-count statistics.

At runtime the module verifies actual schemas, relation destinations, reciprocal links and SUM rollups before writing. Names alone are insufficient. Multiple matching databases/data sources, duplicate involved daily dates/cycles and conflicting nonempty relations fail before apply. Duplicate checks exclude dates without returned API buckets and unrelated historical cycles. Existing daily titles, dates, timestamps, manual content, bookshelf/note/highlight relations and all older records outside the selected interval are preserved. Only duration changes and missing period links are patched. Missing period pages are created in existing databases, never replacing databases. New period pages contain properties; custom template page bodies are not copied.

## First deployment: read-only by default

The guarded workflow declares 09:00 Asia/Shanghai (`0 1 * * *` UTC), with no push/PR trigger. Schedule and manual dispatch default to read-only preview. The bookshelf workflow and its Secrets remain unchanged.

`READING_RECORDS_PAGE` is now a REQUIRED dedicated repository variable containing the confirmed ROOT page URL/ID. The CLI no longer falls back to the bookshelf NOTION_PAGE Secret. The dedicated variable has been set and read back to match the user-confirmed original template. Existing NOTION_TOKEN and WEREAD_API_KEY Secrets are only referenced, never modified. Root/schema discovery fails closed if integration access or the four database relationships are incorrect.

### Preview and bounded first trial

No trial has been run. First preview a deliberately small inclusive date window:

```bash
python -m weread2notion.reading_records --since 2026-08-28 --until 2026-08-29 --max-writes 2
```

The prior real-data snapshot shows August 28 already exists unchanged, August 29 is missing (287 seconds), and their week/month/year pages already exist. Therefore this window is expected to propose one day creation and no existing-day/period changes. Source data can change: inspect a fresh preview before approval.

Only after separate user authorization, use the identical window and cap:

```bash
python -m weread2notion.reading_records --since 2026-08-28 --until 2026-08-29 --max-writes 2 --apply
```

`--apply` requires all THREE explicit arguments (--since, --until, --max-writes). No missing argument is silently filled for writes. Dates must satisfy since <= until <= today. Missing API dates remain unknown. The upper date is passed to the unchanged monthly-fetch/plan functions, so an old trial window cannot expand to the current day.

The cap counts **all logical page write operations**, including period creates, day creates, duration updates and missing-link repairs. If the entire plan exceeds it, no apply is called; there is no partial first-N truncation. Notion SDK automatic retries are disabled for this entry point to avoid unbudgeted uncertain creates. A failed call can still have an ambiguous server-side outcome; preview before retrying.

In GitHub manual dispatch, choose mode=preview first with since=2026-08-28 and until=2026-08-29. After separate approval, mode=trial additionally requires max_writes=2; all three inputs are mandatory. Inputs are passed as environment values to a Python argument list, never interpolated into a shell. Trial mode does not enable future automatic writes or set acceptance flags.

### Future automatic writes: two closed gates

After first real-write acceptance passes, the user must explicitly authorize setting BOTH repository variables to the exact lowercase string `true`:

- READING_RECORDS_FIRST_WRITE_ACCEPTED
- READING_RECORDS_AUTO_WRITE_ENABLED

Missing/false/either-one-only means scheduled runs remain previews. The code does not set either variable and cannot independently certify human acceptance. Both are presently absent. Once both are authorized, scheduled writes use an explicit end date (that run's Beijing today) and a total cap from READING_RECORDS_AUTO_MAX_WRITES (default 100). Over-budget plans stop before any write. Manual preview always remains read-only even if automatic writes are enabled.

No merge, workflow dispatch, real Notion write or automatic-write enablement was performed during this safety stage. The old workflow invocation with bare --apply now fails before API calls instead of doing unbounded writes; it still needs the guarded YAML replacement before claiming default-preview deployment is ready.

## Historical backfill and data rules

The daily job rereads monthly data from August 2026 through the current month and writes only differences. This deliberately favors automatic gap recovery over a persisted checkpoint or a separate weekly full job. It also rechecks older source corrections. Costs increase by one monthly read each month; narrowing that window later requires an explicit policy decision.

Official `/readdata/detail` monthly `readTimes` maps Beijing-midnight Unix timestamps to seconds. Real read-only sampling confirmed 31 daily buckets for August and daily buckets through November 7 for the current month; sums matched totalReadTime. The API includes reading/listening duration. Keep seconds in 日.时长, let existing display formulas convert units.

Every response is validated for integer nonnegative duration, bucket month/midnight, and reconciliation with totalReadTime. Missing dates are UNKNOWN, not zero; only explicit zero buckets can update a day to zero. A zero-row or malformed response does not justify fabricating a calendar. The entire API interval and Notion plan are checked before apply. No history is deleted; changed source duration can replace that day's existing number as explicitly requested, while other properties are preserved.

Duplicate protection is idempotent within a run and across completed sequential runs. The workflow prevents two copies of itself running simultaneously. Notion has no transactional unique date constraint: an independent external writer or an ambiguous create failure can still race; inspect duplicates before retrying uncertain failures. Do not run another reading-record writer concurrently. API errors propagate as failures rather than clearing records. Partial apply is possible; rerun preview before recovery.

## API request estimate

For 2026-08 through 2026-11: **4 official monthly reads** per run, excluding retries. Read-only sampling is already done and is not repeated by tests.

Notion reads: `H + 8 + Pd + Pw + Pm + Py`, where H is the root/container block pagination calls, 8 retrieves the four databases and four schemas, and P is each date-filtered query's page count (100 rows/page). Daily rows Aug 1–Nov 8 span at most 100 calendar dates if unique; normally Pd=1, period sets also normally fit one page each. Actual counts are printed in preview. More existing duplicates or periods increase query pages.

Writes: `U + N + W + M + Y`, where U is changed existing days or missing links, N new daily pages, and W/M/Y missing period pages. One update per changed day even if multiple fields change. No changes implies zero writes. At most 100 daily creates/updates for Aug 1–Nov 8, plus genuinely missing periods; the observed latest existing day is Aug 28, but no exact missing count is claimed without a full preview. SDK retries add requests. Existing database/period pages are cached within one plan. Rollups require no separate writes.

## Heatmap: distinct service, not restored by this PR

Actual template embed is a NotionHub heatmap service URL, unlike old Pro's github_heatmap -> SVG -> repository push -> embed pipeline. Its activation information is deliberately not copied into code, logs or this document. This module neither changes the embed nor requests that credential-bearing third-party URL.

Preferred recovery: use the existing NotionHub service's supported resync/re-authentication flow, then verify displayed dates and duration against the official daily data. The current service's refresh contract and availability are unverified; no refresh endpoint or parameter is invented. The cached URL timestamp is not evidence of updated content.

If that service cannot be restored, a separate approved follow-up can generate a fresh annual SVG from official daily data, publish it to an approved image location, and update only the confirmed heatmap block. Fetch January–July as well for a complete annual image; the August backfill alone is insufficient. Verify an actual recent date and newly generated content before claiming success. Do not relabel an old SVG/static screenshot as current. This alternative is documented, not implemented or deployed here.

## Acceptance steps (not performed against production)

1. Replace the existing reading_records.yml on the SAME PR branch with the delivered guarded file. Current Git credential lacks workflow scope; do not remove or weaken guards to bypass upload restrictions. Confirm both write-enabling variables remain absent/false and the dedicated root variable matches the selected template.
2. Review PR and separately authorize merge only after its default-preview workflow is complete. Merging alone must not enable writes. Check the first scheduled preview and integration schema/permissions; do not alter bookshelf Secrets.
3. Obtain a fresh read-only August 28–29 preview. Verify date bounds, complete proposed write count <=2, correct existing period IDs, and no changes to other dates. If conflicts or additional period creates exceed the cap, stop; do not simply raise it.
4. After separate approval, perform that exact trial with max_writes=2. Compare official seconds, retained manual data and period relations; verify rollups/charts. Repeat preview and expect zero new changes. Heatmap remains a separate service and is not certified by this trial.
5. Record user acceptance of the first write. Only then, with explicit authorization, set FIRST_WRITE_ACCEPTED and AUTO_WRITE_ENABLED true. A successful API response alone must not set these flags.
6. Historical August-to-current apply still requires separately reviewed dates/budget (the prior plan required 91 page operations); it is never a side effect of the first narrow trial. Avoid independent concurrent writers and resolve any uncertain create outcome before retrying.

## Local verification

`PYTHONPATH=src ../test-venv/bin/python -m unittest discover -s tests -v`

44 tests validated: original 32 tests plus 12 safety tests (42 passed initially; two mock-counter fixture omissions were fixed and both affected tests passed targeted recheck), with HTTP/socket transports blocked and synthetic data. Covers historical months, seconds, missing dates, timestamp/day boundaries, ISO week/year boundaries, existing/new days, missing period/link repair, repeat zero writes, duplicate dates/periods, schema mismatch, conflicting relations and pagination. No real Notion writes; actual live Notion API schema validation, relation propagation, default page bodies and chart refresh remain production acceptance items.

## Legacy period compatibility and latest read-only preview

Existing natural periods can use either an inclusive final day or the first day of the following period as the end date. Both map to one logical cycle; existing page IDs, dates and day relations are retained. New periods use the legacy **following-period first day** rule (Monday to next Monday; month first to next month first; January 1 to next January 1). This includes ISO weeks crossing a year boundary.

Queries and duplicate checks are restricted to dates actually returned by the API and their needed natural periods, including a week starting before --since or before January. March/July monthly duplicates outside an August backfill do not block it and are never edited. Two pages for an involved cycle still abort, even if one uses an inclusive end and the other an exclusive end, or an existing day already links to one of them. An incompatible target date range also aborts instead of creating a duplicate.

2026-11-08 fresh read-only preview: 99 official days (August 1–November 7), 28 existing days, **71 new / 7 duration updates / 21 unchanged**. The 7 existing updates contain only 时长, with original period links untouched. Missing periods: 10 weeks, 3 months, 0 years. November 8 remains unknown and is not created as zero. Conditional write estimate is 91 page operations (71+7+10+3), excluding retries, not actual writes.

Preview used fresh Notion connector rows and fresh monthly API responses, then the exact Records.plan implementation with a write-blocked mock client; apply was not called. A local Notion integration token is unavailable, so this is not an end-to-end SDK authentication/schema-discovery check. Production integration permissions, reciprocal rollup propagation and charts remain acceptance items. No code/Secret/template/workflow outside these compatibility changes was modified; no Action was dispatched.
