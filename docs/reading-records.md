# Daily reading records

Independent entry point: `python -m weread2notion.reading_records`. Existing bookshelf code and `weread.yml` are unchanged. Uses the existing API Key, NOTION_TOKEN and the current Notion API version; no Cookie, old Pro initialization, schema migration or database recreation.

## Confirmed template contract

Read-only inspection confirmed the selected original template, not its duplicate:

- 日: 标题 (title), 日期 (date), 时长 and 时间戳 (numbers), 周/月/年 reciprocal relations.
- 周/月/年: 标题 and date range; 时长 is a SUM rollup of 日.时长 through 每日阅读统计.
- The monthly chart sums 月.阅读时长（小时） grouped by month; the annual chart sums 年.阅读时长小时 grouped by year. Maintain daily relations so existing rollups and formulas can recalculate. Existing chart settings and formulas are never modified.
- The books-completed chart depends on bookshelf status and year relations, not daily duration; this module does not repair unrelated book-count statistics.

At runtime the module verifies actual schemas, relation destinations, reciprocal links and SUM rollups before writing. Names alone are insufficient. Multiple matching databases, data sources, daily dates, duplicate period ranges and conflicting nonempty relations fail before apply. Existing daily titles, dates, timestamps, manual content, bookshelf/note/highlight relations and all older records outside the selected interval are preserved. Only duration changes and missing period links are patched. Missing period pages are created in existing databases, never replacing databases. New period pages contain properties; custom template page bodies are not copied.

## Configuration and execution

Reuse existing secrets. `READING_RECORDS_PAGE` is an optional **repository variable**, containing the approved template ROOT page URL/ID (not the bookshelf database). If absent, the existing NOTION_PAGE secret must point to that root; a bookshelf-only ID fails closed before writes. Do not change bookshelf Secrets to satisfy this module. If needed, set only the new repository variable during later deployment acceptance. No credentials or private page IDs are committed.

Default execution is read-only preview:

```bash
python -m weread2notion.reading_records --since 2026-08-01
```

Only after explicit production acceptance:

```bash
python -m weread2notion.reading_records --since 2026-08-01 --apply
```

The new workflow declares 09:00 Asia/Shanghai (`0 1 * * *` UTC), one daily job with a concurrency lock; the bookshelf job remains at 08:00. It has no push/pull_request trigger. Manual dispatch is available but has not been invoked. Once merged, the scheduled workflow uses --apply. Neither this PR nor tests perform production writes. Merging is therefore a separate deployment authorization, with preview and backup checks first.

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

1. Confirm backup, intended root and existing integration access to all four databases. If NOTION_PAGE targets only the bookshelf, configure READING_RECORDS_PAGE as a repository variable; keep existing Secrets unchanged.
2. Run a read-only preview from a trusted local environment using existing credentials. Review months, buckets, duplicate/conflict failures and proposed create/update counts. No sharing credentials in chat or artifacts.
3. For a narrow first write trial after separate authorization, use a recent --since date: compare one existing day and one missing day to the official seconds; confirm preserved manual data and period links. --since is a lower bound through today, not a one-day upper bound.
4. Confirm week/month/year SUM rollups, monthly/annual charts, original bookshelf status/time and notes. A chart pass does not prove the NotionHub heatmap refreshed.
5. Repeat the same interval: unchanged durations/links must produce zero writes. Resolve duplicates or conflicting links manually before any retry; do not delete/recreate to bypass errors.
6. Separately authorize full August-to-current apply and merging/enabling the daily schedule. Observe the next scheduled run and compare displayed data. No weekly full task is needed. Archive neither the original template nor its copy.

## Local verification

`PYTHONPATH=src ../test-venv/bin/python -m unittest discover -s tests -v`

25 tests passed: original 17 bookshelf tests plus 8 reading-record tests, with HTTP/socket transports blocked and synthetic data. Covers historical months, seconds, missing dates, timestamp/day boundaries, ISO week/year boundaries, existing/new days, missing period/link repair, repeat zero writes, duplicate dates/periods, schema mismatch, conflicting relations and pagination. No real Notion writes; actual live Notion API schema validation, relation propagation, default page bodies and chart refresh remain production acceptance items.
