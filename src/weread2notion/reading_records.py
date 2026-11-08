"""Independent daily-duration sync. Preview by default; never imports old Pro helpers."""
import argparse
import calendar
import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from notion_client import Client
from .cli import NOTION_VERSION, WeReadGatewayClient, NOTION_ID_IN_TEXT_PATTERN

TZ = ZoneInfo('Asia/Shanghai')
KINDS = ('日', '周', '月', '年')


def read_months(gateway, start, today):
    """Validate the complete response before planning any Notion write."""
    result = {}
    month = start.replace(day=1)
    while month <= today:
        response = gateway.request('/readdata/detail', mode='monthly',
                                   baseTime=int(datetime.combine(month, time(), TZ).timestamp()))
        buckets = response.get('readTimes')
        total = response.get('totalReadTime')
        if not isinstance(buckets, dict) or type(total) is not int or total < 0:
            raise ValueError('Missing monthly buckets or valid totalReadTime')
        subtotal = 0
        for raw, seconds in buckets.items():
            if type(seconds) is not int or seconds < 0:
                raise ValueError('Duration must be nonnegative integer seconds')
            stamp = int(raw)
            instant = datetime.fromtimestamp(stamp, TZ)
            day = instant.date()
            if instant.time() != time() or day.replace(day=1) != month:
                raise ValueError('Monthly bucket is not a Beijing calendar day')
            subtotal += seconds
            if start <= day <= today:
                if day in result:
                    raise ValueError('Duplicate API day bucket')
                result[day] = seconds
        if subtotal != total:
            raise ValueError('Monthly buckets do not reconcile with totalReadTime')
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result  # Missing dates are unknown, never implicitly zero.


def date_value(prop):
    value = (prop or {}).get('date') or {}
    if not value.get('start') or value.get('end'):
        raise ValueError('Daily record needs a single date')
    raw = value['start']
    if 'T' not in raw:
        return date.fromisoformat(raw)
    parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        if not value.get('time_zone'):
            raise ValueError('Ambiguous datetime without timezone')
        parsed = parsed.replace(tzinfo=ZoneInfo(value['time_zone']))
    return parsed.astimezone(TZ).date()


def period(day, kind):
    if kind == '周':
        first = day - timedelta(days=day.weekday())
        return first, first + timedelta(days=6), f'{day.isocalendar().year}年第{day.isocalendar().week}周'
    if kind == '月':
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1]), f'{day.year}年{day.month}月'
    return date(day.year, 1, 1), date(day.year, 12, 31), str(day.year)


class Records:
    def __init__(self, client, root):
        self.client = client
        self.sources = {}
        self.schemas = {}
        self.databases = {}
        self.counts = {'requests': 0, 'periods_created': 0, 'days_created': 0, 'days_updated': 0}
        self.discover(root)

    def request(self, path, method='GET', body=None):
        self.counts['requests'] += 1
        kwargs = {'path': path, 'method': method}
        if body is not None:
            kwargs['body'] = body
        return self.client.request(**kwargs)

    def all_items(self, path, method='GET', body=None):
        body = dict(body or {})
        items = []
        cursors = set()
        while True:
            if method == 'GET':
                # GET pagination uses query parameters, not a request body.
                self.counts['requests'] += 1
                response = self.client.request(path=path, method='GET', query=body)
            else:
                response = self.request(path, method, body)
            items.extend(response.get('results') or [])
            if not response.get('has_more'):
                return items
            cursor = response.get('next_cursor')
            if not cursor or cursor in cursors:
                raise ValueError('Invalid pagination cursor')
            cursors.add(cursor)
            body['start_cursor'] = cursor

    def discover(self, root):
        seen = set()

        def visit(block_id, depth=0):
            if block_id in seen or depth > 8:
                raise ValueError('Ambiguous or excessively nested template')
            seen.add(block_id)
            for block in self.all_items(f'blocks/{block_id}/children'):
                kind = block['type']
                if kind == 'child_database':
                    title = block[kind]['title']
                    if title in KINDS:
                        if title in self.databases:
                            raise ValueError('Duplicate target database: ' + title)
                        self.databases[title] = block['id']
                elif block.get('has_children') and kind in {'column_list', 'column', 'toggle', 'callout', 'synced_block'}:
                    visit(block['id'], depth + 1)
        visit(root)
        if set(self.databases) != set(KINDS):
            raise ValueError('Root must contain the existing 日/周/月/年 databases; set READING_RECORDS_PAGE to the root page')
        for kind, database in self.databases.items():
            sources = self.request(f'databases/{database}').get('data_sources') or []
            if len(sources) != 1:
                raise ValueError('Expected exactly one data source: ' + kind)
            source = sources[0]['id']
            self.sources[kind] = source
            self.schemas[kind] = self.request(f'data_sources/{source}')['properties']
        for kind, schema in self.schemas.items():
            for name, expected in {'标题': 'title', '日期': 'date', '时长': 'number' if kind == '日' else 'rollup'}.items():
                if schema.get(name, {}).get('type') != expected:
                    raise ValueError(f'Wrong field type: {kind}.{name}')
        day = self.schemas['日']
        if day.get('时间戳', {}).get('type') != 'number':
            raise ValueError('日.时间戳 must be a number')
        for kind in KINDS[1:]:
            relation = day.get(kind, {})
            config = relation.get('relation') or {}
            if relation.get('type') != 'relation' or config.get('type') != 'dual_property':
                raise ValueError('Daily period links must retain reciprocal relations: ' + kind)
            if config.get('data_source_id', config.get('database_id')) not in {self.sources[kind], self.databases[kind]}:
                raise ValueError('Period relation points at another template')
            rollup = self.schemas[kind]['时长']['rollup']
            reverse = self.schemas[kind].get(rollup.get('relation_property_name'), {})
            if (rollup.get('function') != 'sum' or rollup.get('rollup_property_name') != '时长'
                    or reverse.get('type') != 'relation'
                    or (reverse.get('relation') or {}).get('data_source_id', (reverse.get('relation') or {}).get('database_id'))
                    not in {self.sources['日'], self.databases['日']}):
                raise ValueError('Period rollup does not sum the existing daily duration')

    def rows(self, kind, start, today):
        return self.all_items(f'data_sources/{self.sources[kind]}/query', 'POST', {
            'filter': {'and': [
                {'property': '日期', 'date': {'on_or_after': start.isoformat()}},
                {'property': '日期', 'date': {'on_or_before': today.isoformat()}}]}, 'page_size': 100})

    def plan(self, durations, start, today):
        days = {}
        for row in self.rows('日', start, today):
            day = date_value(row['properties'].get('日期'))
            if day in days:
                raise ValueError('Duplicate Notion date; no writes performed: ' + day.isoformat())
            days[day] = row
        periods = {}
        for kind in KINDS[1:]:
            index = {}
            # Includes year starts and the ISO week crossing the year boundary.
            for row in self.rows(kind, date(start.year, 1, 1) - timedelta(days=7), today):
                value = row['properties'].get('日期', {}).get('date') or {}
                first = date.fromisoformat(value['start'][:10])
                last = date.fromisoformat(value.get('end') or value['start'][:10])
                key = (first, last)
                if key in index:
                    raise ValueError('Duplicate period date range: ' + kind)
                index[key] = row['id']
            periods[kind] = index
        creates = {}
        changes = []
        for day, seconds in sorted(durations.items()):
            row = days.get(day)
            properties = {}
            links = {}
            for kind in KINDS[1:]:
                first, last, title = period(day, kind)
                target = periods[kind].get((first, last))
                key = (kind, first, last)
                current = ((row or {}).get('properties', {}).get(kind, {}).get('relation') or [])
                if row and row['properties'].get(kind, {}).get('has_more'):
                    raise ValueError('Truncated relation; refusing to replace it')
                if current and (len(current) != 1 or target != current[0]['id']):
                    raise ValueError('Existing period relation conflicts; preserve and review')
                if not current:
                    if not target:
                        creates[key] = title
                    links[kind] = target or key
            if not row or row['properties'].get('时长', {}).get('number') != seconds:
                properties['时长'] = {'number': seconds}
            if not row:
                properties.update({'标题': {'title': [{'text': {'content': day.strftime('%Y年%m月%d日')}}]},
                                   '日期': {'date': {'start': day.isoformat()}},
                                   '时间戳': {'number': int(datetime.combine(day, time(), TZ).timestamp())}})
            if properties or links:
                changes.append(((row or {}).get('id'), properties, links))
        return creates, changes

    def apply(self, plan):
        creates, changes = plan
        resolved = {}
        for key, title in creates.items():
            kind, first, last = key
            page = self.client.pages.create(parent={'data_source_id': self.sources[kind]}, properties={
                '标题': {'title': [{'text': {'content': title}}]},
                '日期': {'date': {'start': first.isoformat(), 'end': last.isoformat()}}})
            resolved[key] = page['id']
            self.counts['periods_created'] += 1
        for page_id, properties, links in changes:
            properties = dict(properties)
            for kind, target in links.items():
                properties[kind] = {'relation': [{'id': resolved[target] if isinstance(target, tuple) else target}]}
            if page_id:
                self.client.pages.update(page_id=page_id, properties=properties)
                self.counts['days_updated'] += 1
            else:
                self.client.pages.create(parent={'data_source_id': self.sources['日']}, properties=properties)
                self.counts['days_created'] += 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', type=date.fromisoformat, default=date(2026, 8, 1))
    parser.add_argument('--apply', action='store_true', help='Explicitly allow production writes')
    args = parser.parse_args()
    today = datetime.now(TZ).date()
    if args.since > today:
        parser.error('--since must not be in the future')
    root = os.getenv('READING_RECORDS_PAGE') or os.getenv('NOTION_PAGE') or ''
    match = NOTION_ID_IN_TEXT_PATTERN.search(root)
    if not match:
        parser.error('Set READING_RECORDS_PAGE (root page) or existing NOTION_PAGE')
    client = Client(auth=os.environ['NOTION_TOKEN'], notion_version=NOTION_VERSION)
    records = Records(client, match.group(0))
    data = read_months(WeReadGatewayClient(os.environ['WEREAD_API_KEY']), args.since, today)
    plan = records.plan(data, args.since, today)
    print(json.dumps({'preview': not args.apply, 'api_days': len(data),
                      'periods_to_create': len(plan[0]), 'days_to_write': len(plan[1]),
                      'notion_read_requests': records.counts['requests']}, ensure_ascii=False))
    if args.apply:
        records.apply(plan)
        print(json.dumps(records.counts))


if __name__ == '__main__':
    main()
