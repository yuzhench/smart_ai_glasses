import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Fresh interpreter subprocesses (e.g. the native-runtime independence probe)
# auto-apply the adaptor through m3_adaptors/subprocess_hook/sitecustomize.py.
_parts = [str(ROOT / 'm3_adaptors' / 'subprocess_hook'), str(ROOT)]
if os.environ.get('PYTHONPATH'):
    _parts.append(os.environ['PYTHONPATH'])
os.environ['PYTHONPATH'] = os.pathsep.join(_parts)

import m3_adaptors

m3_adaptors.apply()
