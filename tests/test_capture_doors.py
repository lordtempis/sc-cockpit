import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from capture_doors import capture, delivered_fields


class DoorCaptureTests(unittest.TestCase):
    def test_single_vehicle_update_survives_following_omissions(self):
        frames = [
            {'actor': {}}, {'vehicle': {}},
            {'vehicle': {'state': {'doors': {'open_state': 0},
                                   'flight': {'state': {'transformation': 0}}}}},
            {'vehicle': {'state': {'doors': {'open_state': 1}}}},
            {'vehicle': {'state': {'doors': {}}}},
        ]
        def descriptor(mapping):
            return SimpleNamespace(fields=[SimpleNamespace(name=name, message_type=descriptor(child) if child else None)
                                           for name, child in mapping.items()])
        schema = descriptor({'vehicle': {'state': {'doors': {'open_state': None, 'lock_state': None},
                                                   'flight': {'state': {'transformation': None}}}}})
        with TemporaryDirectory() as directory, redirect_stdout(io.StringIO()), (
            patch.dict('sys.modules', grpc=SimpleNamespace(RpcError=Exception))
        ), patch('capture_doors.extract'), patch('capture_doors.stream_messages', return_value=(None, SimpleNamespace(DESCRIPTOR=schema))), (
            patch('capture_doors.game_frames', return_value=frames)
        ):
            capture(None, Path(directory))
            path, = Path(directory).glob('*.jsonl')
            report = json.loads(next(Path(directory).glob('field-delivery-*.json')).read_text())
            start, first, zero, opened, omitted, summary = map(json.loads, path.read_text().splitlines())
        self.assertEqual(start['event'], 'start')
        self.assertEqual(first['vehicle'], {})
        self.assertEqual(zero['vehicle'], {'doors': {'open_state': 0}, 'transformation': 0})
        self.assertEqual(opened['vehicle'], {'doors': {'open_state': 1}})
        self.assertEqual(omitted['vehicle'], {'doors': {}})
        self.assertEqual((summary['frames'], summary['vehicle_frames']), (5, 4))
        self.assertEqual((summary['door_position_frames'], summary['transformation_frames']), (2, 1))

        self.assertEqual(report['field_frames'], {'vehicle.state.doors.open_state': 2,
                                                 'vehicle.state.doors.lock_state': 0,
                                                 'vehicle.state.flight.state.transformation': 1})
        self.assertEqual(report['section_frames'], {'vehicle': 4, 'actor': 1})

    def test_repeated_items_count_once_per_frame_and_keep_false(self):
        frame = {'weapons': {'weapons': [{'ready_to_fire': False}, {'ready_to_fire': True}],
                             'settings': {'gimbals_locked': False}, 'countermeasures': []}}
        self.assertEqual(set(delivered_fields(frame)), {'weapons.weapons.ready_to_fire',
                                                       'weapons.settings.gimbals_locked'})
