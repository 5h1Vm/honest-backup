from pathlib import Path

# Set at runtime by collectors.m365.collector.collect() to the current
# workspace directory. Modules must not use this until collect() has run.
WORKSPACE = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_FILE = PROJECT_ROOT / 'state' / 'm365' / 'state.json'

GRAPH_ROOT = 'https://graph.microsoft.com/v1.0'
GRAPH_BETA = 'https://graph.microsoft.com/beta'

# Office 365 Management Activity API (Unified Audit Log) — a different
# resource to Graph, so it needs its own token.
MANAGEMENT_ROOT = 'https://manage.office.com/api/v1.0'
