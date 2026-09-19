#!/bin/bash
set -e
# PDF dependencies persist in the D-drive data bind mount across recreation.
python -c "import site,pathlib; pathlib.Path(site.getsitepackages()[0], 'sgui_structure.pth').write_text('/app/data/structure-deps\\n')"
if ! python -c 'import pdfplumber; assert pdfplumber.__version__ == "0.11.9"' >/dev/null 2>&1; then
    /usr/local/bin/python -m pip install --target=/app/data/structure-deps -r /app/guicase_workbench/requirements-structure.txt
fi
# Validate once in a short-lived process; Celery need not retain the web stack.
python manage.py check
python manage.py migrate --noinput
python manage.py shell -c "exec(open('/app/workbench_bootstrap.py').read())"
exec supervisord -c /app/supervisord.conf
