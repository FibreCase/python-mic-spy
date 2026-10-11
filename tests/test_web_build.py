import subprocess
import pytest
from mic_spy.web_build import build_web


def test_installs_on_lock_change_builds_each_time(tmp_path, monkeypatch):
    source = tmp_path/'web'
    source.mkdir()
    (source/'package.json').write_text('{}')
    lock = source/'package-lock.json'
    lock.write_text('{}')
    output = tmp_path/'dist'
    calls = []
    monkeypatch.setattr('mic_spy.web_build.shutil.which', lambda _: '/bin/npm')
    def run(command, **kwargs):
        calls.append(command)
        if 'ci' in command:
            binaries = source/'node_modules'/'.bin'
            binaries.mkdir(parents=True, exist_ok=True)
            (binaries/'vite').touch()
        else:
            output.mkdir(exist_ok=True)
            (output/'index.html').write_text('built')
    monkeypatch.setattr('mic_spy.web_build.subprocess.run',run)
    build_web(source, output)
    build_web(source, output)
    assert [c[1] for c in calls] == ['ci','run','run']
    lock.write_text('{"changed":true}')
    build_web(source, output)
    assert [c[1] for c in calls][-2:] == ['ci','run']


def test_build_failure_stops_startup(tmp_path, monkeypatch):
    (tmp_path/'package.json').write_text('{}')
    (tmp_path/'package-lock.json').write_text('{}')
    monkeypatch.setattr('mic_spy.web_build.shutil.which',lambda _: '/bin/npm')
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr('mic_spy.web_build.subprocess.run',fail)
    with pytest.raises(RuntimeError, match='service not started'):
        build_web(tmp_path,tmp_path/'dist')
