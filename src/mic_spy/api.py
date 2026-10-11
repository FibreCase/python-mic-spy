"""Read-only HTTP API and optional built React UI; no recording worker needed."""
import io
import sqlite3
import wave
from datetime import datetime, timezone, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from .query import connect, export_clip


def timestamp(value):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(value)//1000)).isoformat()


def datetime_ns(value):
    delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


def serialize(row):
    result = dict(row)
    result['start_time'] = timestamp(result['start_ns'])
    result['end_time'] = timestamp(result['end_ns'])
    result['duration_seconds'] = (result['end_ns'] - result['start_ns']) / 1e9
    # Preserve integers larger than JavaScript's exact integer range.
    result['start_ns'] = str(result['start_ns'])
    result['end_ns'] = str(result['end_ns'])
    result['audio_url'] = f'/api/transcripts/{result["id"]}/audio'
    return result


def create_app(data: Path, web_dist: Path | None = None):
    data = data.resolve()
    app = FastAPI(title='Mic Spy API', version='0.1.0')

    def database():
        if not (data / 'archive.sqlite3').is_file():
            raise HTTPException(503, '尚无录音数据库，请先启动处理者或本机采集')
        db = connect(data)
        db.row_factory = sqlite3.Row
        return db

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'archive_available': (data / 'archive.sqlite3').is_file()}

    @app.get('/api/stats')
    def stats():
        db = database()
        try:
            row = db.execute('''SELECT COUNT(*) AS transcripts,
                MIN(start_ns) AS oldest_ns, MAX(end_ns) AS newest_ns FROM transcripts''').fetchone()
            audio = db.execute('''SELECT COUNT(*) AS segments,
                COALESCE(SUM((r.end_sample-r.start_sample)*1.0/s.sample_rate),0) AS seconds
                FROM recordings r JOIN sessions s ON s.id=r.session_id''').fetchone()
            return {'transcripts': row['transcripts'], 'segments': audio['segments'],
                    'audio_seconds': audio['seconds'],
                    'oldest_time': timestamp(row['oldest_ns']) if row['oldest_ns'] else None,
                    'newest_time': timestamp(row['newest_ns']) if row['newest_ns'] else None}
        finally:
            db.close()

    @app.get('/api/transcripts')
    def transcripts(text: str = Query('', max_length=500),
                    start: datetime | None = None, end: datetime | None = None,
                    limit: int = Query(30, ge=1, le=100),
                    offset: int = Query(0, ge=0), session_id: str | None = None):
        clauses, params = ['instr(text,?)>0'], [text]
        for moment in (start, end):
            if moment is not None and moment.tzinfo is None:
                raise HTTPException(422, '查询时间必须包含时区')
        if start and end and start >= end:
            raise HTTPException(422, '开始时间必须早于结束时间')
        if start:
            clauses.append('end_ns>?')
            params.append(datetime_ns(start))
        if end:
            clauses.append('start_ns<?')
            params.append(datetime_ns(end))
        if session_id:
            clauses.append('session_id=?')
            params.append(session_id)
        where = ' AND '.join(clauses)
        db = database()
        try:
            db.execute('BEGIN')  # Count and page share the same read snapshot.
            total = db.execute('SELECT COUNT(*) FROM transcripts WHERE '+where,params).fetchone()[0]
            rows = db.execute('SELECT * FROM transcripts WHERE '+where+
                ' ORDER BY start_ns DESC,id DESC LIMIT ? OFFSET ?', params+[limit,offset]).fetchall()
            return {'items': [serialize(row) for row in rows], 'total': total,
                    'limit': limit, 'offset': offset}
        finally:
            db.close()

    @app.get('/api/recordings')
    def recordings(start: datetime | None = None, end: datetime | None = None,
                   at: datetime | None = None, limit: int = Query(30, ge=1, le=100),
                   offset: int = Query(0, ge=0)):
        for moment in (start, end, at):
            if moment is not None and moment.tzinfo is None:
                raise HTTPException(422, '查询时间必须包含时区')
        if start and end and start >= end:
            raise HTTPException(422, '开始时间必须早于结束时间')
        # Calculate times from samples; silent audio has the same timeline as speech.
        base = """SELECT r.*,s.sample_rate,
            (s.started_ns / 1000 + (r.start_sample * 1000000000 / s.sample_rate) / 1000) * 1000 AS start_ns,
            (s.started_ns / 1000 + (r.end_sample * 1000000000 / s.sample_rate) / 1000) * 1000 AS end_ns
            FROM recordings r JOIN sessions s ON s.id=r.session_id
            WHERE r.end_sample > r.start_sample"""
        clauses, params = [], []
        if start:
            clauses.append('end_ns>?')
            params.append(datetime_ns(start))
        if end:
            clauses.append('start_ns<?')
            params.append(datetime_ns(end))
        if at:
            clauses.extend(['start_ns<=?', 'end_ns>?'])
            params.extend([datetime_ns(at)]*2)
        where = ' WHERE '+ ' AND '.join(clauses) if clauses else ''
        db = database()
        try:
            db.execute('BEGIN')
            total = db.execute('SELECT COUNT(*) FROM ('+base+')'+where,params).fetchone()[0]
            rows = db.execute('SELECT * FROM ('+base+')'+where+
                ' ORDER BY start_ns DESC,id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
            items = []
            for row in rows:
                item = serialize(row)
                item.pop('path')
                item['audio_url'] = f'/api/recordings/{row["id"]}/audio'
                item['closed'] = bool(row['closed'])
                item['seek_seconds'] = (datetime_ns(at)-row['start_ns'])/1e9 if at else 0
                items.append(item)
            return {'items':items, 'total':total, 'limit':limit, 'offset':offset}
        finally:
            db.close()

    @app.get('/api/recordings/{recording_id}/audio')
    def recording_audio(recording_id: int):
        db = database()
        try:
            row = db.execute('SELECT path FROM recordings WHERE id=?',(recording_id,)).fetchone()
        finally:
            db.close()
        if row is None:
            raise HTTPException(404, '录音已删除或不存在')
        path = (data / row['path']).resolve()
        if not path.is_relative_to(data / 'audio'):
            raise HTTPException(410, '录音路径无效')
        if not path.is_file():
            raise HTTPException(410, '录音文件已清理')
        return FileResponse(path, media_type='audio/wav',
                            headers={'Cache-Control':'no-store'},
                            filename=f'recording-{recording_id}.wav',content_disposition_type='inline')

    @app.get('/api/transcripts/{transcript_id}/audio')
    def audio(transcript_id: int, request: Request):
        db = database()
        try:
            row = db.execute('SELECT start_ns,end_ns FROM transcripts WHERE id=?',(transcript_id,)).fetchone()
        finally:
            db.close()
        if row is None:
            raise HTTPException(404, '文字记录已删除或不存在')
        if row['end_ns']-row['start_ns'] > 600*10**9:
            raise HTTPException(413, '回放句段过长，请使用命令行导出')
        buffer = io.BytesIO()
        try:
            export_clip(data, transcript_id, buffer)
        except (ValueError, FileNotFoundError, wave.Error, EOFError):
            raise HTTPException(410, '对应录音已清理或不完整')
        content = buffer.getvalue()
        size = len(content)
        headers = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store',
                   'Content-Disposition': f'inline; filename="transcript-{transcript_id}.wav"'}
        # Native audio controls use byte ranges for seeking; generate clips in memory.
        requested = request.headers.get('range')
        if requested:
            try:
                unit, value = requested.split('=', 1)
                a,b = value.split('-',1)
                if unit != 'bytes' or ',' in value:
                    raise ValueError()
                if a:
                    lo,hi = int(a),min(int(b) if b else size-1,size-1)
                else:
                    suffix = int(b)
                    if suffix <= 0:
                        raise ValueError()
                    lo,hi = max(0,size-suffix),size-1
                if lo < 0 or lo >= size or hi < lo:
                    raise ValueError()
            except ValueError:
                return Response(status_code=416, headers={'Content-Range':f'bytes */{size}'})
            headers['Content-Range'] = f'bytes {lo}-{hi}/{size}'
            return Response(content[lo:hi+1],status_code=206,media_type='audio/wav',headers=headers)
        return Response(content,media_type='audio/wav',headers=headers)

    # Unknown API routes stay JSON 404s, even when static UI is mounted at root.
    @app.get('/api/{path:path}')
    def missing_api(path: str):
        raise HTTPException(404, 'API endpoint not found')

    if web_dist and (web_dist / 'index.html').is_file():
        app.mount('/', StaticFiles(directory=web_dist, html=True), name='web')
    return app
