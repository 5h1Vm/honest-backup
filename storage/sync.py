from pathlib import Path

from .config import (
    REPOSITORY_PATH,
    BACKBLAZE_ENABLED,
    BACKBLAZE_REMOTE,
    USB_ENABLED,
    USB_LABEL,
    USB_BACKUP_PATH,
)

from .mount import find_mountpoint
from .rclone import Rclone


class SyncEngine:

    def __init__(self):

        self.repository = Path(REPOSITORY_PATH)

    def sync(self):

        usb_result = self.sync_usb()
        backblaze_result = self.sync_backblaze()

        return {
            "repository": {
                "path": str(self.repository),
                "exists": self.repository.exists(),
                "status": self.repository.exists(),
            },

            "usb": usb_result,

            "backblaze": backblaze_result,
        }

    def sync_usb(self):

        if not USB_ENABLED:
            return {
                "enabled": False,
                "connected": False,
                "destination": None,
                "status": False,
            }

        mount = find_mountpoint(USB_LABEL)

        if mount is None:
            print("[Sync] USB not connected")
            return {
                "enabled": True,
                "connected": False,
                "destination": None,
                "status": False,
            }

        if USB_BACKUP_PATH:
            destination = mount / USB_BACKUP_PATH
        else:
            destination = mount

        print(f"[Sync] Repository -> USB ({destination})")

        # copy, not sync: local retention must never delete the USB copy.
        Rclone.copy(
            str(self.repository),
            str(destination),
        )

        return {
            "enabled": True,
            "connected": True,
            "destination": str(destination),
            "status": True,
        }

    def sync_backblaze(self):

        if not BACKBLAZE_ENABLED:
            return {
                "enabled": False,
                "remote": None,
                "status": False,
            }

        print("[Sync] Repository -> Backblaze")

        # copy, not sync: local retention must never delete the cloud copy.
        Rclone.copy(
            str(self.repository),
            BACKBLAZE_REMOTE,
        )

        return {
            "enabled": True,
            "remote": BACKBLAZE_REMOTE,
            "status": True,
        }