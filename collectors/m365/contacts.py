import json
import requests
from . import config
from .graph import get_token, graph_delta_get
from .state import get_user_resource_state, set_user_resource_state

def collect_contacts(user_id, headers, logger=None, delta_link=None):
    """
    Collect new/changed contacts for a given user using delta query.
    Returns a tuple: (list_of_contacts, updated_state_dict_for_this_user_resource)
    The updated_state_dict is what should be merged back into the main state under
    state['users'][user_id]['contacts'].
    If logger is provided, it will be used for logging.
    If delta_link is provided, it will be used; otherwise, the function will attempt
    to read the current delta link from the state.
    """
    def log(level, msg):
        if logger:
            getattr(logger, level)(msg)
        else:
            print(f"[{level.upper()}] {msg}")

    log('info', f"Starting contacts delta query for user {user_id}")

    # Build the endpoint URL
    url = f"{config.GRAPH_ROOT}/users/{user_id}/contacts"

    # If delta_link not provided, try to get it from state
    if delta_link is None:
        from .state import load_state
        state = load_state()
        current_state = get_user_resource_state(state, user_id, 'contacts')
        if current_state and isinstance(current_state, dict):
            delta_link = current_state.get('deltaLink')

    # Perform the delta query
    try:
        contacts, new_delta_link = graph_delta_get(url, headers, delta_link)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            log('warning', f"User {user_id} has no contacts or contacts not found (404). Skipping.")
        elif e.response.status_code == 403:
            log('warning', f"Insufficient permissions to access contacts for user {user_id} (403). Skipping.")
        else:
            log('error', f"HTTP error during contacts delta query for user {user_id}: {e}")
        # Return empty list and no state change on error
        return [], {}
    except Exception as e:
        log('error', f"Unexpected error during contacts delta query for user {user_id}: {e}")
        return [], {}

    log('info', f"Retrieved {len(contacts)} contacts for user {user_id}")

    # Prepare the state update for this resource
    state_update = {}
    if new_delta_link:
        state_update['deltaLink'] = new_delta_link

    return contacts, state_update