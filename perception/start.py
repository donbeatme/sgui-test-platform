"""Start perception with the platform's local/.env, without copying its secrets."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / 'local/.env')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=18781)
    args = parser.parse_args()
    allowed = {'GUI_PERCEPTION_TOKEN', 'GUI_PERCEPTION_QWEN', 'GUI_CONTROL_TYPING',
               'GUI_CONTROL_MODEL', 'GUI_ICON_MODEL', 'OLLAMA_URL'}
    for line in args.env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if separator and key in allowed:
            os.environ[key] = value.strip().strip('\"\'')
    from perception.service import main as serve
    sys.argv = ['service.py', '--host', args.host, '--port', str(args.port)]
    serve()


if __name__ == '__main__':
    main()
