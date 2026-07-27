from pathlib import Path
from datetime import datetime
import hashlib
import json


class ManifestBuilder:

    def __init__(self, notion_dir: Path):

        self.root = Path(notion_dir)

    def _sha256(self, path):

        h = hashlib.sha256()

        with open(path, "rb") as fp:
            while True:
                data = fp.read(1024 * 1024)
                if not data:
                    break
                h.update(data)

        return h.hexdigest()

    def build(self):

        manifest = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "files": []
        }

        for file in sorted(self.root.rglob("*")):

            if not file.is_file():
                continue

            if file.name == "manifest.json":
                continue

            manifest["files"].append({
                "file": str(file.relative_to(self.root)),
                "size": file.stat().st_size,
                "sha256": self._sha256(file)
            })

        outfile = self.root / "manifest.json"

        with open(outfile, "w") as fp:
            json.dump(
                manifest,
                fp,
                indent=2
            )

        print()
        print("=" * 60)
        print("Manifest")
        print("=" * 60)
        print(f"[+] Files : {len(manifest['files'])}")
        print(f"[+] Saved : {outfile}")

        return outfile