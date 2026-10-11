"""Prepare the Node frontend before starting HTTP services."""
import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger('mic-spy')


def build_web(source: Path, output: Path):
    source, output = source.resolve(), output.resolve()
    manifest, lockfile = source / 'package.json', source / 'package-lock.json'
    if not manifest.is_file() or not lockfile.is_file():
        raise RuntimeError(f'Frontend sources missing at {source}; use --web-source or --no-build for a prebuilt frontend')
    npm = shutil.which('npm')
    if npm is None:
        raise RuntimeError('Automatic web build requires Node.js and npm; install them or use --no-build')
    digest = hashlib.sha256(manifest.read_bytes() + lockfile.read_bytes()).hexdigest()
    marker = source / 'node_modules' / '.mic-spy-dependencies'
    try:
        if not marker.is_file() or marker.read_text() != digest or not (source / 'node_modules' / '.bin' / 'vite').exists():
            log.info('Installing frontend dependencies')
            subprocess.run([npm, 'ci', '--include=dev'], cwd=source, check=True)
            marker.write_text(digest)
        log.info('Building frontend')
        subprocess.run([npm, 'run', 'build', '--', '--outDir', str(output)], cwd=source, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f'Frontend installation/build failed (exit {exc.returncode}); service not started') from exc
    if not (output / 'index.html').is_file():
        raise RuntimeError('Frontend build did not produce index.html')
