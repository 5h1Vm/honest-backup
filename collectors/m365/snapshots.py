"""Tenant-wide directory and policy snapshots.

These are point-in-time records of how the tenant was configured: who existed,
who held which role, which apps were registered, and which access policies were
in force. They are the backbone of any configuration audit.
"""

import json

from . import config
from .graph import graph_paginated_get


# name -> (endpoint, needs_beta)
SNAPSHOTS = {
    # --- directory inventory ---
    "users": ("users", False),
    "groups": ("groups", False),
    "applications": ("applications", False),
    "servicePrincipals": ("servicePrincipals", False),
    "directoryRoles": ("directoryRoles", False),
    "administrativeUnits": ("directory/administrativeUnits", False),
    "domains": ("domains", False),
    "organization": ("organization", False),
    "subscribedSkus": ("subscribedSkus", False),

    # --- access control ---
    "conditionalAccessPolicies": ("identity/conditionalAccess/policies", False),
    "namedLocations": ("identity/conditionalAccess/namedLocations", False),
    "authorizationPolicy": ("policies/authorizationPolicy", False),
    "authenticationMethodsPolicy": ("policies/authenticationMethodsPolicy", False),
    "authenticationStrengthPolicies": (
        "policies/authenticationStrengthPolicies", False
    ),
    "adminConsentRequestPolicy": ("policies/adminConsentRequestPolicy", False),
    "identitySecurityDefaults": (
        "policies/identitySecurityDefaultsEnforcementPolicy", False
    ),
    "defaultAppManagementPolicy": (
        "policies/defaultAppManagementPolicy", False
    ),
    "tokenLifetimePolicies": ("policies/tokenLifetimePolicies", False),
    "crossTenantAccessPartners": (
        "policies/crossTenantAccessPolicy/partners", False
    ),
    "identityProviders": ("identity/identityProviders", False),

    # --- RBAC (the modern role model, richer than directoryRoles) ---
    "roleAssignments": ("roleManagement/directory/roleAssignments", False),
    "roleDefinitions": ("roleManagement/directory/roleDefinitions", False),

    # --- deleted objects: who and what was removed, still recoverable ---
    "deletedUsers": (
        "directory/deletedItems/microsoft.graph.user", False
    ),
    "deletedGroups": (
        "directory/deletedItems/microsoft.graph.group", False
    ),
    "deletedApplications": (
        "directory/deletedItems/microsoft.graph.application", False
    ),

    # --- devices ---
    "devices": ("devices", False),
    "deviceRegistrationPolicy": ("policies/deviceRegistrationPolicy", True),
}

# Selected so a deleted account can still be identified years later.
SELECT = {
    "users": (
        "id,userPrincipalName,displayName,mail,accountEnabled,userType,"
        "createdDateTime,jobTitle,department,assignedLicenses,"
        "onPremisesSyncEnabled,signInActivity"
    ),
}


def collect_snapshots(headers, logger, workspace):
    """Write one JSON file per snapshot dataset. Returns (counts, warnings)."""
    counts = {}
    warnings = []

    snapshot_dir = workspace / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for name, (endpoint, use_beta) in SNAPSHOTS.items():
        logger.info(f"[+] Snapshot {name}")

        root = config.GRAPH_BETA if use_beta else config.GRAPH_ROOT
        url = f"{root}/{endpoint}"
        if name in SELECT:
            url += f"?$select={SELECT[name]}"

        try:
            data = graph_paginated_get(url, headers)
        except Exception as e:
            # signInActivity needs Entra ID P1; retry without it rather than
            # losing the whole user inventory.
            if name in SELECT and "signInActivity" in SELECT[name]:
                try:
                    fallback = SELECT[name].replace(",signInActivity", "")
                    data = graph_paginated_get(
                        f"{root}/{endpoint}?$select={fallback}", headers
                    )
                    warnings.append(
                        f"{name}: collected without signInActivity ({e})"
                    )
                except Exception as e2:
                    warnings.append(f"{name} failed: {e2}")
                    logger.warning(f"{name} failed: {e2}")
                    continue
            else:
                warnings.append(f"{name} failed: {e}")
                logger.warning(f"{name} failed: {e}")
                continue

        outfile = snapshot_dir / f"{name}.json"
        with open(outfile, "w") as f:
            json.dump(data, f, indent=2)

        counts[name] = len(data)
        logger.success(f"Collected {len(data)}")

    # Role membership has to be resolved per role — the role list alone does
    # not tell you who held admin rights.
    try:
        logger.info("[+] Snapshot roleMembers")
        roles = graph_paginated_get(
            f"{config.GRAPH_ROOT}/directoryRoles", headers
        )
        memberships = []
        for role in roles:
            role_id = role.get("id")
            if not role_id:
                continue
            members = graph_paginated_get(
                f"{config.GRAPH_ROOT}/directoryRoles/{role_id}/members", headers
            )
            memberships.append({
                "roleId": role_id,
                "displayName": role.get("displayName"),
                "roleTemplateId": role.get("roleTemplateId"),
                "members": members,
            })
        with open(snapshot_dir / "roleMembers.json", "w") as f:
            json.dump(memberships, f, indent=2)
        counts["roleMembers"] = sum(len(m["members"]) for m in memberships)
        logger.success(f"Collected {counts['roleMembers']}")
    except Exception as e:
        warnings.append(f"roleMembers failed: {e}")
        logger.warning(f"roleMembers failed: {e}")

    # Group membership, same reasoning.
    try:
        logger.info("[+] Snapshot groupMembers")
        groups = graph_paginated_get(
            f"{config.GRAPH_ROOT}/groups?$select=id,displayName", headers
        )
        group_members = []
        for group in groups:
            gid = group.get("id")
            if not gid:
                continue
            try:
                members = graph_paginated_get(
                    f"{config.GRAPH_ROOT}/groups/{gid}/members"
                    f"?$select=id,userPrincipalName,displayName",
                    headers,
                )
            except Exception:
                members = []
            group_members.append({
                "groupId": gid,
                "displayName": group.get("displayName"),
                "members": members,
            })
        with open(snapshot_dir / "groupMembers.json", "w") as f:
            json.dump(group_members, f, indent=2)
        counts["groupMembers"] = sum(len(g["members"]) for g in group_members)
        logger.success(f"Collected {counts['groupMembers']}")
    except Exception as e:
        warnings.append(f"groupMembers failed: {e}")
        logger.warning(f"groupMembers failed: {e}")

    return counts, warnings
