import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class ReportStore:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS report_previews (
              id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, payload TEXT NOT NULL,
              status TEXT NOT NULL, report_id TEXT, approval TEXT);
            CREATE TABLE IF NOT EXISTS report_jobs (
              id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
              stage TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
              result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS report_events (
              report_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(report_id, seq));
            ''')
        for row in self.conn.execute("SELECT id FROM report_jobs WHERE status IN ('queued','running')").fetchall():
            self.fail(row['id'], '서버 재시작으로 분석이 중단되었습니다. 다시 요청해 주세요.')

    def create_preview(self, thread_id, payload):
        preview_id = uuid.uuid4().hex
        with self.lock, self.conn:
            self.conn.execute('INSERT INTO report_previews (id,thread_id,payload,status) VALUES (?,?,?,?)',
                              (preview_id, thread_id, json.dumps(payload, ensure_ascii=False), 'pending'))
        return {**payload, 'preview_id': preview_id, 'status': 'pending'}

    def get_preview(self, preview_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM report_previews WHERE id=?', (preview_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        value['approval'] = json.loads(value['approval']) if value['approval'] else None
        return value

    def cancel_preview(self, preview_id):
        with self.lock, self.conn:
            self.conn.execute("UPDATE report_previews SET status='cancelled' WHERE id=? AND status='pending'", (preview_id,))

    def create(self, request, preview_id=None):
        report_id = uuid.uuid4().hex
        with self.lock, self.conn:
            self.conn.execute('INSERT INTO report_jobs (id,request,status,stage,created_at) VALUES (?,?,?,?,?)',
                              (report_id, json.dumps(request, ensure_ascii=False), 'queued', 'queued', now()))
            if preview_id is not None:
                updated = self.conn.execute(
                    "UPDATE report_previews SET status='approved',report_id=?,approval=? WHERE id=? AND status='pending'",
                    (report_id, json.dumps(request, ensure_ascii=False), preview_id)).rowcount
                if updated != 1:
                    raise ValueError('이미 처리된 분석 제안입니다.')
            self.event(report_id, {'step': 'queued', 'message': '분석을 준비하고 있습니다.'})
        return report_id

    def get(self, report_id):
        with self.lock:
            row = self.conn.execute('SELECT * FROM report_jobs WHERE id=?', (report_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value['report_id'] = value.pop('id')
        value['request'] = json.loads(value['request'])
        value['result'] = json.loads(value['result']) if value['result'] else None
        return value

    def event(self, report_id, payload):
        with self.lock, self.conn:
            seq = self.conn.execute('SELECT COALESCE(MAX(seq),0)+1 FROM report_events WHERE report_id=?',
                                    (report_id,)).fetchone()[0]
            self.conn.execute('INSERT INTO report_events VALUES (?,?,?)',
                              (report_id, seq, json.dumps(payload, ensure_ascii=False)))
            self.conn.execute('UPDATE report_jobs SET stage=? WHERE id=?', (payload['step'], report_id))
        return seq

    def events(self, report_id, after=0):
        with self.lock:
            return [(row['seq'], json.loads(row['payload'])) for row in self.conn.execute(
                'SELECT seq,payload FROM report_events WHERE report_id=? AND seq>? ORDER BY seq',
                (report_id, after)).fetchall()]

    def running(self, report_id):
        with self.lock, self.conn:
            self.conn.execute("UPDATE report_jobs SET status='running' WHERE id=?", (report_id,))

    def complete(self, report_id, result):
        with self.lock, self.conn:
            self.conn.execute("UPDATE report_jobs SET status='completed',result=?,finished_at=? WHERE id=?",
                              (json.dumps(result, ensure_ascii=False), now(), report_id))
            self.event(report_id, {'step': 'completed', 'message': '보고서가 완성되었습니다.'})

    def fail(self, report_id, message):
        with self.lock, self.conn:
            self.conn.execute("UPDATE report_jobs SET status='failed',error=?,finished_at=? WHERE id=?",
                              (message, now(), report_id))
            self.event(report_id, {'step': 'failed', 'message': message})

    def close(self):
        with self.lock:
            self.conn.close()
