from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cockpit import State
from kill_monitor import DeathMonitor


STAMP = '<2026-11-21T01:01:02.350Z> [Notice] '
LOGIN = STAMP + '<AccountLoginCharacterStatus_Character> Character: geid 102 - accountId 100 - name TestPilot - state ACTIVE [Team][Login]\n'


def death(victim='TestPilot', actor_id='102', attacker='OtherPilot'):
    return (STAMP + f"<Actor Death> CActor::Kill: '{victim}' [{actor_id}] in zone 'Ship', "
            f"killed by '{attacker}' [103] using 'laser' [Class unknown] with damage type 'Energy'\n")


class DeathMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'Game.log'
        self.monitor = DeathMonitor(self.path)

    def append(self,text):
        with self.path.open('ab') as f:
            f.write(text.encode())

    def test_replay_then_follow_without_duplicate_or_other_victims(self):
        self.append(LOGIN + death('SomeoneElse','104') + death() + death())
        state=State()
        with patch('kill_monitor.time.sleep', side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                self.monitor.watch(state)
        dashboard = state.snapshot()['dashboard']
        self.assertEqual(len(dashboard['journal']), 1)
        latest = dashboard['kill_monitor']['last_death']
        self.assertEqual(latest['time'],'2026-11-21T01:01:02.350000+00:00')
        self.assertEqual(latest['value'],'Weapon: laser · Damage: Energy')
        self.assertTrue(latest['warning'])
        self.assertEqual(latest,dashboard['journal'][0])
        self.assertEqual(self.monitor.poll(),([],False))

    def test_telemetry_only_death_is_visible_and_distinct_from_an_older_logged_death(self):
        state = State()
        state.ingest({'events': {'events': [{'type': 2}]}})
        view = state.snapshot()['dashboard']['kill_monitor']
        self.assertEqual(view['last_telemetry_report']['value'], 'Died')
        self.assertIsNone(view['last_death'])
        self.append(LOGIN + death())
        with patch('kill_monitor.time.sleep', side_effect=InterruptedError), self.assertRaises(InterruptedError):
            self.monitor.watch(state)
        after = state.snapshot()['dashboard']['kill_monitor']
        self.assertEqual(after['last_telemetry_report'], view['last_telemetry_report'])
        self.assertNotIn('OtherPilot', str(after['last_death']))
        self.assertNotIn('OtherPilot', str(after['last_telemetry_report']))
        state.ingest({'events': {'events': [{'type': 2}]}})
        self.assertEqual(state.snapshot()['dashboard']['kill_monitor'], after)

    def test_downed_is_distinct_and_death_takes_precedence_within_one_frame(self):
        state = State()
        for kinds, expected in [([6], 'Downed'), ([2, 6], 'Died'), ([], 'Died'), ([6, 2], 'Died')]:
            state.ingest({'events': {'events': [{'type': kind} for kind in kinds]}})
            report = state.snapshot()['dashboard']['kill_monitor']['last_telemetry_report']
            self.assertEqual(report['value'], expected)
            self.assertNotIn('attacker', report)

    def test_partial_line_and_manual_identity(self):
        self.monitor = DeathMonitor(self.path, 'TestPilot')
        line=death(attacker='ANVL_Arrow_123')
        self.append(line[:-8])
        self.assertEqual(self.monitor.poll()[0],[])
        self.append(line[-8:])
        self.assertEqual(self.monitor.poll()[0][0]['value'], 'Weapon: laser · Damage: Energy')

    def test_truncate_rotate_reset_identity(self):
        self.append(LOGIN + death())
        self.assertEqual(len(self.monitor.poll()[0]),1)
        self.path.write_text(death())
        self.assertEqual(self.monitor.poll()[0],[])
        self.assertIsNone(self.monitor.handle)
        self.path.rename(self.path.with_suffix('.old'))
        self.append(LOGIN.replace('TestPilot','NewPilot').replace('geid 102','geid 105') +
                    death('NewPilot','105','unknown'))
        self.assertEqual(self.monitor.poll()[0][0]['value'], 'Weapon: laser · Damage: Energy')

    def test_only_structured_records_and_actor_identity(self):
        self.monitor.parse(LOGIN)
        self.assertIsNone(self.monitor.parse(STAMP + '<Chat> ' + death()))
        self.assertIsNone(self.monitor.parse(death('TestPilot2','222')))
        record = self.monitor.parse(death('AlternateActorName','102','NPC_Pirate'))
        self.assertEqual(record['value'], 'Weapon: laser · Damage: Energy')
        self.assertNotIn('PvP',record['label'])

    def test_death_without_weapon_or_damage(self):
        self.monitor.parse(LOGIN)
        record = self.monitor.parse(death().split(' using ')[0])
        self.assertEqual(record['value'], 'Died')
