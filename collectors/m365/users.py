from . import config
from .graph import get_token, graph_paginated_get

def get_all_users():
    """
    Retrieve all user objects from the tenant.
    Returns a list of dicts with at least 'id' and 'userPrincipalName'.
    """
    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    # Request only needed fields to reduce payload
    url = f"{config.GRAPH_ROOT}/users?$select=id,userPrincipalName,displayName"
    users = graph_paginated_get(url, headers)
    return users