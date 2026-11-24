"""Audit LIVE telemetry field delivery and record door and transformation samples."""
from collections import Counter
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from cockpit import game_frames, stream_messages
from extract_schema import extract
from diagnostics import schema_fields, delivered_fields


def vehicle_reading(frame):
    if 'vehicle' not in frame:
        return None
    vehicle = frame['vehicle']
    state = vehicle.get('state', {})
    flight = state.get('flight', {}).get('state', {})
    reading = {key: state[key] for key in ('name', 'class_name', 'doors') if key in state}
    if 'transformation' in flight:
        reading['transformation'] = flight['transformation']
    return reading


def capture(game, directory):
    import grpc

    directory.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory() as temp:
        schema = Path(temp) / 'live.desc'
        extract(game, schema)
        request, response = stream_messages(schema)

    path = directory / datetime.now().strftime('doors-%Y%m%d-%H%M%S-%f.jsonl')
    field_frames = dict.fromkeys(schema_fields(response.DESCRIPTOR), 0)
    section_frames = Counter()
    frames = vehicles = positions = transformations = 0
    reason = 'Stream ended early'
    with path.open('x', encoding='utf-8', buffering=1) as output:
        def record(**fields):
            timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            print(json.dumps(dict(time=timestamp, **fields)), file=output)

        record(event='start', timeout_seconds=60)
        print(f'Saving to {path}\nWaiting for vehicle telemetry...', flush=True)
        try:
            for frame in game_frames(request, response, timeout=60):
                frames += 1
                if frames == 1:
                    print('Receiving LIVE telemetry. Counting all fields for this capture.', flush=True)
                section_frames.update(frame.keys())
                for field in set(delivered_fields(frame)):
                    field_frames[field] += 1
                reading = vehicle_reading(frame)
                if reading is None:
                    continue
                vehicles += 1
                record(frame=frames, vehicle=reading)
                if vehicles == 1:
                    print('Recording vehicle frames. Open and close the doors now.', flush=True)
                positions += 'open_state' in reading.get('doors', {})
                transformations += 'transformation' in reading
        except grpc.RpcError as exc:
            reason = 'Capture time elapsed' if exc.code() == grpc.StatusCode.DEADLINE_EXCEEDED else f'Connection ended ({exc.code().name})'
        except KeyboardInterrupt:
            reason = 'Stopped by user'
        record(event='end', reason=reason, frames=frames, vehicle_frames=vehicles,
               door_position_frames=positions, transformation_frames=transformations)
    print(f'{reason}. Vehicle frames: {vehicles}. Door position: {positions}. Transformation: {transformations}.')
    if not vehicles:
        print('No vehicle frames received. Check that LIVE telemetry is enabled and you are seated, then retry.')
    report_path = path.with_name(path.name.replace('doors-', 'field-delivery-')).with_suffix('.json')
    report = {'capture': path.name, 'reason': reason, 'frames': frames,
              'section_frames': dict(section_frames), 'field_frames': field_frames}
    with report_path.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    observed = sum(count > 0 for count in field_frames.values())
    print(f'Saved {path}')
    print(f'{observed} of {len(field_frames)} schema fields received. Field counts: {report_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    capture(args.game, args.directory)
