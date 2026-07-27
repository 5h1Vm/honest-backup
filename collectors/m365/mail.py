import json
import requests
from . import config
from .graph import get_token, graph_delta_get
from .state import get_user_resource_state, set_user_resource_state

def collect_messages_for_user(user_id, headers, logger=None, delta_link=None):
    """
    Collect new/changed messages for a given user using delta query.
    Returns a tuple: (list_of_messages, updated_state_dict_for_this_user_resource)
    The updated_state_dict is what should be merged back into the main state under
    state['users'][user_id]['mail'].
    If logger is provided, it will be used for logging.
    If delta_link is provided, it will be used as the delta link for the request;
    otherwise, the delta link is read from the state.
    """
    def log(level, msg):
        if logger:
            getattr(logger, level)(msg)
        else:
            print(f"[{level.upper()}] {msg}")

    log('info', f"Starting mail delta query for user {user_id}")

    # Build the endpoint URL
    url = f"{config.GRAPH_ROOT}/users/{user_id}/messages"

    # Perform the delta query
    try:
        messages, new_delta_link = graph_delta_get(url, headers, delta_link)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            log('warning', f"User {user_id} has no mailbox or mailbox not found (404). Skipping.")
        elif e.response.status_code == 403:
            log('warning', f"Insufficient permissions to access mailbox for user {user_id} (403). Skipping.")
        else:
            log('error', f"HTTP error during mail delta query for user {user_id}: {e}")
        # Return empty list and no state change on error
        return [], {}
    except Exception as e:
        log('error', f"Unexpected error during mail delta query for user {user_id}: {e}")
        return [], {}

    log('info', f"Retrieved {len(messages)} messages for user {user_id}")

    # Prepare the state update for this resource
    state_update = {}
    if new_delta_link:
        state_update['deltaLink'] = new_delta_link

    return messages, state_update