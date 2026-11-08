"""Synthetic contract tests; production HTTP transports are forbidden."""
import copy
import unittest
from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

with patch('dotenv.load_dotenv'):
    from weread2notion.reading_records import Records, TZ, date_value, period, read_months


def prop_date(start, end=None):
    return {'date': {'start': start.isoformat(), 'end': end.isoformat() if end else None}}


class ReadingRecordTests(unittest.TestCase):
    def setUp(self):
        for name in ('requests.sessions.Session.request', 'httpx.Client.send',
                     'socket.socket.connect', 'socket.create_connection'):
            guard = patch(name, side_effect=AssertionError('Network forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def gateway(self, values):
        gateway = MagicMock()
        gateway.request.return_value = values
        return gateway

    def test_history_months_use_daily_seconds_without_filling_missing_days(self):
        gateway = MagicMock()
        def response(api, mode, baseTime):
            self.assertEqual((api, mode), ('/readdata/detail', 'monthly'))
            return {'readTimes': {str(baseTime): 3601}, 'totalReadTime': 3601}
        gateway.request.side_effect = response
        data = read_months(gateway, date(2026, 8, 1), date(2026, 11, 8))
        self.assertEqual(len(data), 4)
        self.assertEqual(data[date(2026, 8, 1)], 3601)
        self.assertNotIn(date(2026, 8, 2), data)
        self.assertEqual(gateway.request.call_count, 4)

    def test_invalid_or_non_daily_month_buckets_fail_before_writes(self):
        stamp = int(datetime(2026, 8, 1, tzinfo=TZ).timestamp())
        for response in (
            {}, {'readTimes': {}, 'totalReadTime': 1},
            {'readTimes': {str(stamp): True}, 'totalReadTime': 1},
            {'readTimes': {str(stamp): -1}, 'totalReadTime': -1},
            {'readTimes': {str(stamp + 1): 1}, 'totalReadTime': 1},
            {'readTimes': {str(stamp - 86400): 1}, 'totalReadTime': 1},
        ):
            with self.subTest(response=response), self.assertRaises(ValueError):
                read_months(self.gateway(response), date(2026, 8, 1), date(2026, 8, 2))

    def test_beijing_dates_and_iso_week_year_boundary(self):
        self.assertEqual(date_value({'date': {'start': '2026-08-01T00:00:00+08:00'}}), date(2026, 8, 1))
        self.assertEqual(date_value({'date': {'start': '2026-07-31T16:00:00Z'}}), date(2026, 8, 1))
        self.assertEqual(period(date(2027, 1, 1), '周'),
                         (date(2026, 12, 28), date(2027, 1, 4), '2026年第53周'))
        with self.assertRaises(ValueError):
            date_value({'date': {'start': '2026-08-01T00:00:00'}})

    def fixture(self):
        client = MagicMock()
        ids = {kind: 'source-' + kind for kind in ('日', '周', '月', '年')}
        rows = {kind: [] for kind in ids}
        schemas = {}
        for kind in ids:
            schema = {'标题': {'type': 'title'}, '日期': {'type': 'date'}}
            if kind == '日':
                schema.update({'时长': {'type': 'number'}, '时间戳': {'type': 'number'}})
                for target in ('周', '月', '年'):
                    schema[target] = {'type': 'relation', 'relation': {
                        'type': 'dual_property', 'data_source_id': ids[target]}}
            else:
                schema.update({'时长': {'type': 'rollup', 'rollup': {
                    'function': 'sum', 'relation_property_name': '每日阅读统计',
                    'rollup_property_name': '时长'}},
                    '每日阅读统计': {'type': 'relation', 'relation': {'data_source_id': ids['日']}}})
            schemas[kind] = schema

        def request(path, method, body=None, query=None):
            if path == 'blocks/root/children':
                return {'results': [{'id': 'db-' + k, 'type': 'child_database',
                                     'child_database': {'title': k}} for k in ids]}
            if path.startswith('databases/'):
                kind = path.split('db-')[1]
                return {'data_sources': [{'id': ids[kind]}]}
            if path.endswith('/query'):
                kind = path.split('source-')[1].split('/')[0]
                return {'results': copy.deepcopy(rows[kind])}
            kind = path.split('source-')[1]
            return {'properties': schemas[kind]}

        def create(parent, properties):
            kind = parent['data_source_id'].split('source-')[1]
            page = {'id': f'created-{kind}-{len(rows[kind])}', 'properties': copy.deepcopy(properties)}
            rows[kind].append(page)
            return {'id': page['id']}

        def update(page_id, properties):
            for page in rows['日']:
                if page['id'] == page_id:
                    page['properties'].update(copy.deepcopy(properties))
                    return {'id': page_id}
            raise AssertionError('Unknown page')
        client.request.side_effect = request
        client.pages.create.side_effect = create
        client.pages.update.side_effect = update
        return client, rows, schemas

    def test_existing_and_new_days_preserve_unrelated_data_and_repeat_is_zero_write(self):
        client, rows, _ = self.fixture()
        day = date(2026, 8, 1)
        properties = {'日期': prop_date(day), '时长': {'number': 5},
                      '书架': {'relation': [{'id': 'manual-book'}]},
                      '标题': {'title': [{'text': {'content': 'Keep manual title'}}]}}
        for kind in ('周', '月', '年'):
            first, last, title = period(day, kind)
            rows[kind].append({'id': 'existing-' + kind, 'properties': {'日期': prop_date(first, last)}})
            properties[kind] = {'relation': [{'id': 'existing-' + kind}]}
        rows['日'].append({'id': 'old-day', 'properties': properties})
        records = Records(client, 'root')
        durations = {day: 3600, date(2026, 8, 2): 7200}
        plan = records.plan(durations, day, date(2026, 8, 2))
        self.assertEqual(len(plan[0]), 0)
        self.assertEqual(len(plan[1]), 2)
        client.pages.update.assert_not_called()  # Planning is strictly read-only.
        client.pages.create.assert_not_called()
        records.apply(plan)
        client.pages.update.assert_called_once_with(page_id='old-day', properties={'时长': {'number': 3600}})
        client.pages.create.assert_called_once()
        self.assertEqual(properties['书架']['relation'], [{'id': 'manual-book'}])
        self.assertEqual(properties['标题']['title'][0]['text']['content'], 'Keep manual title')
        self.assertEqual(rows['日'][1]['properties']['周']['relation'], [{'id': 'existing-周'}])
        again = records.plan(durations, day, date(2026, 8, 2))
        self.assertEqual(again, ({}, []))
        client.blocks.delete.assert_not_called()
        client.blocks.update.assert_not_called()

    def test_missing_periods_created_once_and_empty_links_repaired(self):
        client, rows, _ = self.fixture()
        day = date(2026, 8, 1)
        rows['日'].append({'id': 'old-day', 'properties': {'日期': prop_date(day), '时长': {'number': 3600}}})
        records = Records(client, 'root')
        plan = records.plan({day: 3600, date(2026, 8, 2): 7200}, day, date(2026, 8, 2))
        self.assertEqual(len(plan[0]), 3)
        records.apply(plan)
        self.assertEqual(records.counts['periods_created'], 3)
        self.assertEqual(set(client.pages.update.call_args.kwargs['properties']), {'周', '月', '年'})
        self.assertEqual(records.plan({day: 3600, date(2026, 8, 2): 7200}, day, date(2026, 8, 2)), ({}, []))

    def test_duplicate_daily_dates_abort_before_any_writes(self):
        client, rows, _ = self.fixture()
        day = date(2026, 8, 1)
        rows['日'].extend([{'id': str(i), 'properties': {'日期': prop_date(day)}} for i in range(2)])
        records = Records(client, 'root')
        with self.assertRaises(ValueError):
            records.plan({day: 60}, day, day)
        client.pages.create.assert_not_called()
        client.pages.update.assert_not_called()

    def test_wrong_schema_and_conflicting_relations_are_not_overwritten(self):
        client, rows, schemas = self.fixture()
        schemas['月']['时长']['rollup']['function'] = 'count'
        with self.assertRaises(ValueError):
            Records(client, 'root')
        schemas['月']['时长']['rollup']['function'] = 'sum'
        day = date(2026, 8, 1)
        rows['日'].append({'id': 'old', 'properties': {
            '日期': prop_date(day), '周': {'relation': [{'id': 'unrelated'}]}}})
        records = Records(client, 'root')
        with self.assertRaises(ValueError):
            records.plan({day: 60}, day, day)
        client.pages.create.assert_not_called()
        client.pages.update.assert_not_called()

    def test_query_pagination_and_duplicate_periods(self):
        client, rows, _ = self.fixture()
        records = Records(client, 'root')
        client.request.side_effect = [
            {'results': [{'id': 'a'}], 'has_more': True, 'next_cursor': 'next'},
            {'results': [{'id': 'b'}], 'has_more': False}]
        self.assertEqual(len(records.all_items('test', 'POST', {})), 2)
        self.assertEqual(client.request.call_args.kwargs['body']['start_cursor'], 'next')
        client, rows, _ = self.fixture()
        day = date(2026, 8, 1)
        first, last, _ = period(day, '周')
        rows['周'] = [{'id': str(i), 'properties': {'日期': prop_date(first, last)}} for i in range(2)]
        records = Records(client, 'root')
        with self.assertRaises(ValueError):
            records.plan({day: 60}, day, day)
        client.pages.create.assert_not_called()

    def test_new_period_boundaries_follow_legacy_exclusive_end(self):
        self.assertEqual(period(date(2026, 8, 1), '周'),
                         (date(2026, 7, 27), date(2026, 8, 3), '2026年第31周'))
        self.assertEqual(period(date(2026, 12, 31), '月'),
                         (date(2026, 12, 1), date(2027, 1, 1), '2026年12月'))
        self.assertEqual(period(date(2028, 2, 29), '月'),
                         (date(2028, 2, 1), date(2028, 3, 1), '2028年2月'))
        self.assertEqual(period(date(2026, 12, 31), '年'),
                         (date(2026, 1, 1), date(2027, 1, 1), '2026'))

    def test_existing_inclusive_exclusive_and_mixed_period_links_are_preserved(self):
        for day in (date(2026, 8, 1), date(2027, 1, 1)):
            for inclusive in (set(), {'周', '月', '年'}, {'月'}):
                with self.subTest(day=day, inclusive=inclusive):
                    client, rows, _ = self.fixture()
                    props = {'日期': prop_date(day), '时长': {'number': 60}}
                    for kind in ('周', '月', '年'):
                        first, following, _ = period(day, kind)
                        end = following - timedelta(days=1) if kind in inclusive else following
                        rows[kind].append({'id': 'original-' + kind,
                                           'properties': {'日期': prop_date(first, end)}})
                        props[kind] = {'relation': [{'id': 'original-' + kind}]}
                    rows['日'].append({'id': 'original-day', 'properties': props})
                    before = copy.deepcopy(rows)
                    records = Records(client, 'root')
                    self.assertEqual(records.plan({day: 60}, day, day), ({}, []))
                    self.assertEqual(rows, before)
                    client.pages.update.assert_not_called()
                    client.pages.create.assert_not_called()
                    queries = [c.kwargs for c in client.request.call_args_list
                               if c.kwargs['path'].endswith('/query')]
                    week_query = next(q for q in queries if q['path'].endswith('source-周/query'))
                    self.assertEqual(week_query['body']['filter']['and'][0]['date']['on_or_after'],
                                     period(day, '周')[0].isoformat())

    def test_unrelated_march_and_july_duplicates_do_not_block_august(self):
        client, rows, _ = self.fixture()
        for month in (3, 7):
            first = date(2026, month, 1)
            end = date(2026, month + 1, 1)
            rows['月'].extend([{'id': f'old-{month}-{i}', 'properties': {'日期': prop_date(first, end)}}
                              for i in range(2)])
        before = copy.deepcopy(rows['月'])
        records = Records(client, 'root')
        plan = records.plan({date(2026, 8, 1): 60}, date(2026, 8, 1), date(2026, 8, 1))
        self.assertEqual(len(plan[0]), 3)
        self.assertEqual(len(plan[1]), 1)
        self.assertEqual(rows['月'], before)
        client.pages.create.assert_not_called()

    def test_target_period_duplicates_rejected_even_with_different_end_styles(self):
        for day in (date(2026, 8, 1), date(2027, 1, 1)):
            for kind in ('周', '月', '年'):
                with self.subTest(day=day, kind=kind):
                    client, rows, _ = self.fixture()
                    first, following, _ = period(day, kind)
                    rows[kind] = [
                        {'id': 'a', 'properties': {'日期': prop_date(first, following)}},
                        {'id': 'b', 'properties': {'日期': prop_date(first, following - timedelta(days=1))}},
                    ]
                    records = Records(client, 'root')
                    with self.assertRaisesRegex(ValueError, 'Duplicate period'):
                        records.plan({day: 60}, day, day)
                    client.pages.create.assert_not_called()
                    client.pages.update.assert_not_called()

    def test_unknown_day_duplicates_are_ignored_but_involved_day_duplicates_fail(self):
        client, rows, _ = self.fixture()
        unknown = date(2026, 11, 8)
        rows['日'] = [{'id': str(i), 'properties': {'日期': prop_date(unknown)}} for i in range(2)]
        records = Records(client, 'root')
        plan = records.plan({date(2026, 11, 7): 60}, date(2026, 11, 1), unknown)
        self.assertEqual(len(plan[1]), 1)
        self.assertEqual(plan[1][0][1]['日期']['date']['start'], '2026-11-07')
        with self.assertRaisesRegex(ValueError, 'Duplicate Notion date'):
            records.plan({unknown: 60}, date(2026, 11, 1), unknown)
        self.assertEqual(records.plan({}, date(2026, 11, 1), unknown), ({}, []))
        client.pages.create.assert_not_called()
        client.pages.update.assert_not_called()

    def test_new_period_pages_use_exclusive_dates_without_editing_existing_periods(self):
        client, rows, _ = self.fixture()
        day = date(2027, 1, 1)
        records = Records(client, 'root')
        records.apply(records.plan({day: 60}, day, day))
        expected = {'周': ('2026-12-28', '2027-01-04'),
                    '月': ('2027-01-01', '2027-02-01'),
                    '年': ('2027-01-01', '2028-01-01')}
        for kind, (first, end) in expected.items():
            self.assertEqual(rows[kind][0]['properties']['日期']['date'], {'start': first, 'end': end})
        client.pages.update.assert_not_called()

    def test_wrong_target_period_boundary_is_not_silently_duplicated(self):
        client, rows, _ = self.fixture()
        day = date(2026, 8, 1)
        rows['月'] = [{'id': 'wrong', 'properties': {'日期': prop_date(day, date(2026, 8, 15))}}]
        records = Records(client, 'root')
        with self.assertRaisesRegex(ValueError, 'incompatible date boundaries'):
            records.plan({day: 60}, day, day)
        client.pages.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
