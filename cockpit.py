"""Local, read-only Star Citizen cockpit. Python 3.11+."""
from __future__ import annotations

from kill_monitor import DeathMonitor
from recordings import Recordings
from diagnostics import Diagnostics, installation, schema_fields
from demo import SCENARIOS, sample
from dashboard import Journal, lookup, percent, player, vehicle, weapons

from copy import deepcopy
import argparse
import json
import math
from pathlib import Path
import sys
import sqlite3
import zlib
import threading
import time
import uuid
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
SERVICE = 'sc.game.telemetry.v1.GameTelemetryService'
STALE_AFTER = 5
METRICS = {
    'health': ('Health', 'actor.vitals.health'),
    'oxygen': ('Oxygen', 'actor.vitals.oxygen'),
    'hunger': ('Nutrition', 'actor.vitals.hunger'),
    'thirst': ('Hydration', 'actor.vitals.thirst'),
}
DEFAULT_RULES = {
    key: {'enabled': True, 'threshold': threshold}
    for key, threshold in [('health', 25), ('oxygen', 20), ('hunger', 15), ('thirst', 15)]
}


def validate_rules(value):
    if not isinstance(value, dict) or set(value) != set(METRICS):
        raise ValueError('Supply all four vital alert rules.')
    result = {}
    for key, item in value.items():
        if not isinstance(item, dict) or type(item.get('enabled')) is not bool:
            raise ValueError('Each rule needs an enabled switch.')
        n = item.get('threshold')
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 1 <= n <= 99:
            raise ValueError('Thresholds must be between 1 and 99 percent.')
        result[key] = {'enabled': item['enabled'], 'threshold': n}
    return result


class Alerts:
    """One warning per low episode; five percentage points rearm hysteresis."""
    def __init__(self):
        self.latched = set()
        self.last = {}
        self.counter = 0
        self.session = uuid.uuid4().hex
        self.events = []

    def update(self, values, rules, now):
        active = []
        for key, rule in rules.items():
            value = values.get(key)
            if not rule['enabled']:
                self.latched.discard(key)
                continue
            if value is None:
                continue
            threshold = rule['threshold']
            if value >= min(100, threshold + 5):
                self.latched.discard(key)
            if value <= threshold:
                active.append(key)
                if key not in self.latched and now - self.last.get(key, -math.inf) >= 60:
                    self.latched.add(key)
                    self.last[key] = now
                    self.counter += 1
                    self.events.append({'id': f'{self.session}:{self.counter}', 'key': key,
                                        'text': f'{METRICS[key][0]} low. {round(value)} percent.',
                                        'at': now})
        self.events = [event for event in self.events if now - event['at'] < 15][-16:]
        return active


def remember_fields(known, readings):
    """Remember presence only. A later omission must never replay an old value."""
    for key, value in readings.items():
        if isinstance(value, dict):
            remember_fields(known.setdefault(key, {}), value)
        elif key == 'items':
            items = known.setdefault('items', {})
            for weapon in value:
                remember_fields(items.setdefault(weapon['name'], {}), weapon)
        elif value is not None and value != '':
            known[key] = None


class State:
    def __init__(self, demo=False, recording_directory=None):
        self.lock = threading.RLock()
        self.demo = demo
        self.diagnostics = Diagnostics(demo)
        self.recordings = Recordings(recording_directory)
        self.scenario = "Approach"
        self.paused_at = None
        self.journal = Journal()
        self.kill_status = "Demo mode" if demo else "No Game.log configured"
        self.last_death = None
        self.activity = None
        self.performance = None
        self.panels = {}
        self.reported = {}
        self.offline = False
        self.frames = {}
        self.updated = {}
        self.last_sample_at = None
        self.status = 'Waiting for telemetry'
        self.rules = json.loads(json.dumps(DEFAULT_RULES))
        self.alerts = Alerts()

    def clear(self, status, now=None):
        with self.lock:
            changed = not self.offline or self.status != status
            self.last_sample_at = None
            self.offline = True
            self.diagnostics.current.clear()
            self.journal.observe('Feed', 'Disconnected', warning=True)
            self.journal.leave_ship()
            self.forget_weapon_roster()
            self.panels.clear()
            self.frames.clear()
            self.updated.clear()
            self.status = status
            self.alerts = Alerts()
            if changed:
                self.recordings.update(self, now)

    def forget_weapon_roster(self):
        self.reported.get('weapons', {}).pop('items', None)

    def record_panel(self, section, readings):
        self.panels[section] = readings
        remember_fields(self.reported.setdefault(section, {}), readings)

    def ingest(self, frame, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            # Sections are independently refreshed. Replace complete sections,
            # rather than preserving stale scalar fields from older messages.
            for section, value in frame.items():
                self.frames[section] = value
                self.updated[section] = now
            self.offline = False
            self.status = 'Receiving telemetry'
            self.last_sample_at = time.time()
            self.journal.observe('Feed', 'Receiving')
            if 'events' in frame:
                self.journal.game_events(frame['events'].get('events', []))
            if 'actor' in frame:
                actor = player(frame['actor'])
                self.record_panel('actor', actor)
                self.journal.observe('Player context', actor['context'])
                for key, (_, path) in METRICS.items():
                    value = percent(lookup(frame, path))
                    rule = self.rules[key]
                    if value is not None and rule['enabled']:
                        low = value <= rule['threshold']
                        self.journal.observe('Vital · ' + METRICS[key][0], 'Low' if low else 'Normal', low)
            if self.panels.get('actor', {}).get('context') in ('On foot', 'EVA') and now - self.updated.get('actor', now) < STALE_AFTER:
                self.forget_weapon_roster()
                for section in ('vehicle', 'weapons'):
                    self.panels.pop(section, None)
                    self.frames.pop(section, None)
                    self.updated.pop(section, None)
                self.journal.leave_ship()
            else:
                if 'vehicle' in frame:
                    ship = vehicle(frame['vehicle'])
                    if self.demo:
                        for key, dest in [('quantum_fuel','quantum_percent'), ('boost','boost_percent')]:
                            reading = ship['flight'][key]
                            ship['flight'][dest] = round(reading * 100, 1) if reading is not None else None
                    if ship['name'] and self.journal.ship and ship['name'] != self.journal.ship:
                        self.forget_weapon_roster()
                        self.panels.pop('weapons', None)
                        if 'weapons' not in frame:
                            self.frames.pop('weapons', None)
                            self.updated.pop('weapons', None)
                    self.record_panel('vehicle', ship)
                    self.journal.observe_vehicle(ship, now)
                    if self.demo and ship['flight']['hydrogen_fuel'] is not None:
                        low = ship['flight']['hydrogen_fuel'] <= .12
                        self.journal.observe('Demo hydrogen', 'Low' if low else 'Normal', low)
                if 'weapons' in frame:
                    armament = weapons(frame['weapons'])
                    self.record_panel('weapons', armament)
                    index = armament['preset_index']
                    if index is not None:
                        self.journal.observe('Weapon preset index', index)
                    self.journal.observe('Weapon preset', armament['preset'])

            self.diagnostics.observe(frame, self.panels, now)
            self.recordings.update(self, now)

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.paused_at is not None:
                now = self.paused_at
            fresh = {key: value for key, value in self.frames.items()
                     if now - self.updated[key] < STALE_AFTER}
            values = {key: percent(lookup(fresh, path)) for key, (_, path) in METRICS.items()}
            # Never reuse a previous vehicle section when actor reports on foot/EVA.
            locomotion = lookup(fresh, 'actor.state.locomotion')
            if locomotion in (1, 2):
                fresh.pop('vehicle', None)
            active = self.alerts.update(values, self.rules, now)
            events = [dict(event, age=round(now - event['at'], 2))
                      for event in self.alerts.events
                      if event['key'] in active and now - event['at'] < 15]
            packet = self.diagnostics.packet
            connected = not self.offline and packet is not None and now-packet < STALE_AFTER
            feed = ('paused' if self.paused_at is not None else 'disconnected' if self.offline else 'live' if connected else
                    'stale' if packet is not None else 'waiting')
            if feed == 'stale':
                self.journal.observe('Feed', 'Stale', warning=True)
            # The HTTP handler serializes after releasing the lock.
            dashboard = {'reported': deepcopy(self.reported), 'feed': feed, 'sections': dict(self.panels), 'scenario': self.scenario,
                         'journal': list(self.journal.entries),
                         'scenarios': SCENARIOS if self.demo else (),
                         'history': [[round(now-ts, 2), value] for ts, value in self.journal.history
                                     if 0 <= now-ts <= 60]}
            if self.activity is not None:
                dashboard['activity'] = {k: v for k, v in self.activity.items() if k != 'entries'}
                combined = self.activity['entries'] + dashboard['journal']
                dashboard['journal'] = sorted(combined, key=lambda event: event['time'], reverse=True)[:200]
            dashboard['performance'] = self.performance
            dashboard['kill_monitor'] = {'status': self.kill_status, 'last_death': self.last_death,
                                         'last_telemetry_report': self.journal.last_casualty}
            return {'dashboard': dashboard, 'diagnostics': self.diagnostics.snapshot(now, STALE_AFTER),
                    'last_sample_at': self.last_sample_at, 'mode': 'demo' if self.demo else 'live',
                    'connected': connected, 'status': self.status if connected else 'No fresh telemetry',
                    'detail': self.status, 'values': values, 'frame': fresh,
                    'active': active, 'events': events, 'rules': self.rules,
                    'ages': {k: max(0, now-v) for k, v in self.updated.items()}}


def stream_messages(schema):
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    descriptor = descriptor_pb2.FileDescriptorSet.FromString(schema.read_bytes())
    # Decode the original wire tags with explicit scalar presence. Adding
    # synthetic oneofs locally preserves explicitly encoded zero/false without
    # materializing unreported proto3 defaults. Field numbers/types stay intact.
    for file in descriptor.file:
        if file.syntax == 'proto3':
            pending_messages = list(file.message_type)
            while pending_messages:
                message = pending_messages.pop()
                pending_messages.extend(message.nested_type)
                for field in message.field:
                    if (field.label == field.LABEL_OPTIONAL and field.type != field.TYPE_MESSAGE
                            and not field.HasField('oneof_index')):
                        field.oneof_index = len(message.oneof_decl)
                        message.oneof_decl.add(name='_cockpit_present_' + field.name)
                        field.proto3_optional = True
    pool = descriptor_pool.DescriptorPool()
    pending = list(descriptor.file)
    while pending:
        remaining = []
        for item in pending:
            try:
                pool.Add(item)
            except (TypeError, ValueError):
                remaining.append(item)
        if len(remaining) == len(pending):
            raise ValueError('Descriptor dependencies are missing; use --include_imports when exporting.')
        pending = remaining
    method = pool.FindServiceByName(SERVICE).FindMethodByName('TelemetryStream')
    request = message_factory.GetMessageClass(method.input_type)
    response = message_factory.GetMessageClass(method.output_type)
    return request, response


def game_frames(request, response, timeout):
    import grpc
    from google.protobuf.json_format import MessageToDict

    with grpc.insecure_channel('127.0.0.1:2942', options=[('grpc.max_receive_message_length', 2*1024*1024)]) as channel:
        stream = channel.unary_stream('/' + SERVICE + '/TelemetryStream',
            request_serializer=lambda msg: msg.SerializeToString(),
            response_deserializer=response.FromString)
        for frame in stream(request(), timeout=timeout):
            # Only values actually encoded, including explicit zero/false.
            yield MessageToDict(frame, preserving_proto_field_name=True,
                                always_print_fields_with_no_presence=False,
                                use_integers_for_enums=True)


def read_game(state, schema):
    import grpc

    request, response = stream_messages(schema)
    with state.lock:
        state.diagnostics.schema = set(schema_fields(response.DESCRIPTOR))
    while True:
        try:
            for frame in game_frames(request, response, timeout=3600):
                state.ingest(frame)
        except grpc.RpcError:
            state.clear('Game unavailable; reconnecting to local telemetry')
        else:
            state.clear('Telemetry stream ended; reconnecting')
        time.sleep(2)


def demo_stream(state):
    start = time.monotonic()
    previous = None
    while True:
        with state.lock:
            scenario = state.scenario
            if state.paused_at is not None:
                pass
            elif scenario == 'Waiting for data':
                if previous != scenario:
                    state.clear('Waiting for first sample')
                    state.offline = False
                    state.journal = Journal()
                    state.diagnostics = Diagnostics(True)
                    state.recordings.update(state)
            elif scenario == 'Disconnected':
                if previous != scenario:
                    state.clear('Simulated disconnect')
            elif scenario != 'Stale feed' or previous != scenario:
                frame = sample(scenario, time.monotonic() - start)
                if previous != scenario:
                    for section in set(state.frames) - frame.keys():
                        state.frames.pop(section, None)
                        state.panels.pop(section, None)
                        state.updated.pop(section, None)
                state.ingest(frame)
                if scenario == 'Stale feed':
                    state.updated = {key: stamp - STALE_AFTER for key, stamp in state.updated.items()}
                    state.diagnostics.shift_clock(-STALE_AFTER)
                    state.recordings.update(state)
            previous = scenario
        time.sleep(.25)


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def allowed_host(self):
            return self.headers.get('Host') in {
                f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def send(self, code, body, content_type='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {'error': 'Localhost access only'})
            path = urlparse(self.path).path
            if path == '/api/state':
                with state.lock:
                    sample = state.snapshot()
                    sample['recording'] = state.recordings.name
                    sample['recording_error'] = state.recordings.error
                return self.send(200, sample)
            if path == '/api/recordings':
                return self.send(200, state.recordings.files())
            if path == '/api/replay':
                try:
                    query = parse_qs(urlparse(self.path).query)
                    result = state.recordings.replay(query.get('file', [''])[0],
                                                     float(query.get('at', ['0'])[0]), STALE_AFTER)
                    return self.send(200, result)
                except (ValueError, OSError, sqlite3.Error, TypeError, IndexError, KeyError, zlib.error):
                    return self.send(400, {'error': 'Unable to open this recording or position'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.send(404, {'error': 'Not found'})
            name, mime = files[path]
            self.send(200, (ROOT/'web'/name).read_bytes(), mime)

        def do_POST(self):
            if not self.allowed_host():
                return self.send(403, {'error': 'Localhost access only'})
            origin = self.headers.get('Origin')
            if origin not in {f'http://127.0.0.1:{self.server.server_port}',
                              f'http://localhost:{self.server.server_port}'}:
                return self.send(403, {'error': 'Same-origin requests only'})
            if self.path == '/api/recording':
                try:
                    size = int(self.headers.get('Content-Length', 0))
                    if not 0 < size <= 2048:
                        raise ValueError('Invalid recording command')
                    command = json.loads(self.rfile.read(size))
                    with state.lock:
                        now = time.monotonic()
                        action = command.get('action') if isinstance(command, dict) else None
                        saved = None
                        if action == 'start':
                            state.recordings.start(state, now)
                        elif action == 'stop':
                            saved = state.recordings.stop(state, now)
                        elif action == 'bookmark':
                            state.recordings.bookmark(command.get('label', ''), now)
                        else:
                            raise ValueError('Unknown recording command')
                        name = state.recordings.name
                    return self.send(200, {'recording': name, 'saved': saved})
                except (ValueError, OSError, sqlite3.Error) as exc:
                    return self.send(400, {'error': str(exc)})
            if self.path == '/api/scenario' and state.demo:
                try:
                    size = int(self.headers.get('Content-Length', 0))
                    if not 0 < size <= 128:
                        raise ValueError
                    scenario = json.loads(self.rfile.read(size))
                    if scenario not in (*SCENARIOS, 'Pause', 'Resume'):
                        raise ValueError
                    with state.lock:
                        if scenario == 'Pause':
                            if state.paused_at is None and state.updated:
                                state.paused_at = time.monotonic()
                        elif scenario == 'Resume':
                            if state.paused_at is not None:
                                delta = time.monotonic() - state.paused_at
                                for section in state.updated:
                                    state.updated[section] += delta
                                points = state.journal.history
                                shifted = [(ts + delta, val) for ts, val in points]
                                points.clear()
                                points.extend(shifted)
                                state.diagnostics.shift_clock(delta)
                                state.paused_at = None
                        else:
                            state.paused_at = None
                            state.scenario = scenario
                        state.recordings.update(state)
                    return self.send(200, {'ok': True})
                except (TypeError, ValueError):
                    return self.send(400, {'error': 'Unknown demo scenario'})
            if self.path != '/api/rules':
                return self.send(404, {'error': 'Not found'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    raise ValueError('Invalid request size')
                rules = validate_rules(json.loads(self.rfile.read(size)))
                with state.lock:
                    state.rules = rules
                    state.alerts = Alerts()
                self.send(200, {'ok': True})
            except (ValueError, TypeError):
                self.send(400, {'error': 'Invalid alert rules'})

        def log_message(self, *args):
            pass
    return Handler


def tray_status(state, now):
    with state.lock:
        if state.status.startswith('Reader setup failed'):
            label = 'Reader setup failed'
        elif state.paused_at is not None:
            label = 'Demo paused'
        elif state.offline or state.diagnostics.packet is None:
            label = 'Waiting for telemetry'
        elif now - state.diagnostics.packet >= STALE_AFTER:
            label = 'Telemetry stale'
        else:
            label = 'Demo telemetry' if state.demo else 'Receiving telemetry'
        return label + (' · Recording' if state.recordings.db else '')


class TrayServer(ThreadingHTTPServer):
    """Publish status changes only to the parent launcher, over its private pipe."""
    last_status = None

    def service_actions(self):
        label = tray_status(self.state, time.monotonic())
        if label != self.last_status:
            self.last_status = label
            print('TRAY STATUS ' + label, flush=True)


def watch_launcher(server, commands):
    # EOF also arrives when Windows closes the parent unexpectedly.
    for command in commands:
        if command.strip() == 'quit':
            break
    server.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--demo', action='store_true', help='Clearly labeled simulated data')
    mode.add_argument('--wait-for-game', action='store_true', help='Open the dashboard while locating an installation')
    mode.add_argument('--schema', type=Path, help='FileDescriptorSet for your installed game build')
    mode.add_argument('--game-exe', type=Path, help='Read the schema from this installed game executable')
    parser.add_argument('--game-log', type=Path, help='Override the live Game.log path')
    parser.add_argument('--player-handle', help='Match this exact victim handle instead of login identity')
    parser.add_argument('--tray', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--open-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8743)
    args = parser.parse_args()
    state = State(demo=args.demo)
    if args.wait_for_game:
        state.status = 'Waiting for a game installation. Choose Locate game in the SC Cockpit tray menu.'
    state.diagnostics.metadata = installation(args.game_exe.parent.parent if args.game_exe else
                                              args.game_log.parent if args.game_log and not args.demo else None)
    def worker():
        try:
            if args.demo:
                demo_stream(state)
            elif args.game_exe:
                from extract_schema import extract
                with tempfile.TemporaryDirectory() as directory:
                    schema = Path(directory) / 'live.desc'
                    extract(args.game_exe, schema)
                    read_game(state, schema)
            elif args.schema:
                read_game(state, args.schema)
        except Exception as exc:
            state.clear('Reader setup failed: ' + type(exc).__name__)
            print('Reader setup failed:', str(exc), flush=True)
    server_type = TrayServer if args.tray else ThreadingHTTPServer
    server = server_type(('127.0.0.1', args.port), handler_for(state))
    if args.tray:
        server.state = state
        threading.Thread(target=watch_launcher, args=(server, sys.stdin), daemon=True).start()
        print(f'TRAY PORT {server.server_port}', flush=True)
    log = args.game_log
    if not log and args.game_exe:
        log = args.game_exe.parent.parent / 'Game.log'
    if log and not args.demo:
        state.kill_status = 'Opening Game.log'
        monitor = DeathMonitor(log, args.player_handle)
        threading.Thread(target=monitor.watch, args=(state,), daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    print(f'Cockpit: http://127.0.0.1:{server.server_port} ({"DEMO" if args.demo else "live reader"})', flush=True)
    if args.open_browser:
        webbrowser.open(f'http://127.0.0.1:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with state.lock:
            if state.recordings.db:
                state.recordings.stop(state, time.monotonic())


if __name__ == '__main__':
    main()
