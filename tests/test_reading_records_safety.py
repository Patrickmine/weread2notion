"""Deployment-boundary tests, all HTTP/socket calls forbidden."""
import contextlib
import io
import os
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from weread2notion import reading_records as sync
from weread2notion.reading_records_workflow import workflow_args


class SafetyTests(unittest.TestCase):
    def setUp(self):
        for name in ('requests.sessions.Session.request', 'httpx.Client.send',
                     'socket.socket.connect', 'socket.create_connection'):
            guard = patch(name, side_effect=AssertionError('Network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)
        self.today = date(2026, 11, 8)

    def test_default_schedule_and_dispatch_are_read_only(self):
        for event in ('schedule', 'workflow_dispatch'):
            self.assertNotIn('--apply', workflow_args({'GITHUB_EVENT_NAME': event}, self.today))

    def test_auto_requires_both_acceptance_and_enable_flags(self):
        for accepted, enabled in (('', ''), ('true', ''), ('', 'true'), ('false', 'true'), ('TRUE', 'true')):
            env = {'GITHUB_EVENT_NAME': 'schedule', 'READING_RECORDS_FIRST_WRITE_ACCEPTED': accepted,
                   'READING_RECORDS_AUTO_WRITE_ENABLED': enabled}
            self.assertNotIn('--apply', workflow_args(env, self.today))
        env = {'GITHUB_EVENT_NAME': 'schedule', 'READING_RECORDS_FIRST_WRITE_ACCEPTED': 'true',
               'READING_RECORDS_AUTO_WRITE_ENABLED': 'true', 'READING_RECORDS_AUTO_MAX_WRITES': '10'}
        args = workflow_args(env, self.today)
        self.assertIn('--apply', args)
        parsed = sync.parse_args(args, self.today)
        self.assertEqual(parsed.max_writes, 10)
        self.assertEqual(parsed.until, self.today)

    def test_trial_requires_every_explicit_boundary(self):
        env = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'RECORDS_MODE': 'trial',
               'RECORDS_SINCE': '2026-08-28', 'RECORDS_UNTIL': '2026-08-29', 'RECORDS_MAX_WRITES': '2'}
        for key in ('RECORDS_SINCE', 'RECORDS_UNTIL', 'RECORDS_MAX_WRITES'):
            bad = dict(env); bad.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                workflow_args(bad, self.today)
        parsed = sync.parse_args(workflow_args(env, self.today), self.today)
        self.assertEqual((parsed.since, parsed.until, parsed.max_writes),
                         (date(2026, 8, 28), date(2026, 8, 29), 2))

    def test_old_unbounded_apply_is_refused_before_any_client(self):
        with patch.object(sync, 'Client') as client, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                sync.main(['--since', '2026-08-01', '--apply'])
            client.assert_not_called()

    def test_invalid_ranges_and_budgets_are_rejected(self):
        for tail in (['--until', '2026-11-09', '--max-writes', '2'],
                     ['--until', '2026-08-27', '--max-writes', '2'],
                     ['--until', '2026-08-29', '--max-writes', '0'],
                     ['--until', '2026-08-29', '--max-writes', '-1'],
                     ['--until', '2026-08-29', '--max-writes', 'x']):
            with self.subTest(tail=tail), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                sync.parse_args(['--apply', '--since', '2026-08-28'] + tail, self.today)

    def test_budget_includes_period_pages_and_stops_before_first_write(self):
        records = MagicMock()
        plan = ({('周', date(2026, 8, 31), date(2026, 9, 7)): 'week'},
                [(None, {'时长': {'number': 60}}, {})])
        with self.assertRaisesRegex(ValueError, '2 page writes'):
            sync.apply_with_limit(records, plan, 1)
        records.apply.assert_not_called()
        sync.apply_with_limit(records, plan, 2)
        records.apply.assert_called_once_with(plan)

    def test_unknown_days_and_upper_date_never_expand_to_today(self):
        gateway = MagicMock()
        stamps = {str(int(datetime(2026, 8, d, tzinfo=sync.TZ).timestamp())): d for d in (28, 29, 30)}
        gateway.request.return_value = {'readTimes': stamps, 'totalReadTime': sum(stamps.values())}
        selected = sync.read_months(gateway, date(2026, 8, 28), date(2026, 8, 29))
        self.assertEqual(set(selected), {date(2026, 8, 28), date(2026, 8, 29)})
        self.assertNotIn(date(2026, 11, 8), selected)

    def test_dedicated_root_required_no_bookshelf_secret_fallback(self):
        with patch.dict(os.environ, {'NOTION_PAGE': '11111111111111111111111111111111'}, clear=True):
            with patch.object(sync, 'Client') as client, self.assertRaisesRegex(ValueError, 'dedicated'):
                sync.main(['--since', '2026-08-28', '--until', '2026-08-29'])
            client.assert_not_called()

    def test_main_preview_never_calls_apply_and_passes_exact_range(self):
        env = {'READING_RECORDS_PAGE': '11111111111111111111111111111111',
               'NOTION_TOKEN': 'synthetic', 'WEREAD_API_KEY': 'synthetic'}
        with patch.dict(os.environ, env, clear=True), patch.object(sync, 'Client') as client, \
                patch.object(sync, 'Records') as records, patch.object(sync, 'WeReadGatewayClient'), \
                patch.object(sync, 'read_months', return_value={date(2026, 8, 29): 60}) as read:
            records.return_value.plan.return_value = ({}, [(None, {}, {})])
            records.return_value.counts = {'requests': 0}
            with contextlib.redirect_stdout(io.StringIO()):
                sync.main(['--since', '2026-08-28', '--until', '2026-08-29'])
            self.assertEqual(read.call_args.args[1:], (date(2026, 8, 28), date(2026, 8, 29)))
            records.return_value.apply.assert_not_called()
            self.assertEqual(client.call_args.kwargs['max_retries'], 0)

    def test_main_over_budget_never_calls_apply(self):
        env = {'READING_RECORDS_PAGE': '11111111111111111111111111111111',
               'NOTION_TOKEN': 'synthetic', 'WEREAD_API_KEY': 'synthetic'}
        with patch.dict(os.environ, env, clear=True), patch.object(sync, 'Client'), \
                patch.object(sync, 'Records') as records, patch.object(sync, 'WeReadGatewayClient'), \
                patch.object(sync, 'read_months', return_value={date(2026, 8, 29): 60}):
            records.return_value.plan.return_value = ({('周',): 'week'}, [(None, {}, {})])
            records.return_value.counts = {'requests': 0}
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                sync.main(['--apply', '--since', '2026-08-29', '--until', '2026-08-29', '--max-writes', '1'])
            records.return_value.apply.assert_not_called()

    def test_unsupported_mode_or_event_refuses_execution(self):
        for env in ({'GITHUB_EVENT_NAME': 'push'},
                    {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'RECORDS_MODE': 'apply_all'}):
            with self.assertRaises(ValueError):
                workflow_args(env, self.today)

    def test_workflow_wires_guarded_runner_without_bookshelf_config(self):
        text = Path('.github/workflows/reading_records.yml').read_text()
        self.assertIn('run: python -m weread2notion.reading_records_workflow', text)
        self.assertNotIn('--apply', text)
        self.assertNotIn('NOTION_PAGE:', text)
        for name in ('READING_RECORDS_PAGE', 'READING_RECORDS_FIRST_WRITE_ACCEPTED',
                     'READING_RECORDS_AUTO_WRITE_ENABLED'):
            self.assertIn(name + ': ${{ vars.' + name + ' }}', text)
        for name in ('mode', 'since', 'until', 'max_writes'):
            self.assertIn('${{ inputs.' + name + ' }}', text)
