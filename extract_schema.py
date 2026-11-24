"""Best-effort extraction of embedded protobuf descriptors, without changing game files."""
import argparse
from pathlib import Path
import re


def varint(data, start):
    value = 0
    for shift in range(0, 70, 7):
        if start >= len(data):
            raise ValueError('Truncated varint')
        byte = data[start]
        start += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, start
    raise ValueError('Invalid varint')


def descriptor_end(data, start):
    """Walk complete top-level FileDescriptorProto fields; stop at binary padding."""
    pos = start
    seen_name = False
    # Every current FileDescriptorProto field is wire type 2 except public/weak
    # dependency indices, which may also be unpacked varints. Unknown fields
    # are intentionally rejected rather than importing arbitrary binary bytes.
    known = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15}
    while pos < min(len(data), start + 4 * 1024 * 1024):
        try:
            tag, after = varint(data, pos)
            field, wire = tag >> 3, tag & 7
            if field not in known or (field == 1 and seen_name):
                break
            if wire == 2:
                length, after = varint(data, after)
                end = after + length
                if end > len(data) or length > 4 * 1024 * 1024:
                    break
            elif wire == 0 and field in (10, 11, 14):
                _, end = varint(data, after)
            else:
                break
        except ValueError:
            break
        if field == 1:
            seen_name = True
        pos = end
    return pos


def extract(executable, output):
    from google.protobuf import descriptor_pb2
    data = executable.read_bytes()
    found = {}
    # Descriptor names in this API are short ASCII paths with a one-byte length.
    pattern = rb'\x0a[\x01-\x7f][A-Za-z0-9_./-]+\.proto'
    for match in re.finditer(pattern, data):
        start = match.start()
        if data[start + 1] != len(match.group()) - 2:
            continue
        end = descriptor_end(data, start)
        candidate = descriptor_pb2.FileDescriptorProto()
        try:
            candidate.ParseFromString(data[start:end])
        except Exception:
            continue
        if candidate.name.endswith('.proto') and candidate.package and candidate.syntax in ('proto2', 'proto3', ''):
            found[candidate.name] = candidate
    roots = {name for name, item in found.items()
             if item.package == 'sc.game.telemetry.v1'}
    if not roots:
        raise ValueError('No telemetry descriptors found. This build may use a different binary format.')
    needed = set()
    def include(name):
        if name in needed:
            return
        if name not in found:
            raise ValueError(f'Missing descriptor dependency: {name}')
        needed.add(name)
        for dep in found[name].dependency:
            include(dep)
    for name in roots:
        include(name)
    from google.protobuf import descriptor_pool
    pool = descriptor_pool.DescriptorPool()
    pending = [found[name] for name in sorted(needed)]
    result = descriptor_pb2.FileDescriptorSet()
    while pending:
        remaining = []
        for item in pending:
            try:
                pool.Add(item)
            except (TypeError, ValueError):
                remaining.append(item)
            else:
                result.file.add().CopyFrom(item)
        if len(remaining) == len(pending):
            raise ValueError('Extracted descriptors did not validate; no output written.')
        pending = remaining
    pool.FindServiceByName('sc.game.telemetry.v1.GameTelemetryService').FindMethodByName('TelemetryStream')
    with output.open('xb') as target:
        target.write(result.SerializeToString())
    print(f'Validated {len(result.file)} descriptors. Saved to {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path, help='Path to your LIVE Bin64/StarCitizen.exe')
    parser.add_argument('--output', type=Path, default=Path('telemetry.desc'))
    args = parser.parse_args()
    try:
        extract(args.executable, args.output)
    except Exception as exc:
        parser.exit(1, f'Extraction failed: {exc}\n')
