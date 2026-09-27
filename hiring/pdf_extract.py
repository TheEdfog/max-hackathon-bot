"""Bounded PDF extraction in a disposable process, with no access to bot credentials."""
import io
import os
from pathlib import Path
import subprocess
import sys
import threading

_slot = threading.BoundedSemaphore(1)
MAX_BYTES = 5 * 1024 * 1024


def extract_pdf(raw):
    if len(raw) > MAX_BYTES or not raw.startswith(b'%PDF'):
        raise ValueError('Unsupported PDF')
    if not _slot.acquire(blocking=False):
        raise ValueError('PDF reader busy; paste text or retry')
    try:
        # Do not inherit MAX/LLM/JWT credentials or user-controlled PYTHONPATH.
        env = {k: v for k, v in os.environ.items() if k.upper() in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
        result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--worker'],
                                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, timeout=8, check=False,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        text = result.stdout.decode('utf-8', errors='strict')
        if result.returncode or not 40 <= len(text.strip()) <= 20000:
            raise ValueError('Unreadable PDF')
        return text
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise ValueError('PDF reader failed') from exc
    finally:
        _slot.release()


def worker():
    if sys.platform == 'linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    from pypdf import PdfReader
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError()
    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted or len(reader.pages) > 10:
        raise ValueError()
    chunks, remaining = [], 20000
    for page in reader.pages:
        text = (page.extract_text() or '')[:remaining]
        chunks.append(text)
        remaining -= len(text) + 1
        if remaining <= 0:
            break
    result = '\n'.join(chunks)[:20000]
    if len(result.strip()) < 40:
        raise ValueError()
    sys.stdout.buffer.write(result.encode('utf-8'))


if __name__ == '__main__':
    try:
        worker()
    except Exception:
        sys.exit(1)  # No parser diagnostics or document contents in server logs.
