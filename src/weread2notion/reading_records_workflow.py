"""Fail-closed workflow mode selection; no shell interpolation of user inputs."""
import json
import os
from datetime import datetime
from .reading_records import TZ, main as sync_main, parse_args


def workflow_args(env, today=None):
    today = today or datetime.now(TZ).date()
    event = env.get('GITHUB_EVENT_NAME', '')
    if event == 'schedule':
        args = ['--since', '2026-08-01', '--until', today.isoformat()]
        if (env.get('READING_RECORDS_FIRST_WRITE_ACCEPTED') == 'true'
                and env.get('READING_RECORDS_AUTO_WRITE_ENABLED') == 'true'):
            args += ['--apply', '--max-writes', env.get('READING_RECORDS_AUTO_MAX_WRITES') or '100']
    elif event == 'workflow_dispatch':
        mode = env.get('RECORDS_MODE') or 'preview'
        if mode not in ('preview', 'trial'):
            raise ValueError('Unknown workflow mode')
        since, until = env.get('RECORDS_SINCE'), env.get('RECORDS_UNTIL')
        args = []
        if since:
            args += ['--since', since]
        if until:
            args += ['--until', until]
        if mode == 'trial':
            if not since or not until or not env.get('RECORDS_MAX_WRITES'):
                raise ValueError('Trial requires explicit since, until and max_writes')
            args += ['--apply', '--max-writes', env['RECORDS_MAX_WRITES']]
    else:
        raise ValueError('Only schedule and workflow_dispatch are supported')
    parse_args(args, today=today)  # Validate everything before starting any API call.
    return args


def main():
    args = workflow_args(os.environ)
    print(json.dumps({'mode': 'bounded-write' if '--apply' in args else 'read-only-preview', 'args': args}))
    sync_main(args)


if __name__ == '__main__':
    main()
