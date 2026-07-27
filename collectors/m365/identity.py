"""Identity and device posture evidence.

Answers the questions an access-control audit actually asks: which MFA methods
did each person have registered, which devices were enrolled and compliant, and
which licences were assigned at the time of the backup.
"""

import json

from . import config
from .graph import graph_paginated_get


# name -> (root, endpoint, description)
IDENTITY_ENDPOINTS = {
    "authentication_methods": (
        config.GRAPH_BETA,
        "reports/authenticationMethods/userRegistrationDetails",
        "MFA registration state per user",
    ),
    "managed_devices": (
        config.GRAPH_ROOT,
        "deviceManagement/managedDevices",
        "Intune managed devices",
    ),
    "device_compliance_policies": (
        config.GRAPH_ROOT,
        "deviceManagement/deviceCompliancePolicies",
        "Intune compliance policies",
    ),
    "device_configurations": (
        config.GRAPH_ROOT,
        "deviceManagement/deviceConfigurations",
        "Intune configuration profiles",
    ),
    "app_role_assignments": (
        config.GRAPH_ROOT,
        "servicePrincipals?$expand=appRoleAssignedTo",
        "Application permission grants",
    ),
    "oauth2_permission_grants": (
        config.GRAPH_ROOT,
        "oauth2PermissionGrants",
        "Delegated permission consents",
    ),
    "intune_apps": (
        config.GRAPH_ROOT,
        "deviceAppManagement/mobileApps",
        "Intune managed applications",
    ),
    "intune_app_protection": (
        config.GRAPH_ROOT,
        "deviceAppManagement/managedAppPolicies",
        "Intune app protection policies",
    ),
    "service_announcements": (
        config.GRAPH_ROOT,
        "admin/serviceAnnouncement/messages",
        "Microsoft service change notices",
    ),
    "service_health": (
        config.GRAPH_ROOT,
        "admin/serviceAnnouncement/healthOverviews",
        "Microsoft service health per workload",
    ),
    "access_reviews": (
        config.GRAPH_BETA,
        "identityGovernance/accessReviews/definitions",
        "Access review definitions",
    ),
}


def collect_identity(headers, logger, workspace):
    """Write one JSON file per identity dataset. Returns (counts, warnings)."""
    counts = {}
    warnings = []

    identity_dir = workspace / "identity"
    identity_dir.mkdir(parents=True, exist_ok=True)

    for name, (root, endpoint, description) in IDENTITY_ENDPOINTS.items():
        logger.info(f"[+] Identity {name}")

        try:
            data = graph_paginated_get(f"{root}/{endpoint}", headers)
        except Exception as e:
            message = f"{description} unavailable: {e}"
            logger.warning(message)
            warnings.append(message)
            continue

        with open(identity_dir / f"{name}.json", "w") as f:
            json.dump(data, f, indent=2)

        counts[name] = len(data)
        logger.success(f"Collected {len(data)}")

    return counts, warnings
