"""Compare publishable files against local secret values, without printing those values."""
from pathlib import Path
import subprocess
import sys
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main():
    values = dotenv_values(ROOT / '.env.hiring')
    secrets = [value.encode() for key, value in values.items() if value and len(value) >= 8
               and any(part in key for part in ('TOKEN', 'SECRET', 'PASSWORD', 'EMPLOYER_CODE'))]
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    findings = []
    for name in set(names) - {''}:
        path = ROOT / name
        if path.is_file() and any(value in path.read_bytes() for value in secrets):
            findings.append(name)
    if findings:
        print('Blocked: local secret value found in publishable file(s):', ', '.join(sorted(findings)))
        return 1
    print(f'PASS: {len(secrets)} local secret values absent from publishable files. Not a complete secret-history audit.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
