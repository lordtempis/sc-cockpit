import unittest

from cockpit import STALE_AFTER, State
from demo import sample


class DashboardTests(unittest.TestCase):
    def test_wait_receive_stale_disconnect_and_reconnect(self):
        state = State()
        self.assertEqual(state.snapshot(now=0)['dashboard']['feed'], 'waiting')
        state.ingest(sample('Approach', 0), now=1)
        state.ingest({'actor': sample('Approach', 1)['actor']}, now=5)
        mixed = state.snapshot(now=7)
        self.assertEqual(mixed['dashboard']['feed'], 'live')
        self.assertGreaterEqual(mixed['ages']['vehicle'], STALE_AFTER)
        self.assertIsNotNone(mixed['dashboard']['sections']['vehicle']['flight']['speed'])
        stale = state.snapshot(now=12)
        self.assertEqual(stale['dashboard']['feed'], 'stale')
        self.assertFalse(stale['active'])
        state.clear('Lost connection')
        disconnected = state.snapshot(now=13)['dashboard']
        self.assertEqual((disconnected['feed'],disconnected['sections']), ('disconnected',{}))
        state.ingest(sample('EVA',1),now=14)
        self.assertEqual(set(state.snapshot(now=14)['dashboard']['sections']), {'actor'})

    def test_player_detail_native_readings_on_foot(self):
        state = State()
        frame = sample('On foot', 0)
        frame['actor']['vitals'].update(pressure=0, gravity=0.55, intoxication_level=0)
        frame['actor']['environment'].update(ambient_radiation_rate=0, external_temperature=-8)
        state.ingest(frame, now=0)
        sections = state.snapshot(now=6)['dashboard']['sections']
        actor = sections['actor']
        self.assertEqual([actor[k] for k in ('pressure','gravity','intoxication','radiation','external_temperature')],
                         [0, 0.55, 0, 0, -8])
        self.assertNotIn('vehicle', sections)
        state.ingest(sample('EVA', 0), now=7)
        self.assertIsNone(state.snapshot(now=7)['dashboard']['sections']['actor']['pressure'])

    def test_on_foot_balance_preserves_int64_precision_zero_and_absence(self):
        state = State()
        for value, expected in [('12345', '12345'), ('9223372036854775807', '9223372036854775807'),
                                (0, '0'), (None, None)]:
            actor = {'state': {'locomotion': 1}}
            if value is not None:
                actor['state']['uec_balance'] = value
            state.ingest({'actor': actor}, now=0)
            self.assertEqual(state.snapshot(now=6)['dashboard']['sections']['actor']['uec_balance'], expected)

    def test_gunner_preset_switch_and_missing_membership(self):
        state = State()
        frame = {'actor': {'state': {'locomotion': 3, 'crew_role': 2}}, 'weapons': {
            'preset_index': 2, 'settings': {'staggered_fire': True},
            'weapons': [{'name': 'Cannon', 'contained_in_preset': True}]}}
        state.ingest(frame, now=0)
        frame['weapons'].update(preset_index=0, weapons=[
            {'name': 'Cannon', 'contained_in_preset': False}, {'name': 'Repeater'}])
        state.ingest(frame, now=1)
        state.ingest(frame, now=2)
        snap = state.snapshot(now=2)['dashboard']
        panel = snap['sections']['weapons']
        self.assertEqual(panel['preset_index'], 0)
        self.assertEqual(panel['staggered'], 'On')
        self.assertEqual([item['selected'] for item in panel['items']], ['Off', None])
        self.assertEqual([e['value'] for e in snap['journal'] if e['label']=='Weapon preset index'], [0])
        state.ingest({'weapons': {}}, now=3)
        self.assertIsNone(state.snapshot(now=3)['dashboard']['sections']['weapons']['preset_index'])

    def test_reported_fields_remember_presence_between_polls(self):
        state = State()
        state.ingest({'vehicle': {'state': {'power': {'engines': True}}}}, now=0)
        for power, expected in [({}, None), ({'engines': False}, 'Off'), ({}, None), ({'engines': True}, 'On')]:
            state.ingest({'vehicle': {'state': {'power': power}}}, now=1)
            snap = state.snapshot(now=1)['dashboard']
            self.assertEqual(snap['reported']['vehicle']['power'], {'Engines': None})
            self.assertEqual(snap['sections']['vehicle']['power']['Engines'], expected)
        state.clear('Disconnected')
        self.assertEqual(state.snapshot(now=2)['dashboard']['reported']['vehicle']['power'], {'Engines': None})
        self.assertEqual(State(demo=True).snapshot()['dashboard']['reported'], {})

    def test_remembered_weapon_names_follow_current_ship(self):
        state = State()
        for name in ('First ship', 'Second ship'):
            state.ingest({'vehicle': {'state': {'name': name}},
                          'weapons': {'preset_index': 0, 'weapons': [{'name': name + ' gun', 'is_powered': False}]}}, now=0)
            self.assertIn('weapons', state.snapshot(now=0)['frame'])
            state.ingest({'weapons': {}}, now=1)
            known = state.snapshot(now=1)['dashboard']['reported']['weapons']
            self.assertEqual(set(known['items']), {name + ' gun'})
            self.assertIn('preset_index', known)
        state.ingest(sample('On foot', 0), now=2)
        self.assertNotIn('items', state.snapshot(now=2)['dashboard']['reported']['weapons'])

    def test_vehicle_door_position_and_lock_are_independent(self):
        reader = State()
        for doors, expected in [
            ({'open_state': 1, 'lock_state': 2}, ('Open', 'Locked')),
            ({'open_state': 2, 'lock_state': 1}, ('Closed', 'Unlocked')),
            ({'lock_state': 2}, (None, 'Locked')),
            ({'open_state': 2}, ('Closed', None)),
            ({'open_state': 0, 'lock_state': 0}, (None, None)),
            ({}, (None, None)),
        ]:
            with self.subTest(doors=doors):
                reader.ingest({'vehicle': {'state': {'doors': doors}}}, now=0)
                config = reader.snapshot(now=0)['dashboard']['sections']['vehicle']['configuration']
                self.assertEqual((config['doors'], config['locks']), expected)

    def test_partial_zero_false_and_missing(self):
        state = State()
        data = sample('Partial feed', 0)
        data['actor']['environment'] = {'breathable_atmosphere': False}
        state.ingest(data, now=1)
        group = state.snapshot(now=1)['dashboard']['sections']
        self.assertEqual(group['vehicle']['flight']['speed'],0)
        self.assertIsNone(group['vehicle']['flight']['radar_altitude'])
        self.assertEqual(group['actor']['air'],'Unbreathable')
        self.assertIsNone(group['actor']['health'])

    def test_switching_ships_preserves_hydrogen_percent_without_reusing_capacity(self):
        state = State()
        frame = sample('Approach',0)
        ship = frame['vehicle']['state']
        ship['name']='ANVL_Liberator_927921008571'
        ship['flight']['instruments'].update(hydrogen_fuel=.99870664, altitude=3.4028235e38)
        state.ingest(frame,now=0)
        motion=state.snapshot(now=0)['dashboard']['sections']['vehicle']['flight']
        self.assertEqual((motion['hydrogen_m'],motion['hydrogen_percent']),(53.13,99.87))
        self.assertIsNone(motion['altitude'])
        ship['name']='Other ship'
        state.ingest(frame,now=1)
        other = state.snapshot(now=1)['dashboard']['sections']['vehicle']['flight']
        self.assertEqual(other['hydrogen_percent'],99.87)
        self.assertIsNone(other['hydrogen_m'])
        instruments = ship['flight']['instruments']
        for value, expected in [(0,0), (1,100), (1.2,None), (None,None)]:
            instruments['hydrogen_fuel'] = value
            state.ingest(frame, now=2)
            self.assertEqual(state.snapshot(now=2)['dashboard']['sections']['vehicle']['flight']['hydrogen_percent'], expected)

    def test_no_ship_after_leaving_seat(self):
        for scenario in ('On foot','EVA'):
            with self.subTest(scenario=scenario):
                state=State()
                state.ingest(sample('Approach',0),now=0)
                state.ingest(sample(scenario,1),now=1)
                dashboard=state.snapshot(now=1)['dashboard']
                self.assertEqual(set(dashboard['sections']),{'actor'})
                self.assertEqual(dashboard['sections']['actor']['context'],scenario)
                self.assertEqual(dashboard['history'],[])

    def test_transitions_and_warnings_are_recorded_once(self):
        state=State(demo=True)
        for i in range(20):
            frame=sample('Approach' if i<10 else 'Low fuel',i)
            if i>=10:
                frame['events']={'events':[{'type':6}]}
            state.ingest(frame,now=i)
            state.snapshot(now=i)
        entries=state.snapshot(now=19)['dashboard']['journal']
        for key in ['Player event','Shield warning','Demo hydrogen','Vital · Oxygen']:
            self.assertEqual(sum(1 for e in entries if e['label']==key and e['warning']),1)
        self.assertTrue(all(e['time'].endswith('+00:00') for e in entries))
        self.assertLessEqual(len(entries),12)

    def test_history_expires_and_keeps_zero(self):
        state=State()
        for i in range(91):
            state.ingest(sample('Partial feed',i),now=i)
        history=state.snapshot(now=90)['dashboard']['history']
        self.assertEqual(history[0],[60,0])
        self.assertEqual(history[-1],[0,0])
        self.assertEqual(len([row for row in history if row[0]<=30]),31)
