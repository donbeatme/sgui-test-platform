"""Start the optional MeterSphere adapter only with usable credentials."""
import os
import sys
from urllib.parse import urlparse


def configuration_ready(env):
    host = urlparse(env.get('MS_API_HOST', ''))
    access = env.get('MS_ACCESS_KEY', '')
    secret = env.get('MS_SECRET_KEY', '')
    return (
        host.scheme in ('http', 'https')
        and bool(host.hostname)
        and host.hostname != 'ms.example.com'
        and len(access.encode('utf-8')) == 16
        and len(secret.encode('utf-8')) in (16, 24, 32)
        and not access.startswith('your_')
        and not secret.startswith('your_')
    )


def main():
    if not configuration_ready(os.environ):
        print('MeterSphere adapter inactive: configure MS_API_HOST, MS_ACCESS_KEY '
              'and MS_SECRET_KEY in local/.env, then recreate the mcp service.', flush=True)
        return 0
    os.execv(sys.executable, [sys.executable, '/app/ms_mcp_api.py'])


if __name__ == '__main__':
    raise SystemExit(main())
