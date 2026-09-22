"""Open one mission case with both side-specific BML files explicitly selected."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    manifest = json.loads((HERE/'MISSION_MANIFEST.json').read_text(encoding='utf-8'))
    cases = {c['key']: c for c in manifest['cases']}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=str.upper, choices=sorted(cases))
    args = parser.parse_args()
    case = cases[args.case]
    # Running inside MISSION also keeps any user-requested L-key replay export here.
    return subprocess.run([
        sys.executable, '-B', str(ROOT/'main.py'), str(HERE/case['scenario']),
        '--blue-bml', str(HERE/case['blue_bml']),
        '--red-bml', str(HERE/case['red_bml']),
    ], cwd=HERE).returncode


if __name__ == '__main__':
    raise SystemExit(main())
