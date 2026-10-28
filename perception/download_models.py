"""Download the declared model, or verify existing local weights (stdlib only)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent


def verify(path, spec):
    if not path.is_file() or path.stat().st_size != spec['size']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == spec['sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify without downloading')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'models.json').read_text())
    for spec in manifest['files']:
        destination = ROOT / spec['path']
        if verify(destination, spec):
            print('Verified:', spec['path'])
            continue
        if args.check:
            raise SystemExit('Missing or mismatched model: ' + spec['path'])
        # Never replace an existing unknown model implicitly.
        if destination.exists():
            raise SystemExit('Existing model has a different checksum; move it aside first: ' + str(destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='.model-', dir=destination.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(handle, 'wb') as output, urlopen(spec['url'], timeout=120) as response:
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    output.write(block)
            if not verify(temporary, spec):
                raise SystemExit('Downloaded model differs from models.json; no model was installed')
            # Exclusive creation avoids overwriting a file created during download.
            os.link(temporary, destination)
            print('Downloaded and verified:', spec['path'])
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
