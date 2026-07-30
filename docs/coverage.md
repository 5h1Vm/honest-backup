# What gets backed up, and why some things don't

A reference for what each platform collects, and — just as importantly —
what is deliberately excluded and why. Nothing listed under "not backed
up" is a fault in the backup system; each has a specific, named reason.

Exact record counts and file totals change with every run and aren't
tracked here — use **My backups → check it** in the TUI, or `--check-cloud`,
for the current state of the archive.

---

## Microsoft 365

**Directory and configuration**
Users, groups, group membership, directory roles, role membership, RBAC role
assignments and definitions, applications, service principals, domains,
organisation, licences, administrative units, devices.

**Access control**
Conditional Access policies, named locations, authorisation policy,
authentication methods policy, authentication strength policies, admin
consent policy, security defaults state, app management policy, token
lifetime policies, cross-tenant access partners, identity providers.

**Deleted objects — recoverable**
Deleted users, deleted groups, deleted applications. This answers "who was
removed, and when".

**Security posture**
Secure Score history, Secure Score control profiles, risk detections,
Defender alerts and incidents.

**Identity and devices**
MFA registration per user, Intune managed devices, compliance policies,
configuration profiles, Intune apps, app protection policies, application
permission grants, delegated consents, access reviews.

**Content**
SharePoint document libraries (full file content), mail with attachments,
calendar and contacts — all incremental after the first run. Mail activity,
mailbox usage, client usage, service usage reports.

**Operations**
Service announcements, service health per workload.

**Logs**
Unified Audit Log, Entra sign-in logs, Entra directory audits, Entra
provisioning logs, Defender alerts, Defender incidents, Defender advanced
hunting tables.

Microsoft keeps the Unified Audit Log for 180 days and sign-in logs for 30.
This archive keeps them for as long as retention is configured to — see
[§ Retention](#retention) below.

## Cloudflare

| Area | Contents |
|---|---|
| Zone | All zones, DNS records, zone settings, DNSSEC, rulesets, certificates, load balancers |
| Security | Firewall rules, filters, WAF packages |
| Zero Trust | Access applications, policies, groups, users, service tokens, identity providers, device posture, devices, gateway rules and lists, tunnels |
| Account | Members, roles, subscriptions, Workers scripts |
| Logs | Account audit log (accumulating, de-duplicated), Access authentication requests |

## Notion

Full workspace export (Markdown and HTML), page and database inventory,
metadata, database schemas, all database rows, workspace statistics.

---

## Not backed up — and why

### Blocked by licence

| Not collected | Requires |
|---|---|
| Risky user scoring | Entra ID P2 |
| Per-message mail trace | Defender for Office 365 P2 |
| Email hunting table | Defender for Office 365 P2 |
| Device event and sign-in hunting | Defender for Endpoint, devices onboarded |
| Privileged Identity Management | Entra ID P2 |
| Teams messages | a Teams licence in the tenant |
| Cloudflare HTTP, firewall, Gateway logs | Cloudflare Enterprise with Logpush |

A tenant without one of these licences will show the corresponding item as
skipped in the run report — that's expected, not a failure, and it's
reported separately from real errors so it never reads as a critical issue.

### Blocked by a Microsoft-side fault

**Personal OneDrive** can return HTTP 503 from the Graph Sites API rather
than a permission error. When that happens, SharePoint is collected via
SharePoint's own REST API with certificate authentication as a fallback;
personal OneDrive currently has no equivalent fallback. If this recurs, a
Microsoft support ticket for the Graph Sites endpoint is the right next
step — it isn't something this tool can work around further.

### Not applicable

An account with no Exchange licence has no mailbox, calendar or contacts to
back up. Reported as skipped, not failed.

---

## How the backup works

```
collect → manifest → tar → zstd compress → age encrypt → SHA-256 → store
```

Every archive is encrypted and hash-verified before it's written anywhere.

### Retention

Three storage tiers, each with its own window — set from the TUI's
**Scheduling** screen, not by hand:

| Where | Current setting |
|---|---|
| This server | 14 days |
| Backblaze B2 | forever |
| Office drive | forever — never deleted |

`0` on the local vault means "delete once a copy exists elsewhere"; the
office drive is append-only regardless of setting, since it exists
specifically to survive the cloud copy being lost.

### Incremental backups

The first run downloads everything. Every run after that fetches only what
changed — mail, calendar, contacts and file libraries all use API
checkpoints or content hashes, so an unchanged file costs nothing to skip
on the next run.

Backups run on a schedule without anyone logged in, or on demand from the
TUI's **Back up now**.
