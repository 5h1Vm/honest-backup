"""Records when each collector last finished.

It used to decide *whether* a collector should run, by comparing the time
since its last run against a per-collector interval. That is gone: the
schedule is now the set of clock times in backup.conf, and every enabled
collector runs at every one of them. What is left is the record of when
each one last completed, which the TUI shows as "last: 3 hours ago".
"""

import json

from datetime import datetime
from pathlib import Path


STATE_FILE = Path(
    "state/scheduler/state.json"
)


class Scheduler:

    def __init__(self):

        STATE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not STATE_FILE.exists():

            STATE_FILE.write_text(
                "{}"
            )

        try:
            self.state = json.loads(
                STATE_FILE.read_text()
            )
        except Exception:
            self.state = {}

    def save(self):

        STATE_FILE.write_text(

            json.dumps(

                self.state,

                indent=4,

            )

        )

    def mark_complete(
        self,
        collector,
    ):

        self.state[collector] = (

            datetime.now().isoformat()

        )

        self.save()
