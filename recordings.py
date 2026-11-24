"""Local, indexed dashboard recordings. Playback reads immutable time slices."""
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import zlib
from datetime import datetime

SUFFIX = '.screc'
NAME = re.compile(r'session-\d{8}-\d{6}-[0-9a-f]{8}\.screc')
FORMAT = 'SC Cockpit 1'


def local_directory():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share') / 'SC-Cockpit' / 'recordings'


class Recordings:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else local_directory()
        self.db = None
        self.name = None
        self.error = None

    def files(self):
        return sorted((p.name for p in self.directory.glob('session-*' + SUFFIX)
                       if NAME.fullmatch(p.name) and p.is_file()), reverse=True)

    def start(self, state, now):
        if self.db:
            raise ValueError('Already recording')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.name = datetime.now().strftime('session-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8] + SUFFIX
        self.started = now
        self.error = None
        try:
            self.db = sqlite3.connect(self.directory / (self.name + '.partial'), check_same_thread=False)
            self.db.executescript('CREATE TABLE metadata (data TEXT); CREATE TABLE samples (at REAL, data BLOB);'
                                  'CREATE INDEX by_time ON samples(at); CREATE TABLE bookmarks (at REAL, label TEXT);')
            meta = dict(format=FORMAT, source='demo' if state.demo else 'live', **state.diagnostics.metadata)
            self.db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(meta),))
            self.write(state, now)
            self.db.commit()
            self.committed = now
        except Exception:
            self.abort()
            raise

    def write(self, state, now):
        sample = state.snapshot(now)
        payload = zlib.compress(json.dumps(sample, separators=(',', ':'), allow_nan=False).encode(), 1)
        self.db.execute('INSERT INTO samples VALUES (?,?)', (max(0, now-self.started), payload))

    def update(self, state, now=None):
        if self.db:
            now = time.monotonic() if now is None else now
            try:
                self.write(state, now)
                if now-self.committed >= 1:
                    self.db.commit()
                    self.committed = now
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.abort()
                self.error = 'Recording interrupted: ' + str(exc)

    def abort(self):
        if self.db:
            self.db.close()
        self.db = None
        self.name = None

    def bookmark(self, label, now):
        if not self.db:
            raise ValueError('Start a recording first')
        if not isinstance(label, str) or len(label) > 120:
            raise ValueError('Bookmark labels can contain up to 120 characters')
        self.db.execute('INSERT INTO bookmarks VALUES (?,?)', (max(0, now-self.started), label.strip() or 'Bookmark'))
        self.db.commit()

    def stop(self, state, now):
        if not self.db:
            raise ValueError('No recording in progress')
        name = self.name
        try:
            self.write(state, now)
            self.db.commit()
            self.abort()
            (self.directory / (name + '.partial')).rename(self.directory / name)
        except Exception:
            self.abort()
            raise
        return name

    def replay(self, name, position, stale_after):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('Select a saved recording')
        if not math.isfinite(position):
            raise ValueError('Invalid replay position')
        path = self.directory / name
        if not path.is_file():
            raise ValueError('Recording not found')
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            metadata = json.loads(db.execute('SELECT data FROM metadata').fetchone()[0])
            if metadata.get('format') != FORMAT:
                raise ValueError('Unknown recording format')
            duration = db.execute('SELECT MAX(at) FROM samples').fetchone()[0]
            position = max(0, min(position, duration))
            at, blob = db.execute('SELECT at,data FROM samples WHERE at<=? ORDER BY at DESC,rowid DESC LIMIT 1',
                                  (position,)).fetchone()
            sample = json.loads(zlib.decompress(blob))
            marks = db.execute('SELECT at,label FROM bookmarks ORDER BY at,rowid').fetchall()
        finally:
            db.close()
        advance(sample, position-at, stale_after)
        sample['original_mode'] = sample['mode']
        sample['mode'] = 'replay'
        sample['replay'] = dict(file=name, position=position, duration=duration, bookmarks=marks)
        return sample


def advance(sample, elapsed, stale_after):
    """Advance recorded ages between packets without inventing an update."""
    dash = sample['dashboard']
    if dash['feed'] == 'paused':
        return
    diag = sample['diagnostics']
    if diag['packet_age'] is not None:
        diag['packet_age'] += elapsed
    for row in diag['fields'].values():
        if row[2] is not None:
            row[2] += elapsed
            if row[2] >= stale_after:
                row[3] = 'stale · last known'
    for row in diag['inputs'].values():
        if row[3] is not None:
            row[3] += elapsed
    for key in sample['ages']:
        sample['ages'][key] += elapsed
        if sample['ages'][key] >= stale_after:
            sample['frame'].pop(key, None)
    dash['history'] = [[age+elapsed, value] for age, value in dash['history'] if age+elapsed <= 60]
    for event in sample['events']:
        event['age'] += elapsed
    sample['events'] = [event for event in sample['events'] if event['age'] < 15]
    if 'actor' not in sample['frame']:
        sample['values'] = dict.fromkeys(sample['values'])
        sample['active'] = []
        sample['events'] = []
    if dash['feed'] == 'live' and diag['packet_age'] is not None and diag['packet_age'] >= stale_after:
        dash['feed'] = 'stale'
        sample['connected'] = False
        sample['status'] = 'No fresh telemetry'
