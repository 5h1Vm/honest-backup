import json
import os
from .config import STATE_FILE


def load_state():
    """
    Load the state file. If it doesn't exist, return a default structure.
    Expected structure:
    {
        "audit": {},
        "users": {
            "user-id": {
                "resource-name": {
                    "deltaLink": "string",
                    ... other metadata if needed
                }
            }
        }
    }
    """
    default_state = {"audit": {}, "users": {}}
    if not os.path.exists(STATE_FILE):
        return default_state
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
            # Ensure required keys exist
            if "audit" not in data:
                data["audit"] = {}
            if "users" not in data:
                data["users"] = {}
            return data
    except Exception:
        return default_state


def save_state(state):
    """Save the state dictionary to the state file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def get_user_resource_state(state, user_id, resource_name):
    """
    Get the state dict for a given user and resource.
    Returns None if not found.
    """
    return state.get("users", {}).get(user_id, {}).get(resource_name)


def set_user_resource_state(state, user_id, resource_name, data):
    """
    Set the state for a given user and resource.
    """
    if "users" not in state:
        state["users"] = {}
    if user_id not in state["users"]:
        state["users"][user_id] = {}
    state["users"][user_id][resource_name] = data