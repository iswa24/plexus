"""Seed security knowledge base for the RAG agent (swap for your real corpus)."""
from __future__ import annotations

DOCS = {
    "ir-runbook": """Incident Response Runbook — Overview.
The IR lifecycle has six phases: Preparation, Identification, Containment, Eradication, Recovery, and Lessons Learned.

Identification. Triage the alert, confirm it is a true positive, assign a severity (P1-P4), and declare an incident in the IR tool. P1 incidents require a bridge within 15 minutes and notification to the on-call SOC lead and the BISO.

Containment. For a compromised host, isolate it from the network (EDR network-contain) but do not power it off (preserve volatile memory). Reset credentials for affected identities and revoke active sessions/tokens. For account compromise, force password reset and re-enrollment of MFA.

Eradication. Remove malware, close the initial access vector, and patch the exploited vulnerability. Confirm no persistence remains (scheduled tasks, services, registry run keys).

Recovery. Restore from known-good backups, monitor for re-infection for 72 hours, and only return assets to production after validation.""",

    "ransomware-playbook": """Ransomware Playbook.
On a confirmed ransomware indicator (mass file-encryption, ransom note, or EDR 'file_encryption_burst'): immediately isolate the affected systems from the network and disable the affected accounts. Do NOT pay the ransom without executive and legal approval.

Containment steps: 1) network-contain all hosts showing the indicator; 2) block the C2 IPs/domains at the edge; 3) disable shared-drive write access; 4) snapshot affected volumes for forensics.

Notify the CISO, legal, and the cyber-insurance contact within 1 hour. Preserve evidence. Begin recovery only from offline/immutable backups after eradication is confirmed.""",

    "access-policy": """Access Control Policy.
All privileged access requires multi-factor authentication (MFA). Accounts without MFA may not access critical or high-criticality assets. Owners of critical assets must have MFA enabled at all times.

Joiners-Movers-Leavers (JML): access is provisioned on role change and must be reviewed quarterly. Segregation of Duties (SoD): no single identity may both create and approve a payment, nor both request and grant access.

Service accounts must be non-interactive, have no MFA exemption for human use, and rotate credentials every 90 days.""",

    "phishing-response": """Phishing Response Procedure.
When a user reports a phishing email: collect the headers, the sender, URLs, and any attachments. Check the sending IP and domain reputation against threat intelligence. If the user clicked a link or entered credentials, treat it as a potential account compromise — force password reset, revoke sessions, and enable enhanced monitoring on the account.

If the campaign targets multiple finance users, escalate to P1 (likely credential-harvesting campaign). Block the phishing domain and sinkhole it. Search the mail gateway for other recipients and pull the message.""",
}
