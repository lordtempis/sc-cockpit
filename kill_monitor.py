"""Read the current Star Citizen session's local-character death records."""
from collections import deque
from datetime import datetime
import csv
import os
from pathlib import Path
import re
import time

from session_activity import MARKERS, SessionActivity, latest_performance

# Match structured game records from the start, so message/chat text cannot
# impersonate an actor-death or account-login record.
HEADER = r'^<(?P<stamp>\d{4}-\d\d-\d\dT[\d:.]+Z)>\s+\[Notice\]\s+'
LOGIN = re.compile(HEADER + r'<AccountLoginCharacterStatus_Character>\s+Character:\s*(.*)$')
DEATH = re.compile(HEADER + r"<Actor Death>\s+CActor::Kill:\s*'(?P<victim>[^']+)'[.\s]*"
                   r"\[(?P<actor_id>\d+)\](?P<body>.*)$")
WEAPON = re.compile(r"\busing\s+'([^']*)'", re.I)
DAMAGE = re.compile(r"\bwith damage type\s+'([^']*)'", re.I)
CHUNK = 256 * 1024
MAX_LINE = 32 * 1024


def extract(pattern, body):
    match = pattern.search(body)
    return match[1][:160].strip() if match else ''


class DeathMonitor:
    def __init__(self, path, handle=None):
        self.path = Path(path)
        self.override = handle
        self.recent = deque(maxlen=512)
        self.identity = None
        self.reset_file()

    def reset_file(self):
        self.activity = SessionActivity()
        self.handle = self.override
        self.actor_id = None
        self.offset = 0
        self.anchor = b''
        self.partial = b''
        self.skipping_long_line = False

    def parse(self, line):
        login = LOGIN.match(line)
        if login:
            fields = dict(part.split(' ', 1) for part in login[2].split(' [', 1)[0].split(' - ')
                          if ' ' in part)
            name = fields.get('name', '').strip()
            self.handle = self.override or name or None
            self.actor_id = fields.get('geid') if not self.override or self.override.casefold() == name.casefold() else None
            return None
        self.activity.consume(line, self.actor_id)
        match = DEATH.match(line)
        if not match or not self.handle:
            return None
        if self.handle.casefold() != match['victim'].casefold() and (
                not self.actor_id or self.actor_id == '0' or self.actor_id != match['actor_id']):
            return None
        try:
            stamp = datetime.fromisoformat(match['stamp'].replace('Z', '+00:00')).isoformat()
        except ValueError:
            return None
        key = (stamp, match['actor_id'], match['victim'].casefold())
        if key in self.recent:
            return None
        self.recent.append(key)
        body = match['body']
        details = []
        for label, pattern in [('Weapon', WEAPON), ('Damage', DAMAGE)]:
            detail = extract(pattern, body)
            if detail and detail.lower() != 'unknown':
                details.append(label + ': ' + detail)
        value = ' · '.join(details) or 'Died'
        return {'time': stamp, 'label': 'Your death', 'value': value, 'warning': True}

    def poll(self):
        # A short-lived handle allows Windows to rotate the log between reads.
        with self.path.open('rb') as file:
            stat = os.fstat(file.fileno())
            identity = (stat.st_dev, stat.st_ino)
            file.seek(max(0, self.offset - len(self.anchor)))
            if (identity != self.identity or stat.st_size < self.offset or
                    file.read(len(self.anchor)) != self.anchor):
                self.reset_file()
                self.identity = identity
            file.seek(self.offset)
            chunk = file.read(CHUNK)
            self.offset += len(chunk)
            self.anchor = (self.anchor + chunk)[-64:]
        lines = (self.partial + chunk).split(b'\n')
        self.partial = lines.pop()
        events = []
        for line in lines:
            if self.skipping_long_line:
                self.skipping_long_line = False
            elif len(line) <= MAX_LINE and (b'<Actor Death>' in line or
                                            b'<AccountLoginCharacterStatus_Character>' in line or
                                            self.activity.pending or any(tag in line for tag in MARKERS)):
                event = self.parse(line.decode('utf-8', errors='replace').rstrip('\r'))
                if event:
                    events.append(event)
        if len(self.partial) > MAX_LINE:
            self.partial = b''
            self.skipping_long_line = True
        return events, len(chunk) == CHUNK

    def watch(self, state):
        next_performance = 0
        while True:
            try:
                events, backlog = self.poll()
                status = 'Watching Game.log' if self.handle else 'Waiting for character identity'
                if backlog:
                    status = 'Reading session history'
                activity = self.activity.snapshot()
                if backlog or self.actor_id in (None, '', '0'):
                    activity['kills'] = None
            except OSError as exc:
                activity = None
                events, backlog = [], False
                status = ('Waiting for Game.log' if isinstance(exc, FileNotFoundError)
                          else 'Game.log unreadable · retrying')
            performance_changed = False
            if time.monotonic() >= next_performance:
                next_performance = time.monotonic() + 60
                try:
                    performance = latest_performance(self.path.parent)
                except (OSError, csv.Error):
                    performance = None
                with state.lock:
                    performance_changed = state.performance != performance
                    state.performance = performance
            with state.lock:
                changed = performance_changed or events or state.kill_status != status or (activity is not None and state.activity != activity)
                if activity is not None:
                    state.activity = activity
                state.kill_status = status
                for event in events:
                    state.journal.entries.appendleft(event)
                    state.last_death = event
                if changed:
                    state.recordings.update(state)
            if not backlog:
                time.sleep(1)
