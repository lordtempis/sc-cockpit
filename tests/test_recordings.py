import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cockpit import State, STALE_AFTER
from diagnostics import installation


class SessionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = State(recording_directory=self.root)
        self.files = self.state.recordings

    def test_delivery_uses_field_presence_and_field_age(self):
        s = self.state
        s.diagnostics.schema = {'actor.environment.breathable_atmosphere', 'turret.light_amplification'}
        s.ingest({'actor': {'environment': {'breathable_atmosphere': False}, 'vitals': {'heart_rate': 0}},
                  'force_feedback': {'gunfire': False}}, now=20)
        s.ingest({'actor': {'vitals': {'heart_rate': 2}}}, now=21)
        snap = s.snapshot(now=22)
        d = snap['diagnostics']
        self.assertEqual(d['fields']['actor.air'], ['Unbreathable', '—', 2, 'stale · last known'])
        self.assertEqual(d['fields']['actor.heart'], [2, 'native · unverified', 1, 'fresh'])
        self.assertEqual(d['fields']['vehicle.flight.speed'][3], 'never received')
        self.assertEqual(d['inputs']['force_feedback.gunfire'], ['received', 'unmapped', False, 2])
        self.assertEqual(d['inputs']['actor.environment.breathable_atmosphere'], ['received', 'mapped', False, 2])
        self.assertEqual(d['inputs']['turret.light_amplification'][:2], ['unseen on wire', 'unmapped'])
        self.assertEqual(d['inputs']['vehicle.state.doors.open_state'][0], 'unknown')
        self.assertEqual(d['packet_age'], 1)
        self.assertEqual(s.snapshot(now=27)['diagnostics']['fields']['actor.heart'][3], 'stale · last known')
        s.clear('Game closed', now=28)
        self.assertEqual(s.snapshot(now=29)['diagnostics']['packet_age'], 8)

    def test_save_reopen_seek_and_leave_live_state_untouched(self):
        s = self.state
        s.diagnostics.metadata = {'build':'123', 'channel':'PTU'}
        self.files.start(s, 10)
        s.ingest({'actor': {'state': {'locomotion': 3}}, 'vehicle': {'state': {
            'flight': {'instruments': {'speed': 0}}, 'power': {'engines': False}}}}, now=11)
        self.files.bookmark('Before outage', 12)
        s.clear('Game unavailable', now=20)
        s.ingest({'actor': {'state': {'locomotion': 2}}}, now=22)
        name = self.files.stop(s, now=24)
        offline = State(recording_directory=self.root)
        self.assertEqual(offline.recordings.files(), [name])
        def seek(at):
            result = offline.recordings.replay(name, at, STALE_AFTER)
            self.assertEqual(result['mode'], 'replay')
            self.assertEqual(result['diagnostics']['metadata'], s.diagnostics.metadata)
            return result
        self.assertEqual(seek(0)['dashboard']['feed'], 'waiting')
        self.assertEqual(seek(7)['dashboard']['feed'], 'stale')
        self.assertEqual(seek(7)['frame'], {})
        self.assertEqual(seek(7)['diagnostics']['packet_age'], 6)
        self.assertEqual(seek(10)['dashboard']['feed'], 'disconnected')
        eva = seek(13)
        self.assertEqual(eva['dashboard']['feed'], 'live')
        self.assertEqual(eva['dashboard']['sections']['actor']['context'], 'EVA')
        self.assertNotIn('vehicle', eva['dashboard']['sections'])
        restored = seek(2)
        self.assertEqual(restored['replay']['bookmarks'], [(2, 'Before outage')])
        self.assertEqual(restored['diagnostics']['fields']['vehicle.flight.speed'], [0, 'native · unverified', 1, 'fresh'])
        self.assertEqual(restored['frame']['vehicle']['state']['power']['engines'], False)
        self.assertEqual(offline.snapshot()['dashboard']['feed'], 'waiting')
        self.assertEqual(seek(100)['replay']['position'],14)
        with self.assertRaises(ValueError):
            seek(float('nan'))
        with self.assertRaises(ValueError):
            offline.recordings.replay('../bad', 0, STALE_AFTER)

    def test_empty_packet_updates_connection_independently_of_ship_fields(self):
        self.state.ingest({}, now=4)
        sample = self.state.snapshot(now=6)
        self.assertEqual(sample['dashboard']['feed'], 'live')
        self.assertEqual(sample['diagnostics']['packet_age'], 2)
        self.assertEqual(self.state.snapshot(now=9)['dashboard']['feed'], 'stale')

    def test_manifest_identification(self):
        game = self.root / 'LIVE'
        game.mkdir()
        (game / 'build_manifest.id').write_text(json.dumps({'Data': {'Version':'1.2','RequestedP4ChangeNum':'123'}}))
        self.assertEqual(installation(game), {'channel':'LIVE','build':'1.2 · 123'})

        (game / 'build_manifest.id').unlink()
        (game / 'Game.log').write_text('BackupNameAttachment=" Build(456)"\nFileVersion: 2.7.3')
        self.assertEqual(installation(game)['build'], '2.7.3 · 456')
