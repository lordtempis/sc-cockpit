"""Field delivery evidence from the decoder and the dashboard adapter."""
import json
import re
from pathlib import Path

from dashboard import ANNUNCIATORS, ASSISTS, POWER, player, vehicle, weapons


def installation(root):
    if not root:
        return {}
    root = Path(root)
    channel = root.name.upper()
    info = {'channel': channel} if channel in {'LIVE', 'PTU', 'EPTU', 'TECH-PREVIEW', 'HOTFIX', 'PREVIEW'} else {}
    try:
        data = json.loads((root / 'build_manifest.id').read_text(encoding='utf-8-sig'))['Data']
        info['build'] = ' · '.join(str(data[k]) for k in ('Version', 'RequestedP4ChangeNum') if data.get(k))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if not info.get('build'):
        try:
            with (root / 'Game.log').open('rb') as log:
                header = log.read(8192).decode('utf-8', errors='replace')
            number = re.search(r'BackupNameAttachment=" Build\((\d+)\)', header)
            version = re.search(r'FileVersion: ([0-9.]+)', header)
            info['build'] = ' · '.join(match[1] for match in (version, number) if match)
        except OSError:
            pass
    return info


def schema_fields(message, prefix=''):
    for field in message.fields:
        path = prefix + field.name
        if field.message_type is None:
            yield path
        else:
            yield from schema_fields(field.message_type, path + '.')


def leaves(data, prefix=''):
    for key, value in data.items():
        path = prefix + key
        if isinstance(value, dict):
            yield from leaves(value, path + '.')
        elif isinstance(value, list):
            grouped = {}
            for item in value:
                if isinstance(item, dict):
                    for field, reading in leaves(item, path + '.'):
                        grouped.setdefault(field, []).append(reading)
                else:
                    grouped.setdefault(path, []).append(item)
            yield from grouped.items()
        else:
            yield path, value


def delivered_fields(data, prefix=''):
    return (path for path, _ in leaves(data, prefix))


# Exact decoder inputs used by dashboard.py and the event journal. Other
# descriptor leaves are still observable in the decoder delivery table.
def adapter_inputs():
    paths = {'events.events.type'}
    groups = {
        'actor.vitals': ['heart_rate', 'pressure', 'gravity', 'intoxication_level', 'body_temperature.current',
                         *(g + '.' + k for g in ('health','oxygen','thirst','hunger') for k in ('current','max'))],
        'actor.environment': ['breathable_atmosphere','external_temperature','ambient_radiation_rate'],
        'actor.state': ['locomotion','crew_role','seat_name','uec_balance'],
        'vehicle.state': ['name','class_name','type','manufacturer','lights_enabled','doors.open_state','doors.lock_state'],
        'vehicle.state.flight.state': ['gear','vtol','transformation','is_landed'],
        'vehicle.state.flight.instruments': ['speed','altitude','radar_altitude','vertical_speed_indicator','heading',
                                            'boost.reserves','hydrogen_fuel','quantum_fuel'],
        'vehicle.state.power': POWER, 'vehicle.state.annunciators': ANNUNCIATORS,
        'vehicle.state.flight.settings': [key + '.active' for key in ASSISTS],
        'weapons': ['preset_name','preset_index','settings.gimbals_locked','settings.staggered_fire',
                    'weapons.name','weapons.ready_to_fire','weapons.is_powered','weapons.contained_in_preset'],
    }
    for prefix, keys in groups.items():
        paths.update(prefix + '.' + k for k in keys)
    return paths


MAPPED_INPUTS = adapter_inputs()
CATALOG = dict(leaves({'actor': player({}), 'vehicle': vehicle({}), 'weapons': weapons({})}))
CATALOG.update({'weapons.items.' + key: None for key in ('name', 'ready', 'power', 'selected')})
CATALOG['events.events.type'] = None


def unit(path, demo):
    key = path.rsplit('.', 1)[-1]
    if key in {'health','oxygen','hydration','nutrition','hydrogen_percent','quantum_percent','boost_percent'}:
        return '%'
    if key == 'uec_balance':
        return 'UEC'
    if key == 'hydrogen_m':
        return 'million HUD units'
    native = {'heart':'bpm', 'temperature':'°C', 'speed':'m/s', 'heading':'°',
              'vertical_speed_indicator':'m/s', 'altitude':'m', 'radar_altitude':'m',
              'boost':'fraction', 'quantum_fuel':'fraction'}
    if key == 'hydrogen_fuel':
        return 'tank fraction'
    if key in native or key in {'pressure','gravity','radiation','intoxication','external_temperature'}:
        return native.get(key, 'unverified') if demo else 'native · unverified'
    return '—'


class Diagnostics:
    def __init__(self, demo):
        self.demo = demo
        self.metadata = {}
        self.schema = set()
        self.packet = None
        self.mapped = {}
        self.raw = {}
        self.current = {}

    def observe(self, frame, panels, now):
        self.packet = now
        for section, data in frame.items():
            current = set()
            for path, value in leaves(data, section + '.'):
                self.raw[path] = (value, now)
                if path == 'events.events.type':
                    self.mapped[path] = (value, now)
                    current.add(path)
            for path, value in leaves(panels.get(section, {}), section + '.'):
                if path == 'weapons.items.name':
                    value = [item.get('name') for item in data.get('weapons', [])]
                if isinstance(value, list):
                    if not any(v is not None for v in value):
                        continue
                if value is not None:
                    self.mapped[path] = (value, now)
                    current.add(path)
            self.current[section] = current
        for section in ('vehicle', 'weapons'):
            if section not in panels:
                self.current.pop(section, None)

    def shift_clock(self, seconds):
        if self.packet is not None:
            self.packet += seconds
        for values in (self.raw, self.mapped):
            for path, (value, stamp) in values.items():
                values[path] = (value, stamp+seconds)

    def snapshot(self, now, stale_after):
        current = set().union(*self.current.values())
        rows = {}
        catalog = set(CATALOG) | self.mapped.keys()
        if self.demo:
            catalog.update(('vehicle.flight.quantum_percent','vehicle.flight.boost_percent'))
        for path in sorted(catalog):
            value, stamp = self.mapped.get(path, (None, None))
            age = None if stamp is None else max(0, now - stamp)
            state = 'never received' if age is None else 'fresh' if path in current and age < stale_after else 'stale · last known'
            rows[path] = [value, unit(path, self.demo), age, state]
        inputs = {}
        for path in sorted(self.schema | self.raw.keys() | MAPPED_INPUTS):
            data = self.raw.get(path)
            delivery = 'received' if data else 'unseen on wire' if path in self.schema and self.packet is not None else 'unknown'
            inputs[path] = [delivery, 'mapped' if path in MAPPED_INPUTS else 'unmapped',
                            data[0] if data else None, max(0, now-data[1]) if data else None]
        return {'metadata': dict(self.metadata), 'packet_age': None if self.packet is None else max(0, now-self.packet),
                'fields': rows, 'inputs': inputs}
