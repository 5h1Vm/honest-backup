from pathlib import Path
import json


STATE_FILE = Path('state/notion/state.json')


def save_state(data):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(STATE_FILE, 'w') as f:
        json.dump(data, f, indent=2)
