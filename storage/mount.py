import json
import subprocess

from pathlib import Path


def find_mountpoint(label: str):

    if not label:
        return None

    result = subprocess.run(
        [
            "lsblk",
            "-J",
            "-o",
            "LABEL,MOUNTPOINT"
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    def search(devices):

        for device in devices:

            if device.get("label") == label:

                mount = device.get("mountpoint")

                if mount:

                    return Path(mount)

            for child in device.get(
                "children",
                []
            ):

                found = search([child])

                if found:

                    return found

        return None

    return search(
        data["blockdevices"]
    )
