#!/usr/bin/env python3
"""A firmware *set* is a reviewed, named, digest-pinned bundle of the six blobs.

The device-observed sizes baked into the C table describe exactly one handset.
When a second official firmware appears (a different OTA, a different regional
build), the gate must be able to say "this is the 12.5.1 set, and here is why we
believe it" instead of silently relaxing a size check. That statement is a JSON
manifest, reviewed like any other source file:

  python3 tools/polaris_firmware_set.py --record DIR --name NAME \
      --archive FILE --member images/vendor.img --note '...' -o manifest.json
  python3 tools/polaris_firmware_set.py --check DIR -m manifest.json

`--record` reads real files and records their size and SHA-256. `--check` does
the opposite and refuses on any difference, so a manifest can never describe
bytes that are not on disk. Nothing here opens a device or starts firmware.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = 1

# Order matches enum pg_blob_id / the ADT memory-map names in polaris_gpu.c.
BLOBS = (
    ('GPU-SQE', 'a630_sqe.fw'),
    ('GPU-GMU', 'a630_gmu.bin'),
    ('GPU-ZAP-MDT', 'a630_zap.mdt'),
    ('GPU-ZAP-B00', 'a630_zap.b00'),
    ('GPU-ZAP-B01', 'a630_zap.b01'),
    ('GPU-ZAP-B02', 'a630_zap.b02'),
)
FILENAMES = dict(BLOBS)
BLOB_ORDER = [blob for blob, _ in BLOBS]


class FirmwareSetError(Exception):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex_digest(value):
    return (isinstance(value, str) and len(value) == 64 and
            all(c in '0123456789abcdef' for c in value))


def validate(manifest):
    """Structural validation. Returns the files list in canonical blob order."""
    if not isinstance(manifest, dict):
        raise FirmwareSetError('manifest must be a JSON object')
    if manifest.get('schema') != SCHEMA:
        raise FirmwareSetError(f'unsupported manifest schema: {manifest.get("schema")!r}')
    name = manifest.get('name')
    if not isinstance(name, str) or not name.strip():
        raise FirmwareSetError('manifest needs a non-empty name')
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) != len(BLOBS):
        raise FirmwareSetError(f'manifest must list exactly {len(BLOBS)} files')

    by_blob = {}
    seen_names = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise FirmwareSetError('each file entry must be an object')
        blob = entry.get('blob')
        if blob not in FILENAMES:
            raise FirmwareSetError(f'unknown blob id: {blob!r}')
        if blob in by_blob:
            raise FirmwareSetError(f'duplicate blob id: {blob}')
        filename = entry.get('filename')
        if filename != FILENAMES[blob]:
            raise FirmwareSetError(
                f'{blob}: filename must be {FILENAMES[blob]}, got {filename!r}')
        if filename in seen_names:
            raise FirmwareSetError(f'duplicate filename: {filename}')
        seen_names.add(filename)
        size = entry.get('bytes')
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise FirmwareSetError(f'{blob}: bytes must be a positive integer')
        digest = entry.get('sha256')
        if not _is_hex_digest(digest):
            raise FirmwareSetError(f'{blob}: sha256 must be 64 lowercase hex digits')
        if digest == '0' * 64:
            raise FirmwareSetError(f'{blob}: zero digest proves nothing')
        by_blob[blob] = {'blob': blob, 'filename': filename,
                         'bytes': size, 'sha256': digest}

    missing = [blob for blob in BLOB_ORDER if blob not in by_blob]
    if missing:
        raise FirmwareSetError(f'missing blobs: {", ".join(missing)}')
    source = manifest.get('source')
    if source is not None and not isinstance(source, dict):
        raise FirmwareSetError('source must be an object when present')
    return [by_blob[blob] for blob in BLOB_ORDER]


def load(path):
    try:
        manifest = json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise FirmwareSetError(f'{path}: not valid JSON ({exc})') from exc
    entries = validate(manifest)
    return manifest, entries


def verify(folder, entries):
    """Re-hash FOLDER against ENTRIES. Returns [(blob, filename, data)]."""
    folder = Path(folder)
    out = []
    for entry in entries:
        path = folder / entry['filename']
        if not path.is_file() or path.is_symlink():
            raise FirmwareSetError(f'missing or non-regular firmware: {entry["filename"]}')
        data = path.read_bytes()
        if len(data) != entry['bytes']:
            raise FirmwareSetError(
                f'{entry["filename"]}: {len(data)} bytes, set pins {entry["bytes"]}')
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry['sha256']:
            raise FirmwareSetError(
                f'{entry["filename"]}: sha256 {digest} does not match the set')
        out.append((entry['blob'], entry['filename'], data))
    return out


def record(folder, name, archive=None, member=None, note=None, path_in_image=None):
    """Describe the files in FOLDER as a manifest dict."""
    folder = Path(folder)
    files = []
    for blob, filename in BLOBS:
        path = folder / filename
        if not path.is_file() or path.is_symlink():
            raise FirmwareSetError(f'missing or non-regular firmware: {filename}')
        files.append({'blob': blob, 'filename': filename,
                      'bytes': path.stat().st_size,
                      'sha256': sha256_file(path)})
    manifest = {'schema': SCHEMA, 'name': name, 'files': files}
    source = {}
    if archive:
        source['archive'] = archive
    if member:
        source['member'] = member
    if path_in_image:
        source['path'] = path_in_image
    if source:
        manifest['source'] = source
    if note:
        manifest['note'] = note
    validate(manifest)  # never emit something we would refuse to read back
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', type=Path, nargs='?',
                        help='directory holding the six firmware files')
    parser.add_argument('-m', '--manifest', type=Path,
                        help='manifest to check against (with --check)')
    parser.add_argument('--record', metavar='NAME',
                        help='write a manifest for FOLDER under this name')
    parser.add_argument('--check', action='store_true',
                        help='verify FOLDER against --manifest')
    parser.add_argument('--archive', help='provenance: source archive name')
    parser.add_argument('--member', help='provenance: archive member (e.g. images/vendor.img)')
    parser.add_argument('--path-in-image', help='provenance: path inside that image')
    parser.add_argument('--note', help='provenance: free-form note')
    parser.add_argument('-o', '--output', type=Path, help='write manifest here (default stdout)')
    args = parser.parse_args(argv)

    if not args.folder:
        parser.error('a firmware directory is required')
    try:
        if args.record:
            manifest = record(args.folder, args.record, args.archive, args.member,
                              args.note, args.path_in_image)
            text = json.dumps(manifest, indent=2, sort_keys=False) + '\n'
            if args.output:
                # LF even on Windows: a manifest is reviewed in a diff.
                args.output.write_text(text, encoding='utf-8', newline='\n')
                print(f'{args.output}: recorded {manifest["name"]} '
                      f'({sum(f["bytes"] for f in manifest["files"])} bytes in '
                      f'{len(manifest["files"])} files)')
            else:
                sys.stdout.write(text)
            return 0
        if args.check:
            if not args.manifest:
                parser.error('--check needs --manifest')
            manifest, entries = load(args.manifest)
            blobs = verify(args.folder, entries)
            for blob, filename, data in blobs:
                print(f'{blob}: {len(data)} bytes sha256={hashlib.sha256(data).hexdigest()}')
            print(f'set verified: {manifest["name"]}')
            return 0
    except FirmwareSetError as exc:
        print(f'REFUSE: {exc}', file=sys.stderr)
        return 1
    parser.error('nothing to do: pass --record or --check')


if __name__ == '__main__':
    sys.exit(main())
