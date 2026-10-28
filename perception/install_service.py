"""Register the perception HTTP service for the current user.

Run in omniparser-env. The only copied secret is the perception token.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERCEPTION = Path(__file__).resolve().parent


def main():
    text = (ROOT / 'local/.env').read_text(encoding='utf-8')
    values = {line.partition('=')[0].strip(): line.partition('=')[2].strip()
              for line in text.splitlines() if '=' in line and not line.lstrip().startswith('#')}
    tokens = [values['GUI_PERCEPTION_TOKEN']] if 'GUI_PERCEPTION_TOKEN' in values else []
    if len(tokens) != 1 or not re.fullmatch(r'[0-9a-fA-F]{64}', tokens[0]):
        raise SystemExit('local/.env must contain one 64-character hex GUI_PERCEPTION_TOKEN')
    if not (PERCEPTION / 'run_ocr.py').is_file():
        raise SystemExit('migrate the perception runtime into this directory first')
    env_file = PERCEPTION / '.service.env'
    service_values = {'GUI_PERCEPTION_TOKEN': tokens[0],
                      'GUI_PERCEPTION_QWEN': values.get('GUI_PERCEPTION_QWEN', '1')}
    for name in ('GUI_CONTROL_MODEL', 'OLLAMA_URL'):
        if values.get(name):
            service_values[name] = values[name]
    env_file.write_text(''.join(f'{name}={value}\n' for name, value in service_values.items()), encoding='utf-8')
    env_file.chmod(0o600)
    unit_dir = Path.home() / '.config/systemd/user'
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / 'sgui-perception.service'
    unit.write_text(f'''[Unit]
Description=SGUI screenshot perception
After=network-online.target

[Service]
Type=simple
WorkingDirectory={PERCEPTION}
EnvironmentFile={env_file}
Environment=HF_HUB_OFFLINE=1
ExecStart={sys.executable} {PERCEPTION / 'service.py'} --host 0.0.0.0 --port 18781
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
''', encoding='utf-8')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print('Registered:', unit)
    print('Python:', sys.executable)
    print('If port 18781 is in use, stop the old foreground perception_server.py process first.')
    print('Start with: systemctl --user enable --now sgui-perception.service')
    print('Check with: systemctl --user status sgui-perception.service --no-pager')


if __name__ == '__main__':
    main()
