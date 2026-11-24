"""Build the single-file EXE or portable x64 ZIP from a CPython 3.12 Windows install archive and installed Windows wheels."""
import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path, PurePosixPath
import os
import subprocess
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'packaging'
APPLICATION = ('cockpit.py', 'dashboard.py', 'demo.py', 'extract_schema.py', 'kill_monitor.py', 'session_activity.py',
               'diagnostics.py', 'recordings.py', 'capture_doors.py',
               'web/index.html', 'web/style.css', 'web/app.js')
RUNTIME = {'python.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'}
SKIP_LIB = {'site-packages', '__pycache__', 'test', 'tests', 'ensurepip', 'idlelib', 'tkinter', 'turtledemo', 'venv'}
FOLDER = 'SC Cockpit/'


def runtime_member(name):
    parts = PurePosixPath(name).parts
    if name in RUNTIME:
        return True
    if len(parts) == 2 and parts[0] == 'DLLs':
        return name.endswith(('.pyd', '.dll')) and parts[1] not in {'_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll'}
    return len(parts) > 1 and parts[0] == 'Lib' and not SKIP_LIB.intersection(parts) and name.endswith('.py')


def include_wheels(output, site, prefix):
    for package in ('grpcio', 'protobuf', 'typing_extensions'):
        metadata, = site.glob(package + '-*.dist-info')
        for name, fingerprint, _ in csv.reader((metadata / 'RECORD').read_text(encoding='utf8').splitlines()):
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            content = (site / name).read_bytes()
            if fingerprint:
                algorithm, expected = fingerprint.split('=', 1)
                actual = base64.urlsafe_b64encode(hashlib.new(algorithm, content).digest()).decode().rstrip('=')
                if actual != expected:
                    raise ValueError('Wheel integrity check failed for ' + name)
            output.writestr(prefix + 'runtime/Lib/site-packages/' + name, content)


def build(args):
    framework = args.framework
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temp:
        stage = Path(temp)
        exe = stage / 'SC Cockpit.exe'
        compiler = ['dotnet', str(args.csc)] if args.csc and args.csc.suffix == '.dll' else [str(args.csc or framework / 'csc.exe')]
        single = args.output.suffix.lower() == '.exe'
        prefix = '' if single else FOLDER
        refs = ['mscorlib.dll', 'System.dll', 'System.Windows.Forms.dll', 'System.Drawing.dll']
        flags = []
        if single:
            refs.extend(['System.IO.Compression.dll', 'System.IO.Compression.FileSystem.dll'])
            flags.append('/define:SINGLE_FILE')

        def compile_launcher():
            subprocess.run(compiler + ['/nologo', '/noconfig', '/nostdlib+', '/optimize+', '/target:winexe', '/platform:x64',
                           '/win32icon:' + str(PACK / 'cockpit.ico'),
                           '/resource:' + str(PACK / 'cockpit.ico') + ',SC.Cockpit.Icon', '/out:' + str(exe)] + ['/reference:' + str(framework / dll) for dll in refs] +
                           flags + [str(PACK / 'Launcher.cs'), str(PACK / 'TrayContext.cs')], check=True)

        archive = stage / 'release.zip'
        with ZipFile(archive, 'w', ZIP_DEFLATED) as output, ZipFile(args.runtime_zip) as runtime:
            if not RUNTIME.issubset(runtime.namelist()):
                raise ValueError('Expected a Windows CPython 3.12 install archive with runtime DLLs.')
            for member in runtime.namelist():
                if runtime_member(member):
                    output.writestr(prefix + 'runtime/' + member, runtime.read(member))
            # Resolve modules relative to the bundled DLL, independent of the working directory and registry.
            output.writestr(prefix + 'runtime/python312._pth', '.\r\nDLLs\r\nLib\r\nLib/site-packages\r\n../app\r\n')
            include_wheels(output, args.site_packages, prefix)
            for member in APPLICATION:
                output.write(ROOT / member, prefix + 'app/' + member)
            if not single:
                compile_launcher()
                output.write(exe, FOLDER + exe.name)
                output.write(PACK / 'START-HERE.txt', FOLDER + 'START HERE.txt')
                output.writestr(FOLDER + 'Try Demo.cmd', '@echo off\r\n"%~dp0SC Cockpit.exe" --demo\r\n')
        with ZipFile(archive) as release:
            names = set(release.namelist())
            for relative in ('runtime/Lib/site-packages/grpc/_cython/cygrpc.cp312-win_amd64.pyd',
                             'runtime/Lib/site-packages/google/_upb/_message.pyd', 'runtime/DLLs/_socket.pyd', 'runtime/DLLs/_sqlite3.pyd', 'runtime/DLLs/sqlite3.dll'):
                if prefix + relative not in names:
                    raise ValueError('Missing Windows x64 dependency: ' + relative)
            if release.testzip():
                raise ValueError('Archive integrity check failed')
        if single:
            flags.append('/resource:' + str(archive) + ',SC.Cockpit.Payload')
            compile_launcher()
            exe.replace(args.output)
        else:
            archive.replace(args.output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-zip', type=Path, required=True)
    parser.add_argument('--site-packages', type=Path, required=True)
    parser.add_argument('--framework', type=Path, default=Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Microsoft.NET/Framework64/v4.0.30319')
    parser.add_argument('--csc', type=Path, help='Optional Roslyn compiler executable or SDK csc.dll')
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'SC-Cockpit-Windows-x64.exe',
                        help='Single-file .exe (default) or portable .zip')
    build(parser.parse_args())
