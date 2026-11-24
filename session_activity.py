"""Read local HUD, vehicle, navigation and saved performance records."""
from collections import deque
import csv
from datetime import datetime, timezone
import re


HUD = 'SHUDEvent_OnNotification'
SELECT = 'Player Selected Quantum Target - Local'
ARRIVE = 'Quantum Drive Arrived - Arrived at Final Destination'
TAGS = (HUD, 'MissionEnded', 'Vehicle Destroyed', SELECT, 'Calculate Route', ARRIVE)
MARKERS = tuple(('<' + tag + '>').encode() for tag in TAGS)
RECORD = re.compile(r'^<([^>]+)> \[Notice\] <([^>]+)> (.*)$')
NOTIFICATION = re.compile(r'^Added notification "([^"]+)" \[\d+\] to queue\..*?MissionId: \[([^\]]*)\], ObjectiveId: \[([^\]]*)\]')
DESTROYED = re.compile(r"^Vehicle '[^']+' \[(\d+)\] class '([^']+)' .*?destroyed: reason \[([^\]]+)\] level \[\d+\] -> \[2\] .*?killer '[^']*' \[(\d+)\]")
NAV = re.compile(r'^\[ItemNavigation\].*?\|\s*([^|\s]+)\[(\d+)\]\|CSCItemNavigation::[^|]+\|([^|]+?) \[Team_CGP4\]\[QuantumTravel\]$')
MISSION_ACTIONS = {'Contract Accepted', 'Contract Complete', 'New Objective', 'Objective Complete', 'Objective Withdrawn'}
ZONE_MESSAGES = {'Entering Armistice Zone - Combat Prohibited': ('Armistice', 'Inside'),
                 'Leaving Armistice Zone - Caution Advised': ('Armistice', 'Outside'),
                 'Entered Monitored Space': ('Monitored space', 'Inside'),
                 'Exited Monitored Space': ('Monitored space', 'Outside')}


class SessionActivity:
    def __init__(self):
        self.entries = deque(maxlen=200)
        self.zones = {}
        self.contracts = {}
        self.destroyed = set()
        self.last_kill = None
        self.routes = {}
        self.recent = deque(maxlen=512)
        self.pending = None

    def record(self, stamp, category, label, value):
        key = (stamp, label, value)
        if key in self.recent:
            return
        self.recent.append(key)
        self.entries.appendleft(dict(time=stamp, category=category, label=label, value=value, warning=False))

    def consume(self, line, actor_id):
        if self.pending:
            partial, self.pending = self.pending, None
            continuation = re.fullmatch(r'<[^>]+> (: " \[\d+\] to queue\..*)', line)
            if continuation:
                line = partial + continuation[1]
        match = RECORD.match(line)
        if not match or match[2] not in TAGS:
            return
        tag, body = match[2], match[3]
        try:
            stamp = datetime.fromisoformat(match[1].replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except ValueError:
            return
        if tag == HUD:
            notice = NOTIFICATION.match(body)
            if notice:
                self.notification(stamp, *notice.groups())
            elif body.startswith('Added notification "Objective ') and body.count('"') == 1:
                self.pending = line
        elif tag == 'Vehicle Destroyed':
            hit = DESTROYED.match(body)
            if hit and actor_id and actor_id != '0' and hit[4] == actor_id and hit[1] not in self.destroyed:
                self.destroyed.add(hit[1])
                self.last_kill = {'ship': hit[2], 'cause': hit[3], 'time': stamp}
                self.record(stamp, 'Combat', 'Ship kill credited to you', hit[2] + ' · ' + hit[3])
        elif tag == 'MissionEnded':
            end = re.fullmatch(r'Received MissionEnded push message for: mission_id ([\w-]+) - mission_state MISSION_STATE_COMPLETED \[Team_GameServices\]\[Missions\]', body)
            if end:
                self.complete(stamp, end[1])
        else:
            nav = NAV.match(body)
            if nav:
                self.navigation(stamp, tag, *nav.groups())

    def complete(self, stamp, mission_id, name=None):
        contract = self.contracts.setdefault(mission_id, {})
        if contract.get('complete'):
            return
        contract.update(complete=True, objectives={})
        self.record(stamp, 'Mission', 'Contract complete', name or contract.get('name') or mission_id)

    def notification(self, stamp, message, mission_id, objective_id):
        text = re.sub(r'</?EM\d+>', '', message).strip().rstrip(':').strip()
        action, _, detail = text.partition(':')
        detail = detail.strip()
        if action in MISSION_ACTIONS and mission_id.strip('0-') and detail:
            if action == 'Contract Complete':
                self.complete(stamp, mission_id, detail)
                return
            contract = self.contracts.setdefault(mission_id, {})
            objectives = contract.setdefault('objectives', {})
            if action == 'Contract Accepted':
                if contract.get('name') == detail:
                    return
                contract['name'] = detail
            elif action == 'New Objective':
                if contract.get('complete') or objectives.get(objective_id) == detail:
                    return
                objectives[objective_id] = detail
            else:
                objectives.pop(objective_id, None)
            self.record(stamp, 'Mission', action, detail)
        elif action == 'Hangar Request Completed':
            self.record(stamp, 'Zone', 'Hangar request', 'Completed')
        else:
            zone = ZONE_MESSAGES.get(action)
            jurisdiction = re.fullmatch(r'Entered (.{1,100}) Jurisdiction', action)
            if jurisdiction:
                zone = ('Jurisdiction', jurisdiction[1])
            if zone and self.zones.get(zone[0], {}).get('value') != zone[1]:
                self.zones[zone[0]] = {'time': stamp, 'value': zone[1]}
                self.record(stamp, 'Zone', *zone)

    def navigation(self, stamp, tag, ship, ship_id, message):
        route = self.routes.get(ship_id)
        if tag == SELECT:
            target = re.fullmatch(r'Player has selected point (.+) as their destination, routing locally', message)
            if target:
                self.routes[ship_id] = target[1]
                self.record(stamp, 'Travel', 'Quantum target selected', ship + ' · ' + target[1])
        elif tag == 'Calculate Route':
            match = re.fullmatch(r'Projected Start Location is (.+) for route to destination (.+)', message)
            if match:
                self.routes[ship_id] = match[2]
                self.record(stamp, 'Travel', 'Projected route', ship + ' · ' + match[1] + ' → ' + match[2])
        elif tag == ARRIVE and message == 'Quantum Drive has arrived at final destination':
            self.routes.pop(ship_id, None)
            detail = ' · Last reported target: ' + route if route else ''
            self.record(stamp, 'Travel', 'Quantum arrival', ship + detail)

    def snapshot(self):
        active = [{'name': c.get('name'), 'objectives': list(c.get('objectives', {}).values())}
                  for c in self.contracts.values() if not c.get('complete')]
        return {'missions': active, 'zones': dict(self.zones), 'kills': len(self.destroyed),
                'last_kill': self.last_kill, 'entries': list(self.entries)}


def latest_performance(directory):
    """Read the last complete row of the newest saved level report."""
    files = sorted(directory.glob('level_stats_*.csv'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        with path.open(encoding='utf-8-sig', errors='replace', newline='') as stream:
            reader = csv.reader(stream, delimiter=';')
            header = next(reader, [])
            subheader = next(reader, [])
            if header[:4] != ['Debug skip', 'Run', 'Frames', 'Entities']:
                continue
            indices = [i for i, name in enumerate(header) if name in ('CPU/Bucket', 'GPU')]
            rows = None
            for row in reader:
                if len(row) != len(header) or not row[2].isdigit():
                    continue
                rows = {'Frames': row[2]}
                for i, name in enumerate(header):
                    if name in ('Entities', 'Memory - Working Set', 'Memory - Commited') and i < len(subheader):
                        label = {'Entities':'Entities', 'Memory - Working Set':'Working set',
                                 'Memory - Commited':'Committed memory'}[name]
                        for j in range(i, min(i+3, len(subheader))):
                            if subheader[j] == 'Max':
                                rows['Peak ' + label.lower()] = row[j].strip()
                                break
                for i in indices:
                    if header[i] == 'GPU' and row[i] == 'INVALID':
                        rows['GPU average'] = None
                    if i+1 >= len(row) or row[i] in ('', 'INVALID'):
                        continue
                    label = 'GPU' if header[i] == 'GPU' else row[i]
                    rows[label + ' average'] = row[i+1].strip()
                    if header[i+2:i+3] == ['<16ms']:
                        rows[label + ' under 16 ms'] = row[i+2].strip()
            if rows:
                return {'file': path.name, 'time': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), 'rows': rows}
    return None
