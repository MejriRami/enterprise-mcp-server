"""Fill empty demo databases with FICTIONAL data, through the MCP tools themselves.

Everything here is invented (company "Northwind Components", its plants, people,
KPIs and actions). Using the server's own write tools instead of raw SQL means
the demo data goes through the same validation, audit and business rules as
real usage.

Prerequisites: PEOPLE_DATABASE_URL and ACTION_PLAN_DATABASE_URL point to
databases that already have the schema (see README "Demo").

    python -m demo.seed_demo
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402  (registers the MCP tools)
import hr_tools  # noqa: E402

UNITS = [
    # unit_id, parent, name, short, city, country, zone
    (1, None, "Northwind Components Group", "NWC-HQ", "Lyon", "France", 1),
    (2, 1, "Northwind Plant Tunis", "NWC-TUN", "Tunis", "Tunisia", 1),
    (3, 1, "Northwind Plant Monterrey", "NWC-MTY", "Monterrey", "Mexico", 2),
    (4, 1, "Northwind Plant Suzhou", "NWC-SZH", "Suzhou", "China", 3),
]

ROLES = [
    # key, name, parent key, managerial, family
    ("ceo", "Chief Executive Officer", None, True, "Management"),
    ("quality_dir", "Group Quality Director", "ceo", True, "Quality"),
    ("plant_mgr", "Plant Manager", "ceo", True, "Operations"),
    ("quality_eng", "Quality Engineer", "quality_dir", False, "Quality"),
    ("prod_sup", "Production Supervisor", "plant_mgr", True, "Operations"),
    ("hr_mgr", "HR Manager", "ceo", True, "Human Resources"),
]

PEOPLE = [
    # first, last, unit, role key, email
    ("Claire", "Martin", 1, "ceo", "claire.martin@northwind.example"),
    ("Youssef", "Ben Salah", 1, "quality_dir", "youssef.bensalah@northwind.example"),
    ("Sami", "Trabelsi", 2, "plant_mgr", "sami.trabelsi@northwind.example"),
    ("Ana", "Lopez", 3, "plant_mgr", "ana.lopez@northwind.example"),
    ("Wei", "Zhang", 4, "plant_mgr", "wei.zhang@northwind.example"),
    ("Ines", "Gharbi", 2, "quality_eng", "ines.gharbi@northwind.example"),
    ("Diego", "Ramirez", 3, "quality_eng", "diego.ramirez@northwind.example"),
    ("Mehdi", "Kacem", 2, "prod_sup", "mehdi.kacem@northwind.example"),
    ("Laura", "Petit", 1, "hr_mgr", "laura.petit@northwind.example"),
]

ACTIONS = [
    # title, responsible email, due, priority
    ("Reduce scrap on winding line 2", "mehdi.kacem@northwind.example", "2026-12-15", 1),
    ("Requalify glue supplier after customer complaint", "ines.gharbi@northwind.example", "2026-11-30", 1),
    ("Deploy new final-inspection limit samples", "diego.ramirez@northwind.example", "2027-01-10", 2),
    ("Update PFMEA after process change on tinning", "ines.gharbi@northwind.example", "2026-11-10", 2),
]


def seed_reference_data() -> None:
    url = os.environ["PEOPLE_DATABASE_URL"]
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO unit_type (unit_type_name) SELECT 'Plant' WHERE NOT EXISTS (SELECT 1 FROM unit_type)")
        cur.execute("SELECT min(unit_type_id) FROM unit_type")
        unit_type_id = cur.fetchone()[0]
        for status_id, name in [(1, "Active"), (2, "Inactive"), (3, "Pending"), (4, "Suspended"), (5, "Closed")]:
            cur.execute("INSERT INTO status (status_id, status_name) OVERRIDING SYSTEM VALUE VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (status_id, name))
        for zone_id, name in [(1, "Europe & Africa"), (2, "Americas"), (3, "Asia")]:
            cur.execute("INSERT INTO zone (zone_id, zone_name) OVERRIDING SYSTEM VALUE VALUES (%s, %s) ON CONFLICT DO NOTHING", (zone_id, name))
        for unit_id, parent, name, short, city, country, zone in UNITS:
            cur.execute(
                """INSERT INTO unit (unit_id, parent_unit_id, unit_name, unit_short_name, city, country, unit_type_id, zone_id) OVERRIDING SYSTEM VALUE
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (unit_id, parent, name, short, city, country, unit_type_id, zone),
            )


def call(tool, **kwargs):
    result = tool(**kwargs)
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(f"{tool.__name__}: {result['error']}")
    return result


def find_id(result, *keys):
    """First value found for any of `keys`, searching nested dicts (tools wrap
    their payload as {"success": ..., "data": {"role": {...}}})."""
    stack = [result]
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            for key in keys:
                if node.get(key) is not None:
                    return node[key]
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)
    raise KeyError(f"none of {keys} in tool result")


def main() -> None:
    seed_reference_data()

    role_ids: dict[str, int] = {}
    for key, name, parent, managerial, family in ROLES:
        res = call(hr_tools.create_role, role_name=name, parent_role_id=role_ids.get(parent),
                   is_managerial=managerial, role_family=family)
        role_ids[key] = find_id(res, "role_id", "id")

    people_ids: dict[str, int] = {}
    for first, last, unit_id, role_key, email in PEOPLE:
        res = call(hr_tools.create_person, first_name=first, name=last, work_at_unit_id=unit_id, email=email)
        pid = find_id(res, "people_id", "id")
        people_ids[email] = pid
        call(hr_tools.assign_role_to_person, people_id=pid, role_id=role_ids[role_key], unit_id=unit_id)

    sujet = call(server.create_sujet, titre="Quality improvement plan 2026", code="QIP-2026",
                 description="Fictional demo action plan", inserted_by="demo")
    sujet_id = find_id(sujet, "sujet_id", "id")
    for title, email, due, prio in ACTIONS:
        call(server.create_action, sujet_id=sujet_id, type="corrective", titre=title, responsable=email.split("@")[0],
             email_responsable=email, due_date=due, priorite=prio)

    print(f"roles: {len(role_ids)}, people: {len(people_ids)}, actions: {len(ACTIONS)}")


if __name__ == "__main__":
    main()
