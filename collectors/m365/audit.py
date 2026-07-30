import json
import os

from . import config
from .graph import graph_paginated_get
from lib.jsonio import merge_records, write_json
from .state import load_state
from .state import save_state


AUDIT_ENDPOINTS = {

    "signins": {
        "endpoint": "auditLogs/signIns",
        "timestamp": "createdDateTime"
    },

    "directoryAudits": {
        "endpoint": "auditLogs/directoryAudits",
        "timestamp": "activityDateTime"
    },

    "provisioning": {
        "endpoint": "auditLogs/provisioning",
        "timestamp": "activityDateTime"
    }
}


def collect_audit(headers, logger, workspace):
    """
    Collect audit logs for all datasets.
    Returns a tuple: (audit_data, state_updates)
    where audit_data is a dict mapping dataset name to list of log items,
    and state_updates is a dict mapping dataset name to the latest timestamp (or None if no data).
    The caller is responsible for updating the state with state_updates and saving it.
    """
    audit_data = {}
    state_updates = {}

    # Load current state to get checkpoints
    state = load_state()
    if "audit" not in state:
        state["audit"] = {}

    audit_dir = (
        workspace /
        "audit"
    )
    audit_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    for dataset, cfg in AUDIT_ENDPOINTS.items():
        logger.info(f"[+] Audit {dataset}")

        url = (
            f"{config.GRAPH_ROOT}/"
            f"{cfg['endpoint']}"
        )

        checkpoint = state["audit"].get(dataset)

        if checkpoint:
            url += (
                f"?$filter="
                f"{cfg['timestamp']} "
                f"gt {checkpoint}"
            )

        data = graph_paginated_get(url, headers)

        # Checkpointed, so `data` holds only what appeared since the last run.
        # Written straight out it replaced the file, and an archive taken
        # after a quiet ten minutes kept ten minutes of audit history in place
        # of the days already collected. Merged, the file stays cumulative;
        # the per-run copy beside it still shows this run alone.
        outfile = audit_dir / f"{dataset}.json"
        write_json(outfile, merge_records(outfile, data, key_fields=("id",)))
        run_id = os.environ.get("HONESTBACKUP_RUN_ID")
        if run_id:
            write_json(audit_dir / f"{dataset}_run_{run_id}.json", data)

        count = len(data)
        logger.info(f"Collected {count}")

        audit_data[dataset] = data

        if data:
            timestamps = [
                item.get(cfg["timestamp"])
                for item in data
                if item.get(cfg["timestamp"])
            ]
            if timestamps:
                state_updates[dataset] = max(timestamps)
            else:
                state_updates[dataset] = None
        else:
            state_updates[dataset] = None

    # Note: we do not save state here; caller will do that
    return audit_data, state_updates