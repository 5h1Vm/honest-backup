import json

from . import config
from .graph import graph_paginated_get
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

        outfile = audit_dir / f"{dataset}.json"
        with open(outfile, "w") as f:
            json.dump(data, f, indent=2)

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