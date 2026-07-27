import json
from pathlib import Path


def build_manifest(date_dir, backup_id=None):

    date_dir = Path(date_dir)

    files = []
    total_size = 0

    for f in date_dir.rglob('*'):
        if f.is_file():
            size = f.stat().st_size
            total_size += size

            files.append({
                'path': str(f.relative_to(date_dir)),
                'size': size
            })

    manifest = {
        'date': date_dir.name,
        'file_count': len(files),
        'total_size': total_size,
        'files': files
    }

    if backup_id:
        outfile = date_dir / f"{backup_id}.manifest.json"
    else:
        outfile = date_dir / 'manifest.json'


    with open(outfile, 'w') as f:
        json.dump(manifest, f, indent=4)

    return outfile
