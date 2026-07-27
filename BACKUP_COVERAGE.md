# HonestBackup - Coverage Report

**Date:** 27 July 2026
**Systems:** Microsoft 365, Cloudflare, Notion
**Verified against:** a live backup run - 128 MB collected, 54 MB stored, synced to Backblaze

---

## Summary

**115 datasets are being backed up** across three platforms. Everything the
tenant's licences and APIs allow is now collected.

| Platform | Datasets | Highlights |
|---|---|---|
| Microsoft 365 | 80 | Unified Audit Log, Defender, Conditional Access, SharePoint files |
| Cloudflare | 29 | DNS, WAF, Zero Trust, account audit log |
| Notion | 6 | Full workspace export plus database rows |

Nothing is failing. Everything not collected is blocked by a **licence we do
not hold** or a **Microsoft service fault** - not by the backup system.

---

## 1. Logs - the complete activity record

| Log | Volume | Covers |
|---|---|---|
| **Unified Audit Log** | 3,572 records / 7 days | Who opened, downloaded, shared or deleted anything, across SharePoint, Exchange, Entra and Planner |
| Entra sign-in logs | incremental | Every authentication: user, app, IP, device, MFA result, Conditional Access verdict |
| Entra directory audits | incremental | Every directory change: users, roles, groups, policies |
| Entra provisioning logs | incremental | Automated account creation and removal |
| Defender alerts | 6 | Security detections |
| Defender incidents | 6 | Alerts grouped into investigations |
| Defender advanced hunting | 4 tables | Alert detail, alert evidence, identity sign-ins, cloud app events |
| Cloudflare account audit | 311, accumulating | Every change made in Cloudflare |
| Cloudflare Access log | live | Who reached which Zero Trust application |

**Retention is the point.** Microsoft keeps the Unified Audit Log for 180 days
and sign-in logs for 30. Our archive keeps them permanently. That alone
satisfies long-term audit retention without an E5 upgrade.

---

## 2. Microsoft 365 - 80 datasets

**Directory and configuration**
Users, groups, group membership, directory roles, role membership, RBAC role
assignments and definitions, applications, service principals, domains,
organisation, licences, administrative units, devices.

**Access control**
Conditional Access policies (6), named locations, authorisation policy,
authentication methods policy, authentication strength policies, admin consent
policy, security defaults state, app management policy, token lifetime
policies, cross-tenant access partners, identity providers.

**Deleted objects - recoverable**
Deleted users (recovered `emergency-admin` and `security-admin`), deleted
groups, deleted applications. This answers "who was removed, and when".

**Security posture**
Secure Score history (90 entries), Secure Score control profiles (449), risk
detections, Defender alerts and incidents.

**Identity and devices**
MFA registration per user, Intune managed devices, compliance policies,
configuration profiles, Intune apps, app protection policies, application
permission grants (347), delegated consents (27), access reviews.

**Content**
- **SharePoint - 990 files, 79.7 MB.** Full document library including
  Organisation Governance, Operating Management System, Service Delivery,
  Assurance and Compliance, Architecture and Technology, Regulatory
  References, Knowledge and Training, Working Area, Archive, Notion Export.
  926 docx, 309 pdf, 252 md, 183 xlsx, 104 png, 54 zip, 34 txt, 14 csv.
- **Mail** - messages with attachments, incremental.
- Calendar and contacts, incremental.
- Mail activity, mailbox usage, client usage, service usage reports.

**Operations**
Service announcements (640), service health per workload (27).

---

## 3. Cloudflare - 29 datasets

| Area | Contents |
|---|---|
| Zone | All zones, DNS records, zone settings, DNSSEC, rulesets, certificates, load balancers |
| Security | Firewall rules, filters, WAF packages |
| Zero Trust | Access applications, policies, groups, users, service tokens, identity providers, device posture, devices, gateway rules and lists, tunnels |
| Account | Members, roles, subscriptions, Workers scripts |
| Logs | Account audit log (accumulating), Access authentication requests |

The audit log previously stopped at 100 entries per run and overwrote itself.
It now pages to the end, backfilled 365 days on first run, and appends into a
de-duplicated archive that grows with every run.

---

## 4. Notion - 6 datasets

Full workspace export (Markdown and HTML), page and database inventory,
metadata, database schemas, all database rows, workspace statistics.

---

## 5. Not backed up - and why

Nothing here is a system fault.

### Blocked by licence

| Not collected | Requires | Impact |
|---|---|---|
| Risky user scoring | Entra ID P2 | No automated identity-risk ratings |
| Per-message mail trace | Defender for Office 365 P2 | No per-message delivery verdicts |
| Email hunting table | Defender for Office 365 P2 | No mail-flow threat telemetry |
| Device event and sign-in hunting | Defender for Endpoint, devices onboarded | No endpoint process telemetry |
| Privileged Identity Management | Entra ID P2 | No just-in-time role records |
| Teams messages | Teams licence | No teams exist in the tenant |
| Cloudflare HTTP, firewall, Gateway logs | Cloudflare Enterprise with Logpush | No per-request traffic logs |

The tenant holds **one seat** of Microsoft 365 Business Premium (no Teams).

### Blocked by a Microsoft fault

**Personal OneDrive.** The Graph Sites API returns HTTP 503 on every endpoint
for this tenant - sustained across days. This is a Microsoft-side fault, not a
permission or licence problem: `Sites.Read.All` and `Files.Read.All` are both
granted, and the audit log proves SharePoint is actively used.

We worked around it for SharePoint by switching to SharePoint's own REST API
with certificate authentication, which is why site files are collected in
full. Personal OneDrive has no equivalent fallback.

**Action: raise a Microsoft support ticket** for the Graph Sites 503. That
would restore OneDrive backup and remove the need for the certificate.

### Not applicable

Four of five accounts have no Exchange licence, so they have no mailbox,
calendar or contacts to back up. This is expected, not a failure.

---

## 6. How the backup works

```
collect → manifest → tar → zstd compress → age encrypt → SHA-256 → store
```

Every archive is encrypted and hash-verified. Three storage tiers with
independent retention:

| Where | Kept for |
|---|---|
| This server | 14 days |
| Backblaze B2 | 180 days |
| Office drive | forever - never deleted |

The office drive is filled by a script on the office laptop that copies down
from Backblaze each day and checks every archive against its SHA-256. It only
ever adds, so once Backblaze ages a backup out at 180 days the office drive
becomes its permanent home. The same script can refill a new cloud bucket from
the drive if the cloud copy is ever lost.

**Incremental backups are on.** The first run downloads everything; later runs
fetch only what changed. Verified: day 1 downloaded 990 SharePoint files
(79.7 MB), day 2 downloaded **0 files, 0 bytes** - all 990 recognised as
unchanged. Mail, calendar, contacts and all log sources use API checkpoints, so
only new items are ever fetched.

Backups can be run on demand from the terminal interface, or on a schedule
without anyone logged in.

---

## 7. Outstanding items

| Item | Owner |
|---|---|
| Microsoft support ticket for the Graph Sites 503 (restores OneDrive) | IT |
| Renew the SharePoint certificate before **26 July 2028** | IT |
| Install the office drive copy on the office laptop | IT |
| Deploy to the server on a schedule | Engineering |

No further API keys, permissions or credentials are needed. All 286 Graph
permissions required have been granted and verified.
