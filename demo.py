"""Explicitly simulated telemetry frames for local dashboard previews."""
import math

SCENARIOS = ('Approach', 'Low fuel', 'On foot', 'EVA', 'Partial feed', 'Waiting for data', 'Stale feed', 'Disconnected')


def sample(scenario, elapsed):
    low = scenario == 'Low fuel'
    vitals = {key: {'current': value, 'max': 100} for key, value in
              [('health', 94), ('oxygen', 12 if low else 86), ('hunger', 71), ('thirst', 63)]}
    vitals.update(heart_rate=116 if low else 76, body_temperature={'current': 37, 'max': 42})
    context = {'On foot': 1, 'EVA': 2}.get(scenario, 3)
    actor = {'vitals': vitals, 'environment': {'breathable_atmosphere': context != 2},
             'state': {'locomotion': context, 'crew_role': 1 if context == 3 else 0,
                       'seat_name': 'Pilot' if context == 3 else ''}}
    if context != 3:
        return {'actor': actor}
    if scenario == 'Partial feed':
        return {'actor': {'vitals': {'oxygen': vitals['oxygen']}, 'state': {'locomotion': 3}},
                'vehicle': {'state': {'name': 'Training vessel', 'flight': {'instruments': {'speed': 0}}}}}
    landing = scenario == 'Approach'
    ship = {'name': 'Training vessel', 'class_name': 'Training freighter', 'manufacturer': 'Anvil',
            'type': 1, 'lights_enabled': landing, 'doors': {'open_state': 2, 'lock_state': 2},
            'annunciators': {'pwr': 1, 'qdrv': 1, 'shld': 5 if low else 1, 'wpn': 1},
            'power': {'powerplants': True, 'shield_generators': True, 'engines': True, 'weapons': True},
            'flight': {'state': {'gear': 3 if landing else 1, 'vtol': 3 if landing else 1,
                                  'is_landed': False},
                       'settings': {'ifcs_coupled': {'active': True}, 'ifcs_g_safe': {'active': True}},
                       'instruments': {'speed': round((35 if landing else 200) + 12*math.sin(elapsed/4), 1),
                                       'heading': 210, 'vertical_speed_indicator': -4 if landing else 0,
                                       'altitude': 500, 'radar_altitude': 14 if landing else 450,
                                       'hydrogen_fuel': .12 if low else .68, 'quantum_fuel': .95, 'boost': {'reserves': .82}}}}
    return {'actor': actor, 'vehicle': {'state': ship}, 'weapons': {'preset_name': 'Group 1',
            'weapons': [{'name': 'Training repeater', 'is_powered': True, 'ready_to_fire': True}]}}
