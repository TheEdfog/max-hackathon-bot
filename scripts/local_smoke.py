"""Isolated real HTTP, restart and backup recovery. Stop only our own processes."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def stop(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
    else:
        process.terminate()
        process.wait(timeout=20)


def main():
    # Use a private ephemeral port and directory; never the running bot's database.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    directory = Path(tempfile.mkdtemp(prefix='rezumit-http-smoke-'))
    env = {**os.environ, 'HIRING_DATABASE_URL': 'sqlite:///' + (directory / 'test.db').as_posix(),
           'HIRING_ENV': 'development', 'HIRING_DEMO': 'false', 'HIRING_WORKER': 'false',
           'HIRING_SECRET': 'synthetic-smoke-secret-not-production',
           'HIRING_EMPLOYER_CODE': 'synthetic-ci-code', 'MAX_BOT_TOKEN': '', 'PYTHONIOENCODING': 'utf-8'}
    base = f'http://127.0.0.1:{port}'
    for phase in ('create', 'restart', 'restore'):
        if phase == 'restore':
            env['HIRING_DATABASE_URL'] = 'sqlite:///' + (directory / 'recovered.db').as_posix()
        with (directory / 'server.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'hiring.main:app', '--host', '127.0.0.1',
                                        '--port', str(port), '--no-access-log'], cwd=ROOT, env=env,
                                       stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/smoke_hiring.py'), '--base-url', base, '--wait']
                                        + (['--verify-existing'] if phase != 'create' else []), cwd=ROOT, env=env, timeout=60)
                if result.returncode:
                    raise SystemExit('Smoke failed; private diagnostics: ' + str(directory))
            finally:
                stop(process)
        if phase == 'create':
            from hiring.maintenance import backup
            backup(env['HIRING_DATABASE_URL'], directory / 'recovered.db')
    print('PASS: real HTTP lifecycle, restart and recovery from a verified SQLite backup. Synthetic diagnostics:', directory)


if __name__ == '__main__':
    main()
