"""Presentation boundary for the installed LIVE telemetry schema."""
import math
import re
from collections import deque
from datetime import datetime, timezone


def lookup(data, path):
    for key in path.split('.'):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def number(value):
    if type(value) in (int, float) and math.isfinite(value) and abs(value) < 3.4e38:
        return value
    return None


def percent(gauge):
    if not isinstance(gauge, dict):
        return None
    current = number(gauge.get('current'))
    maximum = number(gauge.get('max'))
    if current is None or maximum is None or not 0 <= current <= maximum or maximum <= 0:
        return None
    return round(100 * current / maximum, 1)


def enum(value, labels):
    if type(value) is int and 0 < value <= len(labels):
        return labels[value - 1]
    return None


def switch(data, path):
    value = lookup(data, path)
    return ('On' if value else 'Off') if type(value) is bool else None


def player(actor):
    vitals = actor.get('vitals', {})
    state = actor.get('state', {})
    breathable = lookup(actor, 'environment.breathable_atmosphere')
    # Protobuf JSON represents int64 balances as decimal strings.
    balance = state.get('uec_balance')
    credits = str(balance) if type(balance) in (str, int) else ''
    return {
        'health': percent(vitals.get('health')), 'oxygen': percent(vitals.get('oxygen')),
        'heart': number(vitals.get('heart_rate')),
        'temperature': number(lookup(vitals, 'body_temperature.current')),
        'pressure': number(vitals.get('pressure')), 'gravity': number(vitals.get('gravity')),
        'intoxication': number(vitals.get('intoxication_level')),
        'external_temperature': number(lookup(actor, 'environment.external_temperature')),
        'radiation': number(lookup(actor, 'environment.ambient_radiation_rate')),
        'hydration': percent(vitals.get('thirst')), 'nutrition': percent(vitals.get('hunger')),
        'air': ('Breathable' if breathable else 'Unbreathable') if type(breathable) is bool else None,
        'context': enum(state.get('locomotion'), ['On foot', 'EVA', 'Seated']),
        'role': enum(state.get('crew_role'), ['Pilot', 'Gunner', 'Crew']),
        'seat': state.get('seat_name') or None,
        'uec_balance': credits if re.fullmatch(r'-?[0-9]{1,19}', credits) else None,
    }


DEPLOYMENT = ['Retracted', 'Deploying', 'Deployed', 'Retracting']
ANNUNCIATOR = ['Nominal', 'Off', 'Minor · flashing', 'Minor', 'Major · flashing', 'Major']
ANNUNCIATORS = {'pwr': 'Power warning', 'wpn': 'Weapons warning', 'thr': 'Thruster warning',
                'shld': 'Shield warning', 'cool': 'Cooling warning', 'qdrv': 'Quantum drive',
                'radr': 'Radar warning', 'prox': 'Proximity', 'hit': 'Hit warning', 'misl': 'Missile warning'}
ASSISTS = {'ifcs_coupled': 'Coupled flight', 'ifcs_esp': 'ESP', 'ifcs_g_safe': 'G-safe',
           'ifcs_gravity_compensation': 'Gravity compensation', 'ifcs_cruise_mode': 'Cruise',
           'ifcs_speed_limiter': 'Speed limiter', 'ifcs_proximity_assist': 'Proximity assist',
           'ifcs_stability': 'Stability assist', 'ifcs_wind_compensation': 'Wind compensation',
           'ifcs_proximity_assist_landing': 'Landing assist', 'ifcs_core': 'IFCS core',
           'ifcs_command_behaviour': 'Command behaviour'}
POWER = {'powerplants':'Power plants', 'engines':'Engines', 'weapons':'Weapons power',
         'coolers':'Coolers', 'life_support':'Life support', 'gravity_generators':'Gravity',
         'sensors':'Sensors', 'shield_generators':'Shield generators', 'shield_emitters':'Shield emitters'}


def vehicle(frame):
    state = frame.get('state', {})
    flight = state.get('flight', {})
    instruments = flight.get('instruments', {})
    motion = {key: number(instruments.get(key)) for key in
              ('speed', 'radar_altitude', 'altitude', 'vertical_speed_indicator', 'heading')}
    motion['boost'] = number(lookup(instruments, 'boost.reserves'))
    for fuel in ('hydrogen_fuel', 'quantum_fuel'):
        value = number(instruments.get(fuel))
        motion[fuel] = value if value is not None and value >= 0 else None
    liberator = any(re.fullmatch(r'ANVL_Liberator(?:_\d+)?', state.get(k, ''), re.I)
                    for k in ('name', 'class_name'))
    hydrogen = motion['hydrogen_fuel']
    # The instrument is a fill fraction. Tank capacity only matters when
    # expressing the same reading as an absolute amount.
    fraction_valid = hydrogen is not None and hydrogen <= 1
    motion['hydrogen_percent'] = round(100 * hydrogen, 2) if fraction_valid else None
    motion['hydrogen_m'] = round(53.20 * hydrogen, 2) if liberator and fraction_valid else None
    configuration = {key: enum(lookup(flight, 'state.' + key), DEPLOYMENT)
                     for key in ('gear', 'vtol', 'transformation')}
    landed = lookup(flight, 'state.is_landed')
    configuration['landed'] = ('Landed' if landed else 'Airborne') if type(landed) is bool else None
    configuration['doors'] = enum(lookup(state, 'doors.open_state'), ['Open', 'Closed'])
    configuration['locks'] = enum(lookup(state, 'doors.lock_state'), ['Unlocked', 'Locked'])
    configuration['lights'] = switch(state, 'lights_enabled')
    return {'name': state.get('name') or None, 'class': state.get('class_name') or None,
            'manufacturer': state.get('manufacturer') or None,
            'type': enum(state.get('type'), ['Spaceship', 'Ground vehicle', 'Gravlev', 'Boat']),
            'flight': motion, 'configuration': configuration,
            'power': {label: switch(state, 'power.'+key) for key, label in POWER.items()},
            'annunciators': {label: enum(lookup(state, 'annunciators.'+key), ANNUNCIATOR)
                             for key, label in ANNUNCIATORS.items()},
            'assists': {label: switch(flight, 'settings.'+key+'.active') for key, label in ASSISTS.items()}}


def weapons(frame):
    index = frame.get('preset_index')
    return {'preset': frame.get('preset_name') or None,
            'preset_index': index if type(index) is int and 0 <= index <= 0xffffffff else None,
            'gimbals': switch(frame, 'settings.gimbals_locked'),
            'staggered': switch(frame, 'settings.staggered_fire'),
            'items': [{'name': item.get('name') or 'Weapon', 'ready': switch(item, 'ready_to_fire'),
                       'power': switch(item, 'is_powered'), 'selected': switch(item, 'contained_in_preset')}
                      for item in frame.get('weapons', [])]}


class Journal:
    def __init__(self):
        self.entries = deque(maxlen=200)
        self.previous = {}
        self.history = deque(maxlen=130)
        self.ship = None
        self.event_types = set()
        self.last_casualty = None

    def game_events(self, events):
        labels = ['Spawned', 'Died', 'Entered seat', 'Left seat', 'Respawned', 'Downed']
        current = {item.get('type') for item in events}
        for kind in sorted(k for k in current - self.event_types if type(k) is int):
            value = enum(kind, labels)
            if value:
                entry = {'time': datetime.now(timezone.utc).isoformat(), 'label': 'Player event',
                         'value': value, 'warning': value in ('Died', 'Downed')}
                self.entries.appendleft(entry)
                # A frame can contain both transitions. Prefer the confirmed death in that frame.
                if value == 'Died' or (value == 'Downed' and 2 not in current):
                    self.last_casualty = entry
        self.event_types = current

    def observe(self, key, value, warning=False):
        if value is None or value == self.previous.get(key):
            return
        first = key not in self.previous
        self.previous[key] = value
        if not first or warning or key in ('Player context', 'Feed', 'Vessel'):
            self.entries.appendleft({'time': datetime.now(timezone.utc).isoformat(),
                                     'label': key, 'value': value, 'warning': warning})

    def leave_ship(self):
        self.history.clear()
        self.ship = None
        self.previous = {k:v for k,v in self.previous.items()
                         if k in ('Player context','Feed') or k.startswith('Vital · ')}

    def observe_vehicle(self, data, now):
        identity = data['name'] or data['class']
        if identity and identity != self.ship:
            self.leave_ship()
            self.ship = identity
            self.observe('Vessel', identity)
        for group in ('configuration', 'power', 'annunciators', 'assists'):
            for key, value in data[group].items():
                warning = group == 'annunciators' and str(value).startswith(('Major','Minor'))
                self.observe(key.capitalize() if group == 'configuration' else key, value, warning)
        speed = data['flight']['speed']
        if not self.history or now - self.history[-1][0] >= .5:
            self.history.append((now, speed))
