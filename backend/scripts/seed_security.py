"""Seed two related SQLite security databases for NL2SQL testing.

  data/incidents.db   incidents, alerts            (the "warehouse")
  data/assets.db      assets, identities           (the "asset/identity store")

They relate so cross-database questions work:
  incidents.asset_id   -> assets.id
  incidents.opened_by  -> identities.email
  assets.owner_email   -> identities.email

Run:  python scripts/seed_security.py
"""
from __future__ import annotations

import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
os.makedirs(DATA, exist_ok=True)

IDENTITIES = [
    ("a.kumar@corp.com", "Anita Kumar", "Finance", "Analyst", 1),
    ("s.lee@corp.com", "Sam Lee", "Finance", "Manager", 0),
    ("m.patel@corp.com", "Maya Patel", "Engineering", "SRE", 1),
    ("j.diaz@corp.com", "Jon Diaz", "Sales", "Rep", 0),
    ("r.khan@corp.com", "Riya Khan", "Security", "Analyst", 1),
    ("t.owen@corp.com", "Tom Owen", "Engineering", "Developer", 1),
    ("l.chen@corp.com", "Lin Chen", "HR", "Specialist", 0),
    ("p.gupta@corp.com", "Priya Gupta", "Finance", "Controller", 1),
    ("d.ford@corp.com", "Dan Ford", "Sales", "Director", 0),
    ("e.silva@corp.com", "Eva Silva", "Security", "Engineer", 1),
    ("k.brown@corp.com", "Kim Brown", "HR", "Manager", 1),
    ("w.zhao@corp.com", "Wei Zhao", "Engineering", "Architect", 1),
]

ASSETS = [
    ("AST-001", "finance-vpn-gw", "vpn", "Finance", "p.gupta@corp.com", "critical"),
    ("AST-002", "fin-db-prod", "database", "Finance", "p.gupta@corp.com", "critical"),
    ("AST-003", "fin-laptop-12", "laptop", "Finance", "a.kumar@corp.com", "medium"),
    ("AST-004", "sales-crm", "server", "Sales", "d.ford@corp.com", "high"),
    ("AST-005", "sales-laptop-7", "laptop", "Sales", "j.diaz@corp.com", "low"),
    ("AST-006", "eng-build-srv", "server", "Engineering", "w.zhao@corp.com", "high"),
    ("AST-007", "eng-k8s-prod", "server", "Engineering", "m.patel@corp.com", "critical"),
    ("AST-008", "hr-portal", "server", "HR", "k.brown@corp.com", "high"),
    ("AST-009", "hr-laptop-3", "laptop", "HR", "l.chen@corp.com", "low"),
    ("AST-010", "soc-siem", "server", "Security", "e.silva@corp.com", "high"),
    ("AST-011", "fin-laptop-19", "laptop", "Finance", "s.lee@corp.com", "medium"),
    ("AST-012", "eng-laptop-22", "laptop", "Engineering", "t.owen@corp.com", "low"),
    ("AST-013", "corp-vpn-gw", "vpn", "Engineering", "w.zhao@corp.com", "critical"),
    ("AST-014", "sales-db", "database", "Sales", "d.ford@corp.com", "high"),
    ("AST-015", "hr-db", "database", "HR", "k.brown@corp.com", "critical"),
]

# (id, title, severity, status, asset_id, opened_by, ts)
INCIDENTS = [
    ("INC-4821", "Credential phishing targeting finance", "P1", "open", "AST-001", "r.khan@corp.com", "2026-06-03 02:14"),
    ("INC-4822", "MFA fatigue attempts on finance VPN", "P1", "investigating", "AST-001", "r.khan@corp.com", "2026-06-03 01:55"),
    ("INC-4830", "Suspicious query on fin-db-prod", "P1", "open", "AST-002", "e.silva@corp.com", "2026-06-03 00:48"),
    ("INC-4841", "Malware on sales laptop", "P2", "open", "AST-005", "r.khan@corp.com", "2026-06-02 23:30"),
    ("INC-4855", "Brute force on corp VPN", "P1", "open", "AST-013", "e.silva@corp.com", "2026-06-02 22:05"),
    ("INC-4860", "Data exfil attempt from sales-crm", "P1", "investigating", "AST-004", "r.khan@corp.com", "2026-06-02 20:10"),
    ("INC-4866", "Phishing email reported by HR", "P3", "closed", "AST-008", "l.chen@corp.com", "2026-06-02 18:40"),
    ("INC-4870", "Privilege escalation on eng-k8s-prod", "P1", "open", "AST-007", "e.silva@corp.com", "2026-06-02 16:22"),
    ("INC-4875", "Unpatched CVE on eng-build-srv", "P2", "open", "AST-006", "m.patel@corp.com", "2026-06-02 14:05"),
    ("INC-4880", "Anomalous login HR portal", "P2", "investigating", "AST-008", "r.khan@corp.com", "2026-06-02 12:33"),
    ("INC-4888", "Ransomware indicator on hr-db", "P1", "open", "AST-015", "e.silva@corp.com", "2026-06-02 10:18"),
    ("INC-4890", "Phishing on finance controller", "P2", "open", "AST-002", "r.khan@corp.com", "2026-06-02 09:02"),
    ("INC-4895", "Lost laptop (eng)", "P3", "closed", "AST-012", "t.owen@corp.com", "2026-06-01 17:45"),
    ("INC-4901", "Suspicious outbound from sales-db", "P2", "open", "AST-014", "r.khan@corp.com", "2026-06-01 15:20"),
    ("INC-4910", "Phishing cluster across finance", "P1", "open", "AST-003", "r.khan@corp.com", "2026-06-01 11:11"),
    ("INC-4915", "Failed DLP on hr-laptop", "P3", "open", "AST-009", "l.chen@corp.com", "2026-06-01 09:50"),
    ("INC-4920", "SIEM ingestion outage", "P2", "closed", "AST-010", "e.silva@corp.com", "2026-05-31 22:00"),
    ("INC-4930", "Token theft on eng laptop", "P2", "investigating", "AST-012", "e.silva@corp.com", "2026-05-31 14:30"),
    ("INC-4940", "Vishing attempt on sales director", "P3", "open", "AST-004", "r.khan@corp.com", "2026-05-31 10:05"),
    ("INC-4950", "Critical CVE on finance VPN", "P1", "open", "AST-001", "e.silva@corp.com", "2026-05-30 19:40"),
]

# (incident_id, source, signal)
ALERTS = [
    ("INC-4821", "EDR", "credential_dumping"),
    ("INC-4821", "SIEM", "impossible_travel"),
    ("INC-4822", "SIEM", "mfa_push_spam"),
    ("INC-4830", "DB-Audit", "bulk_select_sensitive"),
    ("INC-4855", "IDS", "ssh_brute_force"),
    ("INC-4860", "DLP", "large_outbound_transfer"),
    ("INC-4870", "EDR", "privilege_escalation"),
    ("INC-4888", "EDR", "file_encryption_burst"),
    ("INC-4910", "Email-GW", "phishing_kit_match"),
    ("INC-4910", "EDR", "credential_dumping"),
    ("INC-4950", "Vuln-Scan", "cve_2026_critical"),
]


def _seed_assets():
    path = os.path.join(DATA, "assets.db")
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE assets(
            id TEXT PRIMARY KEY, name TEXT, type TEXT,
            business_unit TEXT, owner_email TEXT, criticality TEXT);
        CREATE TABLE identities(
            email TEXT PRIMARY KEY, name TEXT, department TEXT,
            role TEXT, mfa_enabled INTEGER);
        """
    )
    c.executemany("INSERT INTO assets VALUES (?,?,?,?,?,?)", ASSETS)
    c.executemany("INSERT INTO identities VALUES (?,?,?,?,?)", IDENTITIES)
    c.commit()
    c.close()
    return path


def _seed_incidents():
    path = os.path.join(DATA, "incidents.db")
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE incidents(
            id TEXT PRIMARY KEY, title TEXT, severity TEXT, status TEXT,
            asset_id TEXT, opened_by TEXT, ts TEXT);
        CREATE TABLE alerts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT,
            source TEXT, signal TEXT);
        """
    )
    c.executemany("INSERT INTO incidents VALUES (?,?,?,?,?,?,?)", INCIDENTS)
    c.executemany("INSERT INTO alerts(incident_id, source, signal) VALUES (?,?,?)", ALERTS)
    c.commit()
    c.close()
    return path


if __name__ == "__main__":
    a = _seed_assets()
    i = _seed_incidents()
    print(f"seeded {i}  ({len(INCIDENTS)} incidents, {len(ALERTS)} alerts)")
    print(f"seeded {a}  ({len(ASSETS)} assets, {len(IDENTITIES)} identities)")
