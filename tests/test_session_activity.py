from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from cockpit import State
from kill_monitor import DeathMonitor
from session_activity import latest_performance


STAMP = '<2026-11-21T06:21:25.039Z>'
LOGIN = '<AccountLoginCharacterStatus_Character> Character: geid 42 - name Pilot - state ACTIVE [Team]'
HUD = '<SHUDEvent_OnNotification> Added notification "'
SHIP = "<Vehicle Destroyed> Vehicle 'Target_99' [99] class 'Target' (owner player [0], driver [0]) destroyed: reason [Combat] level [0] -> [2] flags [0] killer 'Pilot' [{}]"


def hud(text, objective=''):
    return HUD + text + ': " [1] to queue. New queue size: 1, MissionId: [a1], ObjectiveId: [' + objective + '] [Team]'


class ActivityTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'Game.log'
        self.monitor = DeathMonitor(self.path)
        self.state = State()

    def append(self, *lines):
        with self.path.open('a', encoding='utf-8') as stream:
            for line in lines:
                stream.write(STAMP + ' [Notice] ' + line + '\n')
        with patch('kill_monitor.time.sleep', side_effect=InterruptedError), self.assertRaises(InterruptedError):
            self.monitor.watch(self.state)
        return self.state.snapshot()['dashboard']

    def test_replay_character_id_dedup_and_rotation(self):
        self.append(LOGIN, SHIP.format(0), SHIP.format(41), '<Chat> '+SHIP.format(42))
        self.assertEqual(self.state.activity['kills'], 0)
        data = self.append(SHIP.format(42), SHIP.format(42))
        self.assertEqual(data['activity']['kills'], 1)
        self.assertEqual(len(data['journal']), 1)
        self.assertIsNone(data['kill_monitor']['last_death'])
        self.path.rename(self.path.with_suffix('.old'))
        reset = self.append(hud('Entered Monitored Space'))
        self.assertIsNone(reset['activity']['kills'])
        self.assertFalse(any(e.get('category') == 'Combat' for e in reset['journal']))

    def test_mission_lifecycle_multiline_and_zone_transitions(self):
        data = self.append(LOGIN, hud('Contract Accepted: Survey'), hud('New Objective: Land','1'),
                           hud('Entering Armistice Zone - Combat Prohibited'), hud('Entering Armistice Zone - Combat Prohibited'))
        self.assertEqual(data['activity']['missions'], [{'name':'Survey','objectives':['Land']}])
        self.assertEqual(sum(e['label']=='Armistice' for e in data['journal']), 1)
        self.append(HUD + 'Objective Complete: Land')
        with self.path.open('a') as f:
            f.write(STAMP + ' : " [1] to queue. New queue size: 1, MissionId: [a1], ObjectiveId: [1] [Team]\n')
        data = self.append('<MissionEnded> Received MissionEnded push message for: mission_id a1 - mission_state MISSION_STATE_COMPLETED [Team_GameServices][Missions]',
                           hud('Contract Complete: Survey'))
        self.assertEqual(data['activity']['missions'], [])
        for label in ['Contract complete','Objective Complete']:
            self.assertEqual(sum(e['label']==label for e in data['journal']),1)

    def test_travel_is_per_vessel_and_requires_arrival_record(self):
        prefix = '[ItemNavigation][CL][2] | NOT AUTH | '
        suffix = ' [Team_CGP4][QuantumTravel]'
        def nav(tag, ship, msg):
            return '<'+tag+'> '+prefix+'Ship_'+ship+'['+ship+']|CSCItemNavigation::Handler|'+msg+suffix
        selected = nav('Player Selected Quantum Target - Local','7','Player has selected point A as their destination, routing locally')
        route = nav('Calculate Route','7','Projected Start Location is B for route to destination A')
        arrived = 'Quantum Drive Arrived - Arrived at Final Destination'
        msg = 'Quantum Drive has arrived at final destination'
        data = self.append(selected,route,nav(arrived,'8',msg),nav(arrived,'7',msg))
        travel = [e for e in data['journal'] if e['category']=='Travel']
        self.assertEqual(len(travel),4)
        arrivals = [e['value'] for e in travel if e['label']=='Quantum arrival']
        self.assertEqual(arrivals,['Ship_7 · Last reported target: A','Ship_8'])

    def test_saved_csv_excludes_invalid_gpu_and_uses_report_timestamp(self):
        self.path.with_name('level_stats_1.csv').write_text(
            'Debug skip;Run;Frames;Entities;;;CPU/Bucket;Avg;<16ms;GPU;Avg;\n'
            ';;;Min;Avg;Max;;;;;;\n;;42;4;6;9;Whole Frame;60 FPS (16.7 ms);20%;INVALID;0 FPS;\n')
        report = latest_performance(self.path.parent)
        self.assertEqual(report['rows']['Peak entities'],'9')
        self.assertEqual(report['rows']['Whole Frame under 16 ms'],'20%')
        self.assertIsNone(report['rows']['GPU average'])
        self.assertTrue(report['time'].endswith('+00:00'))
