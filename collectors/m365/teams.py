"""Microsoft Teams — teams, channels, and channel message content.

The old version recorded team and channel names only. This also pulls the
messages in each channel and their replies, which is what makes Teams data
useful as evidence.

Note on permissions: reading channel message *content* requires
ChannelMessage.Read.All, which Microsoft classifies as a protected API. If it
has not been approved for this tenant the message calls fail and are recorded
as warnings — the team and channel inventory is still collected.
"""

import json

from . import config
from .graph import graph_paginated_get
from lib.jsonio import write_json


def collect_teams(headers, logger, workspace, settings):
    """Collect all teams, channels and (where permitted) messages.

    Returns (counts, warnings).
    """
    counts = {}
    warnings = []

    teams_dir = workspace / "teams"
    teams_dir.mkdir(parents=True, exist_ok=True)

    try:
        teams = graph_paginated_get(f"{config.GRAPH_ROOT}/teams", headers)
    except Exception as e:
        message = f"Teams unavailable: {e}"
        logger.warning(message)
        return counts, [message]

    write_json(teams_dir / "teams.json", teams)
    counts["teams"] = len(teams)

    if not teams:
        logger.info("No teams in this tenant")
        return counts, warnings

    total_channels = 0
    total_messages = 0

    for team in teams:
        team_id = team.get("id")
        team_name = team.get("displayName") or team_id
        if not team_id:
            continue

        logger.info(f"[+] Team {team_name}")

        try:
            channels = graph_paginated_get(
                f"{config.GRAPH_ROOT}/teams/{team_id}/channels", headers
            )
        except Exception as e:
            warnings.append(f"team {team_name}: could not list channels: {e}")
            continue

        total_channels += len(channels)
        safe_team = str(team_name).replace("/", "_")[:80]
        team_dir = teams_dir / safe_team
        team_dir.mkdir(parents=True, exist_ok=True)

        write_json(team_dir / "channels.json", channels)

        # Team membership is its own access-control record.
        try:
            members = graph_paginated_get(
                f"{config.GRAPH_ROOT}/teams/{team_id}/members", headers
            )
            write_json(team_dir / "members.json", members)
        except Exception as e:
            warnings.append(f"team {team_name}: members unavailable: {e}")

        if not settings["download_messages"]:
            continue

        for channel in channels:
            channel_id = channel.get("id")
            channel_name = channel.get("displayName") or channel_id
            if not channel_id:
                continue

            try:
                messages = graph_paginated_get(
                    f"{config.GRAPH_ROOT}/teams/{team_id}/channels/"
                    f"{channel_id}/messages",
                    headers,
                )
            except Exception as e:
                warnings.append(
                    f"{team_name}/{channel_name}: messages unavailable "
                    f"({e}) — needs ChannelMessage.Read.All"
                )
                continue

            # Replies hang off each message and are not returned inline.
            for message in messages:
                message_id = message.get("id")
                if not message_id:
                    continue
                try:
                    replies = graph_paginated_get(
                        f"{config.GRAPH_ROOT}/teams/{team_id}/channels/"
                        f"{channel_id}/messages/{message_id}/replies",
                        headers,
                    )
                    if replies:
                        message["_replies"] = replies
                except Exception:
                    pass

            safe_channel = str(channel_name).replace("/", "_")[:80]
            write_json(team_dir / f"messages_{safe_channel}.json", messages)

            total_messages += len(messages)
            logger.success(f"{channel_name}: {len(messages)} messages")

    counts["channels"] = total_channels
    counts["messages"] = total_messages
    return counts, warnings


def collect_teams_for_user(user_id, headers, logger=None, delta_link=None):
    """Retained for backwards compatibility; tenant-wide collection is handled
    by collect_teams()."""
    return [], {}
