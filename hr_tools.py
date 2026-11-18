# ============================================================================
# ============================================================================
# HR TOOLS — Role tree, People, Role assignments   (KPI_DB_Final / "people")
#
# À COLLER dans server.py JUSTE AVANT la section
#     "SECTION 2 — Tool profile filter"
# (donc avant la ligne `KPI_AGENT_TOOL_ALLOWLIST = {`).
#
# Réutilise les helpers existants : db_connection, success_response,
# error_response, insert_row, _row_exists, _valid_values_text, json_safe.
#
# ── Ce que le schéma réel impose (vérifié sur KPI_DB_Final) ─────────────────
#
# people
#   people_id            IDENTITY ALWAYS  -> jamais fourni à l'insert
#   NOT NULL             name, first_name, paid_by_unit_id, work_at_unit_id, status
#   CHECK status         Active | Inactive              (casse mixte !)
#   CHECK gender         Male | Female | NULL
#   UNIQUE email         (1686 / 2109 personnes n'ont PAS d'email -> reste optionnel)
#   FK                   paid_by_unit_id, work_at_unit_id -> unit
#
# role
#   role_id              nextval(role_role_id_seq1)
#   NOT NULL             role_name
#   UNIQUE               role_name, normalized_role_name
#   CHECK role_level     Executive|VP|Director|Manager|Supervisor|Lead|Senior|
#                        Professional|Operational|Coordinator|Assistant
#   CHECK grade          G1..G7 | NULL
#   CHECK ceo_level      VP|Director|Senior Manager|Manager / Supervisor|
#                        Specialist / Engineer|Technician / Admin.|
#                        Operator / Assistant | NULL
#   CHECK scope          Groupe | Usine | NULL
#   FK                   parent_role_id -> role, functional_report_role_id -> role
#   AUCUN trigger ne remplit normalized_role_name ni role_slug -> les outils
#   les dérivent (convention observée : normalized = lower(role_name),
#   slug = lower + non-alphanum -> "_", ex. "Production / Material planner"
#   -> "production_material_planner").
#
# people_role_assignment  (= la table d'historique RH ; il n'existe AUCUNE
#                            table people_history dans la base)
#   assignment_id        nextval
#   NOT NULL             people_id  (role_id et unit_id sont nullable)
#   CHECK assignment_status  ACTIVE | INACTIVE | ENDED   (MAJUSCULES !)
#   defaults             allocation_percentage=100, is_primary=true,
#                        start_date=CURRENT_DATE, assignment_status='ACTIVE'
#   FK                   people_id -> people, role_id -> role,
#                        product_line_id -> product_line,
#                        reports_to -> people_role_assignment(assignment_id),
#                        functional_manager_assignment_id -> ...(assignment_id)
#   PAS de FK sur unit_id / zone_id / market_id  -> validés applicativement ici.
#   assignment_type observé : PERMANENT | PRIMARY | TEMPORARY | INTERIM | NULL
#
# ── Invariants applicatifs (aucune contrainte SQL ne les protège) ───────────
#   1. UNE SEULE affectation ACTIVE avec is_primary=true par personne.
#      Vérifié : 0 violation sur 2062 personnes affectées. Les outils la
#      maintiennent (rétrogradation explicite ou refus).
#   2. reports_to pointe sur un assignment_id, PAS sur un people_id.
#      Piège classique. Validé ici : doit être une affectation ACTIVE
#      d'une AUTRE personne.
#   3. Changement de poste = 2 lignes, jamais un UPDATE de role_id :
#      l'ancienne passe ENDED + end_date, la nouvelle est ACTIVE avec
#      start_date = end_date de l'ancienne (pattern observé : 33/33 des
#      lignes ENDED ont un end_date renseigné).
#
# ── Effets de bord connus ──────────────────────────────────────────────────
#   * trigger trg_sync_customer_kam (fn_sync_customer_main_contacts) se
#     déclenche sur INSERT/UPDATE/DELETE de people_role_assignment : les
#     contacts principaux (KAM / sales) de la table customer sont resynchronisés
#     automatiquement à chaque affectation.
#   * trigger trg_kpi_knowledge_refresh_on_people / _on_role : toute écriture
#     sur people ou role met en file un refresh du knowledge base KPI.
#   * v_people_assignments filtre  WHERE assignment_status = 'ACTIVE'
#     OR assignment_id IS NULL, SANS upper() : un statut écrit 'Active' ou
#     'ended' ferait disparaître la ligne. Tous les statuts écrits ici sont donc
#     en MAJUSCULES. Conséquence directe : une personne dont toutes les
#     affectations sont ENDED sort complètement de la vue — donc de
#     search_member, list_members, get_member, des responsables KPI et des
#     escalades — même si people.status vaut 'Active'. C'est le cas aujourd'hui
#     de people_id 7 (Joachim GEHL) et 8 (Gunther LUSCHTINETZ). D'où le
#     garde-fou de end_role_assignment.
#   * v_people_with_boss filtre sur une LISTE FIGÉE de role_id
#     (1..7,9..11,13,14,16,17,20..27,48,53,142,143). Les rôles créés par
#     create_role n'y apparaîtront PAS, donc get_org_path /
#     get_escalation_target / get_direct_reports les ignoreront jusqu'à ce que
#     la vue soit étendue. Renseigner reports_to reste le mécanisme fiable.
# ============================================================================
# ============================================================================

# ── Vocabulaires contrôlés (alignés sur les CHECK de la base) ───────────────

import re
import sys
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from psycopg2 import errors, sql

_server_module = sys.modules.get("server")
if _server_module is None or not hasattr(_server_module, "mcp"):
    main_module = sys.modules.get("__main__")
    if main_module is not None and hasattr(main_module, "mcp"):
        _server_module = main_module
    else:
        import server as _server_module

ASSIGNMENT_STATUS_ACTIVE = _server_module.ASSIGNMENT_STATUS_ACTIVE
PEOPLE_PK = _server_module.PEOPLE_PK
PRA_PK = _server_module.PRA_PK
ROLE_PK = _server_module.ROLE_PK
TABLE_CUSTOMER = _server_module.TABLE_CUSTOMER
TABLE_KPI = _server_module.TABLE_KPI
TABLE_KPI_ALLOCATION = _server_module.TABLE_KPI_ALLOCATION
TABLE_MARKET = _server_module.TABLE_MARKET
TABLE_PEOPLE = _server_module.TABLE_PEOPLE
TABLE_PRA = _server_module.TABLE_PRA
TABLE_ROLE = _server_module.TABLE_ROLE
TABLE_UNIT = _server_module.TABLE_UNIT
TABLE_ZONE = _server_module.TABLE_ZONE
db_connection = _server_module.db_connection
error_response = _server_module.error_response
insert_row = _server_module.insert_row
json_safe = _server_module.json_safe
logger = _server_module.logger
mcp = _server_module.mcp
merge_create_payload = _server_module.merge_create_payload
merge_update_payload = _server_module.merge_update_payload
success_response = _server_module.success_response
update_row = _server_module.update_row
_row_exists = _server_module._row_exists
_valid_values_text = _server_module._valid_values_text

VALID_ROLE_LEVEL = {
    "Executive", "VP", "Director", "Manager", "Supervisor", "Lead",
    "Senior", "Professional", "Operational", "Coordinator", "Assistant",
}
VALID_ROLE_GRADE = {"G1", "G2", "G3", "G4", "G5", "G6", "G7"}
VALID_ROLE_CEO_LEVEL = {
    "VP", "Director", "Senior Manager", "Manager / Supervisor",
    "Specialist / Engineer", "Technician / Admin.", "Operator / Assistant",
}
VALID_ROLE_SCOPE = {"Groupe", "Usine"}

VALID_PEOPLE_STATUS = {"Active", "Inactive"}
VALID_GENDER = {"Male", "Female"}
VALID_ASSIGNMENT_TYPES_HR = {"PERMANENT", "PRIMARY", "TEMPORARY", "INTERIM"}

TABLE_PRODUCT_LINE = "product_line"
GROUP_UNIT_ID = 1  # unit_id 1 = Group : un manager "Groupe" encadre toutes les usines

HR_MAX_ROLE_DEPTH = 30
HR_DELETE_DEPENDENCY_ROW_LIMIT = 25

ROLE_CREATE_GUARDED_FIELDS = {
    "role_id", "role_name", "normalized_role_name", "role_slug",
    "parent_role_id", "role_family", "role_level", "grade", "ceo_level",
    "scope", "is_managerial", "is_active", "valid_from", "valid_to",
    "functional_report_role_id", "created_at", "updated_at",
}
ROLE_UPDATE_GUARDED_FIELDS = ROLE_CREATE_GUARDED_FIELDS | {"role_definition"}
PEOPLE_CREATE_GUARDED_FIELDS = {
    "people_id", "name", "first_name", "seniority", "email", "cell_phone",
    "paid_by_unit_id", "work_at_unit_id", "native_language", "other_languages",
    "other_information", "status", "comments", "hr_employee_id", "hr_site",
    "gender", "hire_date", "date_of_birth",
}
PEOPLE_UPDATE_GUARDED_FIELDS = PEOPLE_CREATE_GUARDED_FIELDS
ASSIGNMENT_UPDATE_GUARDED_FIELDS = {
    "assignment_id", "people_id", "role_id", "unit_id", "zone_id", "market_id",
    "assignment_type", "allocation_percentage", "is_primary",
    "functional_manager_role_id", "assistant_context", "knowledge_context",
    "start_date", "end_date", "assignment_status", "created_at", "updated_at",
    "product_line_id", "reports_to", "target_role_label",
    "functional_manager_assignment_id", "admin_manager_assignment_id",
}


# ── Helpers RH ──────────────────────────────────────────────────────────────

def _hr_field_keys(fields: Optional[Dict[str, Any]]) -> Set[str]:
    return {str(k).lower() for k in fields} if fields else set()


def _hr_reject_guarded_fields(
    fields: Optional[Dict[str, Any]], guarded: Set[str], field_name: str
) -> Optional[Dict[str, Any]]:
    bad = sorted(_hr_field_keys(fields).intersection(guarded))
    if not bad:
        return None
    return error_response(
        f"{field_name} ne peut pas modifier directement : {', '.join(bad)}. "
        "Utiliser les parametres dedies de l'outil afin de conserver les validations RH."
    )


def _hr_normalize_role_name(role_name: str) -> str:
    """Convention observée dans role.normalized_role_name : minuscules, espaces compactés."""
    return re.sub(r"\s+", " ", str(role_name).strip()).lower()


def _hr_slugify_role(role_name: str) -> str:
    """
    Convention observée dans role.role_slug :
      'Production / Material planner' -> 'production_material_planner'
      'Receiving / Shipping'          -> 'receiving_shipping'
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(role_name).strip().lower())
    return slug.strip("_")[:150]


def _hr_unique_role_slug(cur, base_slug: str, exclude_role_id: Optional[int] = None) -> str:
    """role_slug n'a pas de contrainte UNIQUE en base, mais on évite les doublons."""
    if not base_slug:
        return base_slug
    candidate = base_slug
    suffix = 2
    while True:
        if exclude_role_id is None:
            cur.execute("SELECT 1 FROM role WHERE role_slug = %s LIMIT 1", (candidate,))
        else:
            cur.execute(
                "SELECT 1 FROM role WHERE role_slug = %s AND role_id <> %s LIMIT 1",
                (candidate, exclude_role_id),
            )
        if cur.fetchone() is None:
            return candidate
        candidate = f"{base_slug[:145]}_{suffix}"
        suffix += 1


def _hr_parse_date(value: Any, field_name: str, default_today: bool = False) -> Optional[date]:
    if value is None:
        return date.today() if default_today else None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except Exception:
        raise ValueError(f"{field_name} doit être une date ISO 'YYYY-MM-DD'.")


def _hr_check_enum(value: Any, allowed: Iterable[str], field_name: str) -> None:
    if value is not None and value not in set(allowed):
        raise ValueError(f"{field_name} doit être l'une de : {_valid_values_text(allowed)}.")


def _hr_role_subtree_ids(cur, role_id: int) -> List[int]:
    """
    Tous les descendants d'un rôle (rôle inclus), is_active ignoré volontairement :
    la détection de cycle doit voir aussi les branches désactivées.
    """
    cur.execute(
        """
        WITH RECURSIVE d AS (
            SELECT role_id, ARRAY[role_id] AS id_path
            FROM role WHERE role_id = %s
            UNION ALL
            SELECT r.role_id, d.id_path || r.role_id
            FROM role r
            JOIN d ON r.parent_role_id = d.role_id
            WHERE NOT r.role_id = ANY(d.id_path)
              AND array_length(d.id_path, 1) < %s
        )
        SELECT role_id FROM d
        """,
        (role_id, HR_MAX_ROLE_DEPTH),
    )
    return [r["role_id"] for r in cur.fetchall()]


def _hr_get_role(cur, role_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        """
        SELECT r.*, vr.full_path AS role_full_path, vr.depth AS role_depth
        FROM role r
        LEFT JOIN v_role_tree vr ON vr.role_id = r.role_id
        WHERE r.role_id = %s
        """,
        (role_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _hr_get_person(cur, people_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        f"""
        SELECT p.*, wu.unit_name AS work_at_unit_name, pu.unit_name AS paid_by_unit_name
        FROM {TABLE_PEOPLE} p
        LEFT JOIN {TABLE_UNIT} wu ON wu.unit_id = p.work_at_unit_id
        LEFT JOIN {TABLE_UNIT} pu ON pu.unit_id = p.paid_by_unit_id
        WHERE p.people_id = %s
        """,
        (people_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _hr_get_assignment(cur, assignment_id: int) -> Optional[Dict[str, Any]]:
    cur.execute(
        f"""
        SELECT a.*, r.role_name, r.role_level, r.parent_role_id,
               u.unit_name, p.first_name, p.name AS last_name, p.email
        FROM {TABLE_PRA} a
        JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
        LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
        LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
        WHERE a.assignment_id = %s
        """,
        (assignment_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _hr_active_primary(cur, people_id: int, exclude_assignment_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """L'affectation ACTIVE principale d'une personne (invariant : au plus une)."""
    params: List[Any] = [people_id]
    extra = ""
    if exclude_assignment_id is not None:
        extra = " AND a.assignment_id <> %s"
        params.append(exclude_assignment_id)
    cur.execute(
        f"""
        SELECT a.assignment_id, a.role_id, r.role_name, a.unit_id, u.unit_name,
               a.start_date, a.allocation_percentage, a.reports_to
        FROM {TABLE_PRA} a
        LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
        LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
        WHERE a.people_id = %s
          AND upper(a.assignment_status) = 'ACTIVE'
          AND a.is_primary IS TRUE{extra}
        ORDER BY a.assignment_id
        LIMIT 1
        """,
        params,
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _hr_validate_reference(
    cur,
    *,
    unit_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
) -> Optional[str]:
    """
    unit_id / zone_id / market_id de people_role_assignment n'ont AUCUNE FK en
    base : on les valide ici pour éviter des affectations orphelines.
    """
    checks = [
        (unit_id, TABLE_UNIT, "unit_id", "unit_id"),
        (zone_id, TABLE_ZONE, "zone_id", "zone_id"),
        (market_id, TABLE_MARKET, "market_id", "market_id"),
        (product_line_id, TABLE_PRODUCT_LINE, "product_line_id", "product_line_id"),
    ]
    for value, table, pk_col, label in checks:
        if value is not None and not _row_exists(cur, table, pk_col, value):
            return f"{label}={value} n'existe pas dans {table}."
    return None


def _hr_validate_reports_to(cur, reports_to: int, people_id: int) -> Optional[str]:
    cur.execute(
        f"SELECT assignment_id, people_id, assignment_status FROM {TABLE_PRA} WHERE assignment_id = %s",
        (reports_to,),
    )
    boss = cur.fetchone()
    if boss is None:
        return (
            f"reports_to={reports_to} n'existe pas. Attention : reports_to attend un "
            "assignment_id (affectation du manager), PAS un people_id."
        )
    if str(boss["assignment_status"]).upper() != "ACTIVE":
        return f"reports_to={reports_to} pointe sur une affectation {boss['assignment_status']}, pas ACTIVE."
    if boss["people_id"] == people_id:
        return f"reports_to={reports_to} est une affectation de la personne elle-même."
    return None


def _hr_resolve_reports_to(
    cur,
    role_id: int,
    unit_id: Optional[int],
    people_id: int,
    zone_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Déduit le manager (assignment_id) à partir du rôle parent, avec la même
    logique de priorité que v_people_with_boss : même unité > unité Groupe (1)
    > unité NULL. Retourne {'assignment_id': .., 'candidates': [..],
    'ambiguous': bool} — jamais d'erreur : l'appelant décide.
    """
    cur.execute(f"SELECT parent_role_id FROM {TABLE_ROLE} WHERE role_id = %s", (role_id,))
    row = cur.fetchone()
    parent_role_id = row["parent_role_id"] if row else None
    if parent_role_id is None:
        return {"assignment_id": None, "candidates": [], "ambiguous": False,
                "reason": "Le rôle n'a pas de rôle parent (sommet de l'arborescence)."}

    cur.execute(
        f"""
        SELECT a.assignment_id, a.people_id, a.unit_id, a.zone_id, a.is_primary,
               p.first_name, p.name AS last_name, p.email,
               r.role_name AS manager_role_name, u.unit_name,
               CASE WHEN a.unit_id = %s THEN 1
                    WHEN a.unit_id = %s THEN 2
                    WHEN a.unit_id IS NULL THEN 3
                    ELSE 4 END AS tier,
               CASE WHEN a.zone_id = %s THEN 1
                    WHEN a.zone_id IS NULL THEN 2
                    ELSE 3 END AS zone_tier
        FROM {TABLE_PRA} a
        JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
        LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
        LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
        WHERE upper(a.assignment_status) = 'ACTIVE'
          AND a.role_id = %s
          AND a.people_id <> %s
        ORDER BY tier, zone_tier, a.is_primary DESC NULLS LAST, a.assignment_id
        """,
        (unit_id, GROUP_UNIT_ID, zone_id, parent_role_id, people_id),
    )
    rows = [dict(r) for r in cur.fetchall()]
    # On ne retient que les candidats pertinents pour l'unité visée.
    rows = [r for r in rows if r["tier"] <= 3]
    if not rows:
        return {"assignment_id": None, "candidates": [], "ambiguous": False,
                "reason": f"Aucun titulaire ACTIF du rôle parent {parent_role_id} trouvé."}
    best_tier = rows[0]["tier"]
    tier_best = [r for r in rows if r["tier"] == best_tier]
    best_zone_tier = tier_best[0]["zone_tier"]
    best = [r for r in tier_best if r["zone_tier"] == best_zone_tier]
    if len(best) == 1:
        return {"assignment_id": best[0]["assignment_id"], "candidates": best, "ambiguous": False}
    return {"assignment_id": None, "candidates": best, "ambiguous": True,
            "reason": f"{len(best)} managers possibles au même niveau — passer reports_to explicitement."}


def _hr_insert_assignment(cur, payload: Dict[str, Any]) -> Dict[str, Any]:
    clean = {k: v for k, v in payload.items() if v is not None}
    q = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({vals}) RETURNING *").format(
        t=sql.Identifier(TABLE_PRA),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in clean),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in clean),
    )
    cur.execute(q, list(clean.values()))
    return dict(cur.fetchone())


def _hr_close_assignment(
    cur, assignment_id: int, end_date: date, new_status: str = "ENDED",
) -> Optional[Dict[str, Any]]:
    """
    Clôt une affectation. is_primary est VOLONTAIREMENT conservé : 43 des 54
    lignes ENDED de la base gardent is_primary=true, et l'invariant "une seule
    principale" ne porte que sur les lignes ACTIVE. Le remettre à false
    divergerait de l'historique existant sans rien protéger.
    assignment_status est écrit en MAJUSCULES car v_people_assignments compare
    sans upper() : "Active" ou "ended" feraient disparaître la ligne.
    """
    cur.execute(
        f"""
        UPDATE {TABLE_PRA}
        SET assignment_status = %s,
            end_date          = %s,
            updated_at        = now()
        WHERE assignment_id = %s
        RETURNING *
        """,
        (new_status, end_date, assignment_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _hr_allocation_warning(cur, people_id: int) -> Optional[str]:
    cur.execute(
        f"""
        SELECT COALESCE(SUM(allocation_percentage), 0) AS total
        FROM {TABLE_PRA}
        WHERE people_id = %s AND upper(assignment_status) = 'ACTIVE'
        """,
        (people_id,),
    )
    total = float(cur.fetchone()["total"] or 0)
    if total > 100:
        return (f"Attention : la somme des allocation_percentage ACTIVES de cette personne "
                f"atteint {total:g}% (> 100%). Non bloquant, mais à corriger.")
    return None


def _hr_dependent_assignments(cur, assignment_id: int) -> List[Dict[str, Any]]:
    """Affectations ACTIVES qui déclarent celle-ci comme manager (reports_to / functional / admin)."""
    cur.execute(
        f"""
        SELECT a.assignment_id, a.people_id, p.first_name, p.name AS last_name,
               r.role_name, a.unit_id, u.unit_name,
               (a.reports_to = %s) AS via_reports_to,
               (a.functional_manager_assignment_id = %s) AS via_functional_manager,
               (a.admin_manager_assignment_id = %s) AS via_admin_manager
        FROM {TABLE_PRA} a
        JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
        LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
        LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
        WHERE upper(a.assignment_status) = 'ACTIVE'
          AND (a.reports_to = %s
               OR a.functional_manager_assignment_id = %s
               OR a.admin_manager_assignment_id = %s)
        ORDER BY p.name, p.first_name
        """,
        (assignment_id,) * 6,
    )
    return [dict(r) for r in cur.fetchall()]


def _hr_limited_rows_payload(count: int, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"count": int(count or 0)}
    if rows:
        payload["rows"] = rows
    if count > len(rows):
        payload["row_limit"] = len(rows)
    return payload


def _hr_role_reference_count(cur, table_schema: str, table_name: str, column_name: str, role_id: int) -> int:
    cur.execute(
        sql.SQL("SELECT count(*) AS n FROM {t} WHERE {c} = %s").format(
            t=sql.Identifier(table_schema, table_name),
            c=sql.Identifier(column_name),
        ),
        (role_id,),
    )
    return int(cur.fetchone()["n"] or 0)


def _hr_role_foreign_key_references(cur, role_id: int) -> List[Dict[str, Any]]:
    """
    Dynamic FK safety net for every FK pointing to role(role_id).
    Rich known dependencies are reported elsewhere, so this list keeps the
    remaining FK references that would still block a permanent DELETE.
    """
    known_rich_checks = {
        (TABLE_ROLE, "parent_role_id"),
        (TABLE_ROLE, "functional_report_role_id"),
        (TABLE_PRA, "role_id"),
        (TABLE_KPI_ALLOCATION, "role_id"),
        (TABLE_KPI, "owner_role_id"),
    }
    delete_rule = {
        "a": "NO ACTION",
        "r": "RESTRICT",
        "c": "CASCADE",
        "n": "SET NULL",
        "d": "SET DEFAULT",
    }
    cur.execute(
        """
        SELECT con.conname AS constraint_name,
               nsp.nspname AS table_schema,
               rel.relname AS table_name,
               att.attname AS column_name,
               con.confdeltype AS delete_rule_code
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
        JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY AS u(attnum, refattnum, ord) ON TRUE
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
        JOIN pg_attribute refatt ON refatt.attrelid = con.confrelid AND refatt.attnum = u.refattnum
        WHERE con.contype = 'f'
          AND con.confrelid = to_regclass(%s)
          AND refatt.attname = %s
          AND array_length(con.conkey, 1) = 1
          AND array_length(con.confkey, 1) = 1
        ORDER BY nsp.nspname, rel.relname, att.attname
        """,
        (TABLE_ROLE, ROLE_PK[0]),
    )
    refs: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        table_name = row["table_name"]
        column_name = row["column_name"]
        if (table_name, column_name) in known_rich_checks:
            continue
        count = _hr_role_reference_count(cur, row["table_schema"], table_name, column_name, role_id)
        if count:
            refs.append({
                "constraint_name": row["constraint_name"],
                "table_schema": row["table_schema"],
                "table": f"{row['table_schema']}.{table_name}",
                "column": column_name,
                "count": count,
                "delete_rule": delete_rule.get(row["delete_rule_code"], row["delete_rule_code"]),
            })
    return refs


def _hr_non_fk_role_references(cur, role_id: int) -> List[Dict[str, Any]]:
    """
    Conservative safety net for role-like columns that are not protected by an
    FK. These are not schema guarantees, but they are still business references.
    """
    cur.execute(
        """
        WITH role_fk_cols AS (
            SELECT nsp.nspname AS table_schema,
                   rel.relname AS table_name,
                   att.attname AS column_name
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY AS u(attnum, refattnum, ord) ON TRUE
            JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
            JOIN pg_attribute refatt ON refatt.attrelid = con.confrelid AND refatt.attnum = u.refattnum
            WHERE con.contype = 'f'
              AND con.confrelid = to_regclass(%s)
              AND refatt.attname = %s
        )
        SELECT c.table_schema, c.table_name, c.column_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        LEFT JOIN role_fk_cols fk
          ON fk.table_schema = c.table_schema
         AND fk.table_name = c.table_name
         AND fk.column_name = c.column_name
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND t.table_type = 'BASE TABLE'
          AND (c.column_name = %s OR c.column_name ILIKE %s ESCAPE '\\')
          AND NOT (c.table_name = %s AND c.column_name = %s)
          AND fk.column_name IS NULL
        ORDER BY c.table_schema, c.table_name, c.column_name
        """,
        (TABLE_ROLE, ROLE_PK[0], ROLE_PK[0], "%\\_role_id", TABLE_ROLE, ROLE_PK[0]),
    )
    refs: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        cur.execute(
            sql.SQL("SELECT count(*) AS n FROM {t} WHERE {c}::text = %s").format(
                t=sql.Identifier(row["table_schema"], row["table_name"]),
                c=sql.Identifier(row["column_name"]),
            ),
            (str(role_id),),
        )
        count = int(cur.fetchone()["n"] or 0)
        if count:
            refs.append({
                "table_schema": row["table_schema"],
                "table": f"{row['table_schema']}.{row['table_name']}",
                "column": row["column_name"],
                "count": count,
            })
    return refs


def _hr_role_delete_dependencies(cur, role_id: int) -> Dict[str, Any]:
    cur.execute(f"SELECT count(*) AS n FROM {TABLE_PRA} WHERE role_id = %s", (role_id,))
    assignment_count = int(cur.fetchone()["n"] or 0)
    cur.execute(
        f"""
        SELECT a.assignment_id, a.people_id, p.first_name, p.name AS last_name,
               a.unit_id, u.unit_name, a.assignment_status, a.is_primary,
               a.start_date, a.end_date
        FROM {TABLE_PRA} a
        JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
        LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
        WHERE a.role_id = %s
        ORDER BY a.assignment_id
        LIMIT %s
        """,
        (role_id, HR_DELETE_DEPENDENCY_ROW_LIMIT),
    )
    assignment_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(f"SELECT count(*) AS n FROM {TABLE_ROLE} WHERE parent_role_id = %s", (role_id,))
    child_count = int(cur.fetchone()["n"] or 0)
    cur.execute(
        f"""
        SELECT role_id, role_name, is_active, parent_role_id
        FROM {TABLE_ROLE}
        WHERE parent_role_id = %s
        ORDER BY is_active DESC NULLS LAST, role_name
        LIMIT %s
        """,
        (role_id, HR_DELETE_DEPENDENCY_ROW_LIMIT),
    )
    child_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        f"SELECT count(*) AS n FROM {TABLE_ROLE} WHERE functional_report_role_id = %s",
        (role_id,),
    )
    functional_count = int(cur.fetchone()["n"] or 0)
    cur.execute(
        f"""
        SELECT role_id, role_name, is_active, functional_report_role_id
        FROM {TABLE_ROLE}
        WHERE functional_report_role_id = %s
        ORDER BY is_active DESC NULLS LAST, role_name
        LIMIT %s
        """,
        (role_id, HR_DELETE_DEPENDENCY_ROW_LIMIT),
    )
    functional_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(f"SELECT count(*) AS n FROM {TABLE_KPI_ALLOCATION} WHERE role_id = %s", (role_id,))
    allocation_count = int(cur.fetchone()["n"] or 0)
    cur.execute(
        f"""
        SELECT a.kpi_target_allocation_id, a.kpi_id, k.kpi_name,
               a.target_status, a.kpi_type, a.plant_id, a.unit_id,
               a.zone_id, a.market_id
        FROM {TABLE_KPI_ALLOCATION} a
        LEFT JOIN {TABLE_KPI} k ON k.kpi_id = a.kpi_id
        WHERE a.role_id = %s
        ORDER BY a.kpi_target_allocation_id
        LIMIT %s
        """,
        (role_id, HR_DELETE_DEPENDENCY_ROW_LIMIT),
    )
    allocation_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(f"SELECT count(*) AS n FROM {TABLE_KPI} WHERE owner_role_id = %s", (role_id,))
    owned_kpi_count = int(cur.fetchone()["n"] or 0)
    cur.execute(
        f"""
        SELECT kpi_id, kpi_name, status
        FROM {TABLE_KPI}
        WHERE owner_role_id = %s
        ORDER BY kpi_name
        LIMIT %s
        """,
        (role_id, HR_DELETE_DEPENDENCY_ROW_LIMIT),
    )
    owned_kpi_rows = [dict(r) for r in cur.fetchall()]

    return {
        "assignments": _hr_limited_rows_payload(assignment_count, assignment_rows),
        "child_roles": _hr_limited_rows_payload(child_count, child_rows),
        "functional_role_references": _hr_limited_rows_payload(functional_count, functional_rows),
        "kpi_target_allocations": _hr_limited_rows_payload(allocation_count, allocation_rows),
        "owned_kpis": _hr_limited_rows_payload(owned_kpi_count, owned_kpi_rows),
        "foreign_key_references": _hr_role_foreign_key_references(cur, role_id),
        "non_fk_role_references": _hr_non_fk_role_references(cur, role_id),
    }


def _hr_role_can_delete(dependencies: Dict[str, Any]) -> bool:
    for key, value in dependencies.items():
        if key in {"foreign_key_references", "non_fk_role_references"}:
            if value:
                return False
        elif int(value.get("count") or 0) > 0:
            return False
    return True


def _hr_delete_role_guidance(dependencies: Dict[str, Any]) -> List[str]:
    guidance: List[str] = []
    assignments = dependencies.get("assignments", {})
    if int(assignments.get("count") or 0) > 0:
        guidance.append(
            "Des affectations actives ou historiques existent : ne pas supprimer ce rôle ; "
            "utiliser normalement deactivate_role pour conserver l'historique RH."
        )
    if int(dependencies.get("child_roles", {}).get("count") or 0) > 0:
        guidance.append("Des rôles enfants existent : les déplacer ou restructurer explicitement d'abord.")
    if int(dependencies.get("functional_role_references", {}).get("count") or 0) > 0:
        guidance.append("Des rôles pointent vers ce rôle en reporting fonctionnel : les mettre à jour d'abord.")
    if int(dependencies.get("kpi_target_allocations", {}).get("count") or 0) > 0:
        guidance.append("Des allocations KPI existent : les revoir manuellement avant toute suppression.")
    if int(dependencies.get("owned_kpis", {}).get("count") or 0) > 0:
        guidance.append("Des KPI ont ce rôle comme owner_role_id : changer le propriétaire KPI d'abord.")
    if dependencies.get("foreign_key_references"):
        guidance.append("D'autres contraintes FK référencent ce rôle : nettoyer ces dépendances explicitement.")
    if dependencies.get("non_fk_role_references"):
        guidance.append("Des colonnes role_id sans FK référencent ce rôle : vérifier ces références métier.")
    return guidance


def _hr_delete_role_blocked_response(role: Dict[str, Any], dependencies: Dict[str, Any]) -> Dict[str, Any]:
    resp = error_response("Le rôle ne peut pas être supprimé définitivement car il est encore référencé.")
    resp["data"] = json_safe({
        "role": role,
        "can_delete": False,
        "dependencies": dependencies,
        "suggested_actions": _hr_delete_role_guidance(dependencies),
    })
    return resp


# ============================================================================
# 1) ARBORESCENCE DES RÔLES
# ============================================================================

@mcp.tool()
def create_role(
    role_name: str,
    role_level: Optional[str] = None,
    parent_role_id: Optional[int] = None,
    role_family: Optional[str] = None,
    grade: Optional[str] = None,
    ceo_level: Optional[str] = None,
    scope: Optional[str] = None,
    is_managerial: bool = False,
    role_definition: Optional[str] = None,
    main_responsibilities: Optional[str] = None,
    expected_knowledge: Optional[str] = None,
    expectation: Optional[str] = None,
    function_definition: Optional[str] = None,
    decision_level: Optional[str] = None,
    functional_report_role_id: Optional[int] = None,
    normalized_role_name: Optional[str] = None,
    role_slug: Optional[str] = None,
    valid_from: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Crée un poste (rôle) dans l'arborescence des rôles de KPI_DB_Final.

    role_name doit être UNIQUE dans toute l'entreprise (contrainte uq_role_name)
    — un même intitulé ne se duplique pas par usine : c'est l'affectation
    (unit_id) qui porte l'usine, pas le rôle.

    role_level (facultatif, CHECK base) : Executive | VP | Director | Manager |
      Supervisor | Lead | Senior | Professional | Operational | Coordinator | Assistant
      Le CHECK de la base passe sur NULL et 17 des 71 rôles actifs n'en ont pas :
      l'outil l'accepte donc vide, mais le signale dans data_quality_note.
    grade      : G1..G7        ceo_level : VP | Director | Senior Manager |
      Manager / Supervisor | Specialist / Engineer | Technician / Admin. |
      Operator / Assistant
    scope      : Groupe | Usine

    parent_role_id place le rôle dans l'arbre (v_role_tree). Si role_family
    n'est pas fourni, il est hérité du parent (convention observée en base).
    normalized_role_name et role_slug sont dérivés automatiquement de role_name
    (aucun trigger ne le fait côté base) ; les passer explicitement pour forcer.

    IMPORTANT : v_people_with_boss filtre sur une liste figée de role_id. Un
    rôle créé ici n'y apparaîtra pas — get_org_path / get_escalation_target /
    get_direct_reports l'ignoreront jusqu'à extension de la vue. Renseigner
    reports_to sur les affectations reste fiable dans tous les cas.
    """
    if not role_name or not role_name.strip():
        return error_response("role_name est obligatoire.")
    guard_error = _hr_reject_guarded_fields(extra_fields, ROLE_CREATE_GUARDED_FIELDS, "extra_fields")
    if guard_error:
        return guard_error
    role_name = re.sub(r"\s+", " ", role_name.strip())
    try:
        _hr_check_enum(role_level, VALID_ROLE_LEVEL, "role_level")
        _hr_check_enum(grade, VALID_ROLE_GRADE, "grade")
        _hr_check_enum(ceo_level, VALID_ROLE_CEO_LEVEL, "ceo_level")
        _hr_check_enum(scope, VALID_ROLE_SCOPE, "scope")
        valid_from_date = _hr_parse_date(valid_from, "valid_from")
    except ValueError as exc:
        return error_response(str(exc))

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT role_id FROM role WHERE role_name = %s", (role_name,))
                clash = cur.fetchone()
                if clash is not None:
                    return error_response(
                        f"Un rôle nommé '{role_name}' existe déjà (role_id={clash['role_id']}). "
                        "role_name est UNIQUE ; utiliser update_role ou choisir un autre intitulé."
                    )

                norm = _hr_normalize_role_name(normalized_role_name or role_name)
                cur.execute(
                    "SELECT role_id, role_name FROM role WHERE normalized_role_name = %s",
                    (norm,),
                )
                norm_clash = cur.fetchone()
                if norm_clash is not None:
                    return error_response(
                        f"normalized_role_name '{norm}' est déjà pris par le rôle "
                        f"'{norm_clash['role_name']}' (role_id={norm_clash['role_id']}). "
                        "Contrainte uq_normalized_role_name."
                    )

                parent: Optional[Dict[str, Any]] = None
                if parent_role_id is not None:
                    parent = _hr_get_role(cur, parent_role_id)
                    if parent is None:
                        return error_response(f"parent_role_id={parent_role_id} n'existe pas dans role.")
                    if not parent.get("is_active"):
                        return error_response(
                            f"parent_role_id={parent_role_id} ('{parent['role_name']}') est inactif. "
                            "v_role_tree ne parcourt que les rôles actifs : le nouveau rôle serait "
                            "invisible dans l'arborescence."
                        )

                if functional_report_role_id is not None and not _row_exists(
                    cur, TABLE_ROLE, "role_id", functional_report_role_id
                ):
                    return error_response(
                        f"functional_report_role_id={functional_report_role_id} n'existe pas dans role."
                    )

                slug = _hr_unique_role_slug(cur, role_slug or _hr_slugify_role(role_name))
                family = role_family if role_family is not None else (parent or {}).get("role_family")

                payload = merge_create_payload({
                    "role_name": role_name,
                    "normalized_role_name": norm,
                    "role_slug": slug,
                    "parent_role_id": parent_role_id,
                    "role_family": family,
                    "role_level": role_level,
                    "grade": grade,
                    "ceo_level": ceo_level,
                    "scope": scope,
                    "is_managerial": bool(is_managerial),
                    "is_active": True,
                    "role_definition": role_definition,
                    "main_responsibilities": main_responsibilities,
                    "expected_knowledge": expected_knowledge,
                    "expectation": expectation,
                    "function_definition": function_definition,
                    "decision_level": decision_level,
                    "functional_report_role_id": functional_report_role_id,
                    "valid_from": valid_from_date,
                }, extra_fields)

            row = insert_row(conn, TABLE_ROLE, payload)

            with conn.cursor() as cur:
                created = _hr_get_role(cur, row["role_id"]) or row

        return success_response(
            {
                "role": created,
                "derived": {"normalized_role_name": norm, "role_slug": slug,
                            "role_family_inherited_from_parent": role_family is None and family is not None},
                "data_quality_note": (
                    None if role_level else
                    "role_level laissé vide : le rôle n'aura pas de niveau hiérarchique "
                    "exploitable dans les filtres (list_roles, search_member). À renseigner "
                    "via update_role dès que le niveau est arbitré."
                ),
                "org_chart_note": (
                    "Rôle absent de la liste figée de v_people_with_boss : renseigner reports_to "
                    "sur les affectations pour que la hiérarchie soit exploitable."
                ),
            },
            f"Rôle '{role_name}' créé (role_id={row['role_id']}).",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("create_role failed")
        return error_response("Échec de la création du rôle.", exc)


@mcp.tool()
def update_role(
    role_id: int,
    fields: Optional[Dict[str, Any]] = None,
    role_name: Optional[str] = None,
    parent_role_id: Optional[int] = None,
    role_level: Optional[str] = None,
    role_family: Optional[str] = None,
    grade: Optional[str] = None,
    ceo_level: Optional[str] = None,
    scope: Optional[str] = None,
    is_managerial: Optional[bool] = None,
    role_definition: Optional[str] = None,
    main_responsibilities: Optional[str] = None,
    expected_knowledge: Optional[str] = None,
    expectation: Optional[str] = None,
    function_definition: Optional[str] = None,
    decision_level: Optional[str] = None,
    functional_report_role_id: Optional[int] = None,
    resync_normalized_name: bool = True,
    resync_slug: bool = False,
) -> Dict[str, Any]:
    """
    Met à jour un rôle. Ne passer que les champs à changer.

    Renommage : si role_name change et resync_normalized_name=True (défaut),
    normalized_role_name est recalculé (unicité vérifiée avant écriture).
    role_slug n'est recalculé que si resync_slug=True — il sert souvent de clé
    stable côté applications, donc il n'est pas touché par défaut.

    Repositionnement dans l'arbre : parent_role_id est validé contre les cycles
    (interdit de rattacher un rôle à lui-même ou à l'un de ses descendants).

    Pour désactiver un rôle, utiliser deactivate_role (qui vérifie les
    titulaires et les rôles enfants) plutôt que is_active via fields.
    """
    guard_error = _hr_reject_guarded_fields(fields, ROLE_UPDATE_GUARDED_FIELDS, "fields")
    if guard_error:
        return guard_error
    try:
        _hr_check_enum(role_level, VALID_ROLE_LEVEL, "role_level")
        _hr_check_enum(grade, VALID_ROLE_GRADE, "grade")
        _hr_check_enum(ceo_level, VALID_ROLE_CEO_LEVEL, "ceo_level")
        _hr_check_enum(scope, VALID_ROLE_SCOPE, "scope")
    except ValueError as exc:
        return error_response(str(exc))

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                current = _hr_get_role(cur, role_id)
                if current is None:
                    return error_response(f"Rôle {role_id} introuvable.")

                new_name = re.sub(r"\s+", " ", role_name.strip()) if role_name else None
                if new_name and new_name != current["role_name"]:
                    cur.execute(
                        "SELECT role_id FROM role WHERE role_name = %s AND role_id <> %s",
                        (new_name, role_id),
                    )
                    if cur.fetchone() is not None:
                        return error_response(f"Un autre rôle porte déjà le nom '{new_name}'.")

                if parent_role_id is not None:
                    if parent_role_id == role_id:
                        return error_response("Un rôle ne peut pas être son propre parent.")
                    parent = _hr_get_role(cur, parent_role_id)
                    if parent is None:
                        return error_response(f"parent_role_id={parent_role_id} n'existe pas.")
                    descendants = _hr_role_subtree_ids(cur, role_id)
                    if parent_role_id in descendants:
                        return error_response(
                            f"Cycle détecté : le rôle {parent_role_id} ('{parent['role_name']}') est "
                            f"un descendant de {role_id} ('{current['role_name']}'). "
                            "Déplacer d'abord la branche enfant."
                        )
                    if not parent.get("is_active"):
                        return error_response(
                            f"parent_role_id={parent_role_id} est inactif : la branche sortirait "
                            "de v_role_tree."
                        )

                if functional_report_role_id is not None and not _row_exists(
                    cur, TABLE_ROLE, "role_id", functional_report_role_id
                ):
                    return error_response(
                        f"functional_report_role_id={functional_report_role_id} n'existe pas."
                    )

                known: Dict[str, Any] = {
                    "role_name": new_name,
                    "parent_role_id": parent_role_id,
                    "role_level": role_level,
                    "role_family": role_family,
                    "grade": grade,
                    "ceo_level": ceo_level,
                    "scope": scope,
                    "is_managerial": is_managerial,
                    "role_definition": role_definition,
                    "main_responsibilities": main_responsibilities,
                    "expected_knowledge": expected_knowledge,
                    "expectation": expectation,
                    "function_definition": function_definition,
                    "decision_level": decision_level,
                    "functional_report_role_id": functional_report_role_id,
                }

                if new_name and resync_normalized_name:
                    norm = _hr_normalize_role_name(new_name)
                    cur.execute(
                        "SELECT role_id, role_name FROM role WHERE normalized_role_name = %s AND role_id <> %s",
                        (norm, role_id),
                    )
                    clash = cur.fetchone()
                    if clash is not None:
                        return error_response(
                            f"normalized_role_name '{norm}' est déjà pris par "
                            f"'{clash['role_name']}' (role_id={clash['role_id']})."
                        )
                    known["normalized_role_name"] = norm
                if new_name and resync_slug:
                    known["role_slug"] = _hr_unique_role_slug(
                        cur, _hr_slugify_role(new_name), exclude_role_id=role_id
                    )

                payload = merge_update_payload(known, fields)

            row = update_row(conn, TABLE_ROLE, ROLE_PK, role_id, payload, touch_updated_at=True)
            if row is None:
                return error_response(f"Rôle {role_id} introuvable.")

            with conn.cursor() as cur:
                refreshed = _hr_get_role(cur, role_id) or row

        return success_response(
            {"role": refreshed, "changed_fields": sorted(payload.keys())},
            "Rôle mis à jour.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("update_role failed")
        return error_response("Échec de la mise à jour du rôle.", exc)


@mcp.tool()
def deactivate_role(
    role_id: int,
    valid_to: Optional[str] = None,
    reassign_children_to_parent: bool = False,
    force_with_active_holders: bool = False,
) -> Dict[str, Any]:
    """
    Désactive un rôle (is_active=false + valid_to). Aucune suppression : le rôle
    reste référencé par l'historique des affectations et par les KPI.

    Garde-fous, parce que v_role_tree ne parcourt que les rôles actifs :
      - REFUSE s'il reste des titulaires ACTIFS (sauf force_with_active_holders=True,
        qui laisse les affectations en place : à traiter avec change_person_role
        ou end_role_assignment).
      - REFUSE s'il reste des rôles enfants actifs, sinon toute la sous-branche
        disparaît de l'arborescence. reassign_children_to_parent=True rattache
        d'abord les enfants au parent du rôle désactivé.

    Signale aussi les allocations KPI (kpi_target_allocation.role_id) et les KPI
    dont il est owner_role_id, qui resteront pointées sur un rôle inactif.
    """
    try:
        valid_to_date = _hr_parse_date(valid_to, "valid_to", default_today=True)
    except ValueError as exc:
        return error_response(str(exc))

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                current = _hr_get_role(cur, role_id)
                if current is None:
                    return error_response(f"Rôle {role_id} introuvable.")

                cur.execute(
                    f"""
                    SELECT a.assignment_id, a.people_id, p.first_name, p.name AS last_name,
                           a.unit_id, u.unit_name, a.is_primary
                    FROM {TABLE_PRA} a
                    JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
                    LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
                    WHERE a.role_id = %s AND upper(a.assignment_status) = 'ACTIVE'
                    ORDER BY p.name, p.first_name
                    """,
                    (role_id,),
                )
                holders = [dict(r) for r in cur.fetchall()]
                if holders and not force_with_active_holders:
                    resp = error_response(
                        f"{len(holders)} titulaire(s) ACTIF(s) sur ce rôle. Les réaffecter "
                        "(change_person_role) ou clore leurs affectations (end_role_assignment) "
                        "avant, ou passer force_with_active_holders=True."
                    )
                    resp["active_holders"] = json_safe(holders)
                    return resp

                cur.execute(
                    "SELECT role_id, role_name FROM role WHERE parent_role_id = %s AND is_active = TRUE ORDER BY role_name",
                    (role_id,),
                )
                children = [dict(r) for r in cur.fetchall()]
                reparented: List[Dict[str, Any]] = []
                if children:
                    if not reassign_children_to_parent:
                        resp = error_response(
                            f"{len(children)} rôle(s) enfant(s) actif(s). Les désactiver d'abord, "
                            "ou passer reassign_children_to_parent=True pour les rattacher au parent "
                            f"({current.get('parent_role_id')}). Sinon toute la sous-branche sort de v_role_tree."
                        )
                        resp["active_children"] = json_safe(children)
                        return resp
                    cur.execute(
                        "UPDATE role SET parent_role_id = %s, updated_at = now() "
                        "WHERE parent_role_id = %s AND is_active = TRUE RETURNING role_id, role_name",
                        (current.get("parent_role_id"), role_id),
                    )
                    reparented = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT
                      (SELECT count(*) FROM {TABLE_KPI_ALLOCATION} WHERE role_id = %s) AS kpi_allocations,
                      (SELECT count(*) FROM {TABLE_KPI} WHERE owner_role_id = %s)      AS owned_kpis,
                      (SELECT count(*) FROM {TABLE_PRA}
                        WHERE role_id = %s AND upper(assignment_status) <> 'ACTIVE')    AS historical_assignments
                    """,
                    (role_id, role_id, role_id),
                )
                impact = dict(cur.fetchone())

                cur.execute(
                    "UPDATE role SET is_active = FALSE, valid_to = %s, updated_at = now() "
                    "WHERE role_id = %s RETURNING *",
                    (valid_to_date, role_id),
                )
                row = dict(cur.fetchone())

        return success_response(
            {
                "role": row,
                "reparented_children": reparented,
                "active_holders_left_in_place": holders if force_with_active_holders else [],
                "impact": impact,
            },
            f"Rôle '{current['role_name']}' désactivé au {valid_to_date.isoformat()}.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("deactivate_role failed")
        return error_response("Échec de la désactivation du rôle.", exc)


@mcp.tool()
def delete_role(
    role_id: int,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Supprime définitivement un rôle, uniquement s'il a été créé par erreur et
    n'a jamais été utilisé. Pour le cycle de vie RH normal et la conservation de
    l'historique, utiliser deactivate_role.

    confirm=False ne supprime rien : l'outil retourne l'analyse complète des
    dépendances et indique si la suppression serait possible. confirm=True est
    requis pour exécuter le DELETE, et celui-ci ne supprime qu'une seule ligne
    de role, sans cascade ni nettoyage automatique.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                role = _hr_get_role(cur, role_id)
                if role is None:
                    return error_response(f"Rôle {role_id} introuvable.")

                if confirm:
                    cur.execute(
                        sql.SQL("SELECT role_id FROM {t} WHERE role_id = %s FOR UPDATE").format(
                            t=sql.Identifier(TABLE_ROLE)
                        ),
                        (role_id,),
                    )
                    if cur.fetchone() is None:
                        return error_response(f"Rôle {role_id} introuvable.")
                    role = _hr_get_role(cur, role_id) or role

                dependencies = _hr_role_delete_dependencies(cur, role_id)
                can_delete = _hr_role_can_delete(dependencies)
                data = {
                    "role": role,
                    "can_delete": can_delete,
                    "dependencies": dependencies,
                }

                if not can_delete:
                    return _hr_delete_role_blocked_response(role, dependencies)

                if not confirm:
                    return success_response(
                        data,
                        "Le rôle est inutilisé et peut être supprimé définitivement. "
                        "Relancer delete_role avec confirm=true.",
                    )

                cur.execute(
                    sql.SQL("DELETE FROM {t} WHERE role_id = %s RETURNING *").format(
                        t=sql.Identifier(TABLE_ROLE)
                    ),
                    (role_id,),
                )
                deleted = cur.fetchone()
                if deleted is None:
                    return error_response(f"Rôle {role_id} introuvable.")

        return success_response(
            {"deleted_role": dict(deleted)},
            f"Rôle '{role['role_name']}' supprimé définitivement.",
        )
    except errors.ForeignKeyViolation as exc:
        logger.exception("delete_role failed")
        return error_response(
            "Le rôle ne peut pas être supprimé définitivement car une contrainte "
            "de clé étrangère le référence encore.",
            exc,
        )
    except Exception as exc:
        logger.exception("delete_role failed")
        return error_response("Échec de la suppression définitive du rôle.", exc)


@mcp.tool()
def get_role_tree(
    root_role_id: Optional[int] = None,
    max_depth: Optional[int] = None,
    include_holder_counts: bool = True,
    include_vacant_only: bool = False,
) -> Dict[str, Any]:
    """
    Renvoie l'arborescence des rôles (v_role_tree) à plat, triée par chemin
    hiérarchique, avec la profondeur et le nombre de titulaires ACTIFS par rôle.

    root_role_id limite au sous-arbre d'un rôle (lui inclus) ; max_depth borne
    la profondeur relative à cette racine. include_vacant_only=True ne garde que
    les rôles sans titulaire actif — la vue "postes à pourvoir".
    Ne parcourt que les rôles actifs (comportement de v_role_tree).
    """
    if max_depth is not None and (max_depth < 0 or max_depth > HR_MAX_ROLE_DEPTH):
        return error_response(f"max_depth doit être entre 0 et {HR_MAX_ROLE_DEPTH}.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if root_role_id is not None:
            conditions.append("%s = ANY(t.id_path)")
            params.append(root_role_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if root_role_id is not None and not _row_exists(cur, TABLE_ROLE, "role_id", root_role_id):
                    return error_response(f"root_role_id={root_role_id} n'existe pas.")

                holders_select = ", 0 AS active_holder_count"
                if include_holder_counts:
                    holders_select = f""",
                       (SELECT count(DISTINCT a.people_id) FROM {TABLE_PRA} a
                         WHERE a.role_id = t.role_id
                           AND upper(a.assignment_status) = 'ACTIVE') AS active_holder_count"""

                cur.execute(
                    f"""
                    SELECT t.role_id, t.parent_role_id, t.role_name, t.role_slug,
                           t.role_level, t.role_family, t.is_managerial,
                           t.depth, t.full_path, t.id_path, t.is_leaf,
                           r.grade, r.ceo_level, r.scope
                           {holders_select}
                    FROM v_role_tree t
                    JOIN {TABLE_ROLE} r ON r.role_id = t.role_id
                    {where}
                    ORDER BY t.full_path
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]

        if root_role_id is not None:
            base = next((r["depth"] for r in rows if r["role_id"] == root_role_id), 0)
            for r in rows:
                r["relative_depth"] = r["depth"] - base
            if max_depth is not None:
                rows = [r for r in rows if r["relative_depth"] <= max_depth]
        elif max_depth is not None:
            rows = [r for r in rows if r["depth"] <= max_depth]

        vacant = [r for r in rows if not r.get("active_holder_count")]
        if include_vacant_only:
            rows = vacant

        return success_response(
            rows,
            count=len(rows),
            vacant_role_count=len(vacant),
            root_role_id=root_role_id,
        )
    except Exception as exc:
        logger.exception("get_role_tree failed")
        return error_response("Échec de la lecture de l'arborescence des rôles.", exc)


# ============================================================================
# 2) PERSONNES
# ============================================================================

@mcp.tool()
def create_person(
    first_name: str,
    name: str,
    work_at_unit_id: int,
    paid_by_unit_id: Optional[int] = None,
    email: Optional[str] = None,
    status: str = "Active",
    hr_employee_id: Optional[str] = None,
    hr_site: Optional[str] = None,
    gender: Optional[str] = None,
    hire_date: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    cell_phone: Optional[str] = None,
    native_language: Optional[str] = None,
    other_languages: Optional[str] = None,
    seniority: Optional[int] = None,
    other_information: Optional[str] = None,
    comments: Optional[str] = None,
    role_id: Optional[int] = None,
    assignment_unit_id: Optional[int] = None,
    assignment_no_unit: bool = False,
    assignment_type: str = "PERMANENT",
    allocation_percentage: Optional[float] = None,
    reports_to: Optional[int] = None,
    auto_resolve_reports_to: bool = True,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
    assignment_start_date: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Crée une personne dans KPI_DB_Final et, en OPTION, sa première affectation
    de poste dans la MÊME transaction (embauche complète en un appel).

    Obligatoires : first_name, name (= nom de famille), work_at_unit_id.
    paid_by_unit_id est NOT NULL en base : s'il est omis, il reprend
    work_at_unit_id. status : Active | Inactive. gender : Male | Female.
    email est UNIQUE mais optionnel (la majorité des opérateurs n'en ont pas).

    Affectation initiale : fournir role_id. assignment_unit_id vaut
    work_at_unit_id par défaut ; assignment_no_unit=True force unit_id à NULL
    pour un rôle de scope Groupe sans rattachement usine. L'affectation est créée is_primary=true et
    ACTIVE, avec target_role_label = role_name (convention observée).
    reports_to attend un assignment_id de manager ; s'il est omis et que
    auto_resolve_reports_to=True, il est déduit du titulaire actif du rôle
    parent dans la même unité (ou l'unité Groupe) — laissé vide si ambigu, la
    réponse listant alors les candidats.
    """
    if not first_name or not first_name.strip():
        return error_response("first_name est obligatoire.")
    if not name or not name.strip():
        return error_response("name (nom de famille) est obligatoire.")
    guard_error = _hr_reject_guarded_fields(extra_fields, PEOPLE_CREATE_GUARDED_FIELDS, "extra_fields")
    if guard_error:
        return guard_error
    try:
        _hr_check_enum(status, VALID_PEOPLE_STATUS, "status")
        _hr_check_enum(gender, VALID_GENDER, "gender")
        _hr_check_enum(assignment_type, VALID_ASSIGNMENT_TYPES_HR, "assignment_type")
        hire = _hr_parse_date(hire_date, "hire_date")
        dob = _hr_parse_date(date_of_birth, "date_of_birth")
        start = _hr_parse_date(assignment_start_date, "assignment_start_date", default_today=True)
    except ValueError as exc:
        return error_response(str(exc))
    if email is not None and "@" not in email:
        return error_response("email invalide.")

    paid_unit = paid_by_unit_id if paid_by_unit_id is not None else work_at_unit_id

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                for unit_value, label in ((work_at_unit_id, "work_at_unit_id"), (paid_unit, "paid_by_unit_id")):
                    if not _row_exists(cur, TABLE_UNIT, "unit_id", unit_value):
                        return error_response(f"{label}={unit_value} n'existe pas dans {TABLE_UNIT}.")

                if email:
                    cur.execute(
                        f"SELECT people_id, first_name, name FROM {TABLE_PEOPLE} WHERE lower(email) = lower(%s)",
                        (email.strip(),),
                    )
                    clash = cur.fetchone()
                    if clash is not None:
                        return error_response(
                            f"L'email '{email}' est déjà utilisé par {clash['first_name']} "
                            f"{clash['name']} (people_id={clash['people_id']}). Contrainte people_email_key."
                        )

                role: Optional[Dict[str, Any]] = None
                if role_id is not None:
                    role = _hr_get_role(cur, role_id)
                    if role is None:
                        return error_response(f"role_id={role_id} n'existe pas dans {TABLE_ROLE}.")
                    if not role.get("is_active"):
                        return error_response(
                            f"role_id={role_id} ('{role['role_name']}') est inactif : "
                            "on n'affecte pas quelqu'un sur un rôle désactivé."
                        )
                    if assignment_no_unit and assignment_unit_id is not None:
                        return error_response(
                            "assignment_no_unit=True et assignment_unit_id sont contradictoires."
                        )
                    ref_err = _hr_validate_reference(
                        cur,
                        unit_id=None if assignment_no_unit else (
                            assignment_unit_id if assignment_unit_id is not None else work_at_unit_id
                        ),
                        zone_id=zone_id, market_id=market_id, product_line_id=product_line_id,
                    )
                    if ref_err:
                        return error_response(ref_err)

                payload = merge_create_payload({
                    "first_name": first_name.strip(),
                    "name": name.strip(),
                    "work_at_unit_id": work_at_unit_id,
                    "paid_by_unit_id": paid_unit,
                    "email": email.strip() if email else None,
                    "status": status,
                    "hr_employee_id": hr_employee_id,
                    "hr_site": hr_site,
                    "gender": gender,
                    "hire_date": hire,
                    "date_of_birth": dob,
                    "cell_phone": cell_phone,
                    "native_language": native_language,
                    "other_languages": other_languages,
                    "seniority": seniority,
                    "other_information": other_information,
                    "comments": comments,
                }, extra_fields)

            person = insert_row(conn, TABLE_PEOPLE, payload)
            people_id = person["people_id"]

            assignment = None
            reports_to_resolution = None
            if role_id is not None and role is not None:
                with conn.cursor() as cur:
                    alloc_unit = None if assignment_no_unit else (
                        assignment_unit_id if assignment_unit_id is not None else work_at_unit_id
                    )
                    resolved_reports_to = reports_to
                    if resolved_reports_to is not None:
                        err = _hr_validate_reports_to(cur, resolved_reports_to, people_id)
                        if err:
                            return error_response(err)
                    elif auto_resolve_reports_to:
                        reports_to_resolution = _hr_resolve_reports_to(
                            cur, role_id, alloc_unit, people_id, zone_id
                        )
                        resolved_reports_to = reports_to_resolution["assignment_id"]

                    assignment = _hr_insert_assignment(cur, {
                        "people_id": people_id,
                        "role_id": role_id,
                        "unit_id": alloc_unit,
                        "zone_id": zone_id,
                        "market_id": market_id,
                        "product_line_id": product_line_id,
                        "assignment_type": assignment_type,
                        "allocation_percentage": allocation_percentage if allocation_percentage is not None else 100,
                        "is_primary": True,
                        "reports_to": resolved_reports_to,
                        "target_role_label": role["role_name"],
                        "start_date": start,
                        "assignment_status": ASSIGNMENT_STATUS_ACTIVE,
                    })

        return success_response(
            {
                "person": person,
                "assignment": assignment,
                "reports_to_resolution": reports_to_resolution,
            },
            f"{first_name.strip()} {name.strip()} créé(e) (people_id={people_id})"
            + (f" et affecté(e) au rôle '{role['role_name']}'." if assignment else " sans affectation."),
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("create_person failed")
        return error_response("Échec de la création de la personne.", exc)


@mcp.tool()
def update_person(
    people_id: int,
    fields: Optional[Dict[str, Any]] = None,
    first_name: Optional[str] = None,
    name: Optional[str] = None,
    email: Optional[str] = None,
    status: Optional[str] = None,
    work_at_unit_id: Optional[int] = None,
    paid_by_unit_id: Optional[int] = None,
    hr_employee_id: Optional[str] = None,
    hr_site: Optional[str] = None,
    gender: Optional[str] = None,
    hire_date: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    cell_phone: Optional[str] = None,
    native_language: Optional[str] = None,
    other_languages: Optional[str] = None,
    seniority: Optional[int] = None,
    other_information: Optional[str] = None,
    comments: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Met à jour la fiche d'une personne (données administratives uniquement).

    Ne touche PAS aux affectations : un changement de poste passe par
    change_person_role, une mobilité d'usine par transfer_person_to_unit.
    work_at_unit_id ne modifie que le rattachement administratif de la fiche —
    l'unité opérationnelle vit dans people_role_assignment.unit_id.

    Pour désactiver quelqu'un, préférer offboard_person : status='Inactive' ici
    laisserait ses affectations ACTIVES, donc la personne resterait titulaire de
    ses KPI et manager de ses équipes.
    """
    guard_error = _hr_reject_guarded_fields(fields, PEOPLE_UPDATE_GUARDED_FIELDS, "fields")
    if guard_error:
        return guard_error
    try:
        _hr_check_enum(status, VALID_PEOPLE_STATUS, "status")
        _hr_check_enum(gender, VALID_GENDER, "gender")
        hire = _hr_parse_date(hire_date, "hire_date")
        dob = _hr_parse_date(date_of_birth, "date_of_birth")
    except ValueError as exc:
        return error_response(str(exc))
    if email is not None and "@" not in email:
        return error_response("email invalide.")

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                current = _hr_get_person(cur, people_id)
                if current is None:
                    return error_response(f"Personne {people_id} introuvable.")

                if email:
                    cur.execute(
                        f"SELECT people_id, first_name, name FROM {TABLE_PEOPLE} "
                        "WHERE lower(email) = lower(%s) AND people_id <> %s",
                        (email.strip(), people_id),
                    )
                    clash = cur.fetchone()
                    if clash is not None:
                        return error_response(
                            f"L'email '{email}' appartient déjà à {clash['first_name']} "
                            f"{clash['name']} (people_id={clash['people_id']})."
                        )

                for unit_value, label in ((work_at_unit_id, "work_at_unit_id"),
                                          (paid_by_unit_id, "paid_by_unit_id")):
                    if unit_value is not None and not _row_exists(cur, TABLE_UNIT, "unit_id", unit_value):
                        return error_response(f"{label}={unit_value} n'existe pas dans {TABLE_UNIT}.")

                payload = merge_update_payload({
                    "first_name": first_name.strip() if first_name else None,
                    "name": name.strip() if name else None,
                    "email": email.strip() if email else None,
                    "status": status,
                    "work_at_unit_id": work_at_unit_id,
                    "paid_by_unit_id": paid_by_unit_id,
                    "hr_employee_id": hr_employee_id,
                    "hr_site": hr_site,
                    "gender": gender,
                    "hire_date": hire,
                    "date_of_birth": dob,
                    "cell_phone": cell_phone,
                    "native_language": native_language,
                    "other_languages": other_languages,
                    "seniority": seniority,
                    "other_information": other_information,
                    "comments": comments,
                }, fields)

            row = update_row(conn, TABLE_PEOPLE, PEOPLE_PK, people_id, payload)
            if row is None:
                return error_response(f"Personne {people_id} introuvable.")

            warning = None
            if payload.get("status") == "Inactive":
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT count(*) AS n FROM {TABLE_PRA} "
                        "WHERE people_id = %s AND upper(assignment_status) = 'ACTIVE'",
                        (people_id,),
                    )
                    n = cur.fetchone()["n"]
                if n:
                    warning = (
                        f"status passé à Inactive mais {n} affectation(s) restent ACTIVES : "
                        "utiliser offboard_person pour clore aussi les affectations."
                    )

        return success_response(
            {"person": row, "changed_fields": sorted(payload.keys()), "warning": warning},
            "Fiche mise à jour.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("update_person failed")
        return error_response("Échec de la mise à jour de la personne.", exc)


@mcp.tool()
def offboard_person(
    people_id: int,
    departure_date: Optional[str] = None,
    reason: Optional[str] = None,
    reassign_reports_to_assignment_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Sortie d'un collaborateur, en une transaction :
      1. toutes ses affectations ACTIVES passent ENDED avec end_date = departure_date
         (l'historique est conservé : la table d'affectation EST l'historique RH) ;
      2. people.status passe à 'Inactive' ;
      3. la raison est ajoutée à people.comments, horodatée.

    Si la personne encadrait des équipes, les affectations qui pointaient sur
    elle via reports_to sont signalées. reassign_reports_to_assignment_id permet
    de les rebasculer sur le manager de remplacement dans la même transaction ;
    sinon elles restent orphelines et v_people_with_boss retombera sur la
    déduction par rôle parent.

    Signale également les allocations KPI et les affectations dont elle est
    set_by/approved_by, qui continueront de la référencer.
    """
    try:
        departure = _hr_parse_date(departure_date, "departure_date", default_today=True)
    except ValueError as exc:
        return error_response(str(exc))

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                person = _hr_get_person(cur, people_id)
                if person is None:
                    return error_response(f"Personne {people_id} introuvable.")

                cur.execute(
                    f"""
                    SELECT a.assignment_id, a.role_id, r.role_name, a.unit_id, u.unit_name,
                           a.is_primary, a.start_date, a.allocation_percentage
                    FROM {TABLE_PRA} a
                    LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
                    LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
                    WHERE a.people_id = %s AND upper(a.assignment_status) = 'ACTIVE'
                    ORDER BY a.is_primary DESC, a.assignment_id
                    """,
                    (people_id,),
                )
                active = [dict(r) for r in cur.fetchall()]
                active_ids = [a["assignment_id"] for a in active]

                # Qui reportait à cette personne ?
                cur.execute(
                    f"""
                    SELECT a.assignment_id, a.people_id, p.first_name, p.name AS last_name,
                           r.role_name, a.unit_id, u.unit_name, a.reports_to
                    FROM {TABLE_PRA} a
                    JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
                    LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
                    LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
                    WHERE upper(a.assignment_status) = 'ACTIVE'
                      AND a.reports_to = ANY(%s)
                    ORDER BY p.name, p.first_name
                    """,
                    (active_ids or [-1],),
                )
                reports = [dict(r) for r in cur.fetchall()]

                reassigned: List[int] = []
                if reports and reassign_reports_to_assignment_id is not None:
                    if reassign_reports_to_assignment_id in active_ids:
                        return error_response(
                            "reassign_reports_to_assignment_id est une affectation de la personne "
                            "qui part : choisir l'affectation du manager de remplacement."
                        )
                    new_boss = _hr_get_assignment(cur, reassign_reports_to_assignment_id)
                    if new_boss is None:
                        return error_response(
                            f"reassign_reports_to_assignment_id={reassign_reports_to_assignment_id} n'existe pas."
                        )
                    if str(new_boss["assignment_status"]).upper() != "ACTIVE":
                        return error_response(
                            "Le manager de remplacement doit avoir une affectation ACTIVE."
                        )
                    cur.execute(
                        f"""
                        UPDATE {TABLE_PRA}
                        SET reports_to = %s, updated_at = now()
                        WHERE upper(assignment_status) = 'ACTIVE' AND reports_to = ANY(%s)
                        RETURNING assignment_id
                        """,
                        (reassign_reports_to_assignment_id, active_ids or [-1]),
                    )
                    reassigned = [r["assignment_id"] for r in cur.fetchall()]

                closed: List[Dict[str, Any]] = []
                for a in active:
                    row = _hr_close_assignment(cur, a["assignment_id"], departure)
                    if row:
                        closed.append(row)

                new_comments = person.get("comments") or ""
                if reason:
                    stamp = departure.isoformat()
                    new_comments = (new_comments + "\n" if new_comments else "") + \
                                   f"[sortie {stamp}] {reason}"

                cur.execute(
                    f"""
                    UPDATE {TABLE_PEOPLE}
                    SET status = 'Inactive', comments = %s
                    WHERE people_id = %s
                    RETURNING *
                    """,
                    (new_comments or None, people_id),
                )
                updated_person = dict(cur.fetchone())

                cur.execute(
                    f"""
                    SELECT
                      (SELECT count(*) FROM {TABLE_KPI_ALLOCATION}
                        WHERE set_by_people_id = %s OR approved_by_people_id = %s) AS kpi_allocations_referencing,
                      (SELECT count(*) FROM {TABLE_UNIT} WHERE manager_id = %s)     AS units_managed,
                      (SELECT count(*) FROM {TABLE_CUSTOMER}
                        WHERE main_sales_people_id = %s OR main_kam_people_id = %s) AS customers_referencing
                    """,
                    (people_id, people_id, people_id, people_id, people_id),
                )
                impact = dict(cur.fetchone())

        return success_response(
            {
                "person": updated_person,
                "closed_assignments": closed,
                "closed_count": len(closed),
                "direct_reports_affected": reports,
                "reports_to_reassigned_assignment_ids": reassigned,
                "impact": impact,
            },
            f"{person['first_name']} {person['name']} sorti(e) au {departure.isoformat()} : "
            f"{len(closed)} affectation(s) close(s).",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("offboard_person failed")
        return error_response("Échec de la sortie du collaborateur.", exc)


# ============================================================================
# 3) AFFECTATIONS DE POSTE
# ============================================================================

@mcp.tool()
def assign_role_to_person(
    people_id: int,
    role_id: int,
    unit_id: Optional[int] = None,
    no_unit: bool = False,
    is_primary: bool = True,
    assignment_type: str = "PERMANENT",
    allocation_percentage: Optional[float] = None,
    reports_to: Optional[int] = None,
    auto_resolve_reports_to: bool = True,
    demote_existing_primary: bool = False,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
    functional_manager_role_id: Optional[int] = None,
    functional_manager_assignment_id: Optional[int] = None,
    target_role_label: Optional[str] = None,
    assistant_context: Optional[str] = None,
    knowledge_context: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    AJOUTE une affectation de poste à une personne, sans toucher aux existantes.
    Sert aux cumuls de fonctions (ex. Plant manager 50% + Product line manager 50%)
    et à la première affectation d'une personne déjà créée.

    Pour un CHANGEMENT de poste, utiliser change_person_role : cet outil-ci
    n'ajoute que, il ne clôt rien, et laisserait deux affectations concurrentes.

    Invariant protégé : une seule affectation ACTIVE is_primary=true par
    personne (0 violation aujourd'hui sur 2062 personnes, aucune contrainte SQL
    ne l'impose). Si une principale existe déjà et is_primary=True,
    l'outil REFUSE sauf demote_existing_primary=True, qui la bascule en
    secondaire dans la même transaction.

    reports_to = assignment_id du manager (PAS un people_id). Omis, il est
    déduit du titulaire actif du rôle parent si auto_resolve_reports_to=True.

    unit_id vaut people.work_at_unit_id par défaut. no_unit=True force unit_id
    à NULL, pour les rôles de scope Groupe non rattachés à une usine (5
    affectations ACTIVES de la base sont dans ce cas, ex. Product line manager).

    assignment_type par défaut 'PERMANENT' : c'est la valeur des affectations
    saisies manuellement. Les 1885 lignes issues de l'import de masse ont
    assignment_type NULL — passer explicitement null pour rester sur cette
    convention-là.
    """
    try:
        _hr_check_enum(assignment_type, VALID_ASSIGNMENT_TYPES_HR, "assignment_type")
        start = _hr_parse_date(start_date, "start_date", default_today=True)
        end = _hr_parse_date(end_date, "end_date")
    except ValueError as exc:
        return error_response(str(exc))
    if allocation_percentage is not None and not (0 < float(allocation_percentage) <= 100):
        return error_response("allocation_percentage doit être dans ]0, 100].")
    if end is not None and end < start:
        return error_response("end_date ne peut pas précéder start_date.")

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                person = _hr_get_person(cur, people_id)
                if person is None:
                    return error_response(f"Personne {people_id} introuvable.")
                if str(person.get("status")).lower() != "active":
                    return error_response(
                        f"{person['first_name']} {person['name']} a le statut "
                        f"'{person.get('status')}' : réactiver la fiche (update_person) avant d'affecter."
                    )

                role = _hr_get_role(cur, role_id)
                if role is None:
                    return error_response(f"role_id={role_id} n'existe pas dans {TABLE_ROLE}.")
                if not role.get("is_active"):
                    return error_response(
                        f"role_id={role_id} ('{role['role_name']}') est inactif : "
                        "réactiver le rôle (update_role) ou en choisir un autre."
                    )

                if no_unit and unit_id is not None:
                    return error_response("no_unit=True et unit_id sont contradictoires.")
                target_unit = None if no_unit else (
                    unit_id if unit_id is not None else person.get("work_at_unit_id")
                )
                ref_err = _hr_validate_reference(
                    cur, unit_id=target_unit, zone_id=zone_id,
                    market_id=market_id, product_line_id=product_line_id,
                )
                if ref_err:
                    return error_response(ref_err)

                if functional_manager_role_id is not None and not _row_exists(
                    cur, TABLE_ROLE, "role_id", functional_manager_role_id
                ):
                    return error_response(
                        f"functional_manager_role_id={functional_manager_role_id} n'existe pas."
                    )
                if functional_manager_assignment_id is not None:
                    err = _hr_validate_reports_to(cur, functional_manager_assignment_id, people_id)
                    if err:
                        return error_response("functional_manager_assignment_id : " + err)

                # Doublon exact déjà actif ?
                cur.execute(
                    f"""
                    SELECT assignment_id FROM {TABLE_PRA}
                    WHERE people_id = %s AND role_id = %s
                      AND (unit_id = %s OR (unit_id IS NULL AND %s::bigint IS NULL))
                      AND upper(assignment_status) = 'ACTIVE'
                    """,
                    (people_id, role_id, target_unit, target_unit),
                )
                dup = cur.fetchone()
                if dup is not None:
                    return error_response(
                        f"Affectation déjà ACTIVE pour ce couple rôle/unité "
                        f"(assignment_id={dup['assignment_id']}). Utiliser update_role_assignment "
                        "ou change_person_role."
                    )

                demoted = None
                if is_primary:
                    existing = _hr_active_primary(cur, people_id)
                    if existing is not None:
                        if not demote_existing_primary:
                            resp = error_response(
                                f"{person['first_name']} {person['name']} a déjà une affectation "
                                f"principale ACTIVE : '{existing.get('role_name')}' "
                                f"(assignment_id={existing['assignment_id']}). Passer is_primary=False "
                                "pour un cumul, demote_existing_primary=True pour la rétrograder, "
                                "ou change_person_role pour un vrai changement de poste."
                            )
                            resp["existing_primary"] = json_safe(existing)
                            return resp
                        cur.execute(
                            f"UPDATE {TABLE_PRA} SET is_primary = FALSE, updated_at = now() "
                            "WHERE assignment_id = %s RETURNING assignment_id, role_id",
                            (existing["assignment_id"],),
                        )
                        demoted = dict(cur.fetchone())

                resolved_reports_to = reports_to
                reports_to_resolution = None
                if resolved_reports_to is not None:
                    err = _hr_validate_reports_to(cur, resolved_reports_to, people_id)
                    if err:
                        return error_response(err)
                elif auto_resolve_reports_to:
                    reports_to_resolution = _hr_resolve_reports_to(
                        cur, role_id, target_unit, people_id, zone_id
                    )
                    resolved_reports_to = reports_to_resolution["assignment_id"]

                assignment = _hr_insert_assignment(cur, {
                    "people_id": people_id,
                    "role_id": role_id,
                    "unit_id": target_unit,
                    "zone_id": zone_id,
                    "market_id": market_id,
                    "product_line_id": product_line_id,
                    "assignment_type": assignment_type,
                    "allocation_percentage": allocation_percentage if allocation_percentage is not None else 100,
                    "is_primary": bool(is_primary),
                    "reports_to": resolved_reports_to,
                    "functional_manager_role_id": functional_manager_role_id,
                    "functional_manager_assignment_id": functional_manager_assignment_id,
                    "target_role_label": target_role_label or role["role_name"],
                    "assistant_context": assistant_context,
                    "knowledge_context": knowledge_context,
                    "start_date": start,
                    "end_date": end,
                    "assignment_status": ASSIGNMENT_STATUS_ACTIVE,
                })

                warning = _hr_allocation_warning(cur, people_id)

        return success_response(
            {
                "assignment": assignment,
                "demoted_previous_primary": demoted,
                "reports_to_resolution": reports_to_resolution,
                "allocation_warning": warning,
            },
            f"{person['first_name']} {person['name']} affecté(e) au rôle "
            f"'{role['role_name']}' (assignment_id={assignment['assignment_id']}).",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("assign_role_to_person failed")
        return error_response("Échec de l'affectation.", exc)


@mcp.tool()
def change_person_role(
    people_id: int,
    new_role_id: int,
    effective_date: Optional[str] = None,
    assignment_id: Optional[int] = None,
    new_unit_id: Optional[int] = None,
    keep_unit: bool = True,
    new_assignment_type: Optional[str] = None,
    new_allocation_percentage: Optional[float] = None,
    reports_to: Optional[int] = None,
    auto_resolve_reports_to: bool = True,
    keep_reports_to: bool = False,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
    target_role_label: Optional[str] = None,
    reassign_direct_reports: bool = False,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """
    CHANGEMENT DE POSTE (promotion, mutation fonctionnelle) en une transaction.

    Reproduit exactement le pattern déjà présent en base — jamais un UPDATE de
    role_id, qui effacerait l'historique :
      1. l'affectation en cours passe assignment_status='ENDED' avec
         end_date = effective_date ;
      2. une NOUVELLE ligne ACTIVE est créée avec start_date = effective_date
         (même date de part et d'autre, comme les 33 changements existants).

    Par défaut on part de l'affectation PRINCIPALE active ; assignment_id permet
    de cibler un cumul précis. L'unité, l'allocation et le type sont repris de
    l'ancienne affectation sauf surcharge (keep_unit=False + new_unit_id pour
    changer aussi d'usine).

    reports_to : recalculé depuis le rôle parent du nouveau rôle par défaut
    (le manager change presque toujours avec le poste) ; keep_reports_to=True
    conserve celui de l'ancienne affectation ; reports_to le force.

    reassign_direct_reports=True fait suivre les subordonnés : les affectations
    qui pointaient sur l'ancienne affectation via reports_to sont repointées sur
    la nouvelle. À utiliser pour une promotion où la personne garde son équipe ;
    à laisser False si l'équipe change de mains.
    """
    try:
        _hr_check_enum(new_assignment_type, VALID_ASSIGNMENT_TYPES_HR, "new_assignment_type")
        effective = _hr_parse_date(effective_date, "effective_date", default_today=True)
    except ValueError as exc:
        return error_response(str(exc))
    if new_allocation_percentage is not None and not (0 < float(new_allocation_percentage) <= 100):
        return error_response("new_allocation_percentage doit être dans ]0, 100].")

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                person = _hr_get_person(cur, people_id)
                if person is None:
                    return error_response(f"Personne {people_id} introuvable.")

                if assignment_id is not None:
                    old = _hr_get_assignment(cur, assignment_id)
                    if old is None:
                        return error_response(f"assignment_id={assignment_id} n'existe pas.")
                    if old["people_id"] != people_id:
                        return error_response(
                            f"assignment_id={assignment_id} appartient à people_id={old['people_id']}, "
                            f"pas à {people_id}."
                        )
                    if str(old["assignment_status"]).upper() != "ACTIVE":
                        return error_response(
                            f"assignment_id={assignment_id} est {old['assignment_status']} : "
                            "on ne clôt que des affectations ACTIVES."
                        )
                else:
                    primary = _hr_active_primary(cur, people_id)
                    if primary is None:
                        return error_response(
                            f"{person['first_name']} {person['name']} n'a aucune affectation "
                            "principale ACTIVE. Utiliser assign_role_to_person pour la première "
                            "affectation, ou préciser assignment_id."
                        )
                    old = _hr_get_assignment(cur, primary["assignment_id"])

                if old["role_id"] == new_role_id and (
                    keep_unit or new_unit_id is None or new_unit_id == old["unit_id"]
                ):
                    return error_response(
                        f"La personne occupe déjà le rôle {new_role_id} dans cette unité "
                        f"(assignment_id={old['assignment_id']}). Rien à changer — pour une mutation "
                        "d'usine à rôle identique, utiliser transfer_person_to_unit."
                    )
                if effective < (old["start_date"] or effective):
                    return error_response(
                        f"effective_date ({effective.isoformat()}) précède le début de l'affectation "
                        f"en cours ({old['start_date']})."
                    )

                new_role = _hr_get_role(cur, new_role_id)
                if new_role is None:
                    return error_response(f"new_role_id={new_role_id} n'existe pas dans {TABLE_ROLE}.")
                if not new_role.get("is_active"):
                    return error_response(
                        f"new_role_id={new_role_id} ('{new_role['role_name']}') est inactif."
                    )

                target_unit = old["unit_id"] if keep_unit and new_unit_id is None else new_unit_id
                ref_err = _hr_validate_reference(
                    cur, unit_id=target_unit, zone_id=zone_id,
                    market_id=market_id, product_line_id=product_line_id,
                )
                if ref_err:
                    return error_response(ref_err)

                resolved_reports_to = None
                reports_to_resolution = None
                if reports_to is not None:
                    err = _hr_validate_reports_to(cur, reports_to, people_id)
                    if err:
                        return error_response(err)
                    resolved_reports_to = reports_to
                elif keep_reports_to:
                    resolved_reports_to = old.get("reports_to")
                elif auto_resolve_reports_to:
                    reports_to_resolution = _hr_resolve_reports_to(
                        cur,
                        new_role_id,
                        target_unit,
                        people_id,
                        zone_id if zone_id is not None else old.get("zone_id"),
                    )
                    resolved_reports_to = reports_to_resolution["assignment_id"]

                dependents = _hr_dependent_assignments(cur, old["assignment_id"])

                closed = _hr_close_assignment(cur, old["assignment_id"], effective)

                new_assignment = _hr_insert_assignment(cur, {
                    "people_id": people_id,
                    "role_id": new_role_id,
                    "unit_id": target_unit,
                    "zone_id": zone_id if zone_id is not None else old.get("zone_id"),
                    "market_id": market_id if market_id is not None else old.get("market_id"),
                    "product_line_id": product_line_id if product_line_id is not None else old.get("product_line_id"),
                    "assignment_type": new_assignment_type or old.get("assignment_type") or "PERMANENT",
                    "allocation_percentage": (
                        new_allocation_percentage if new_allocation_percentage is not None
                        else old.get("allocation_percentage") or 100
                    ),
                    "is_primary": bool(old.get("is_primary")),
                    "reports_to": resolved_reports_to,
                    "functional_manager_role_id": old.get("functional_manager_role_id"),
                    "target_role_label": target_role_label or new_role["role_name"],
                    "assistant_context": old.get("assistant_context"),
                    "knowledge_context": old.get("knowledge_context"),
                    "start_date": effective,
                    "assignment_status": ASSIGNMENT_STATUS_ACTIVE,
                })

                followed: List[int] = []
                if dependents and reassign_direct_reports:
                    cur.execute(
                        f"""
                        UPDATE {TABLE_PRA}
                        SET reports_to = %s, updated_at = now()
                        WHERE upper(assignment_status) = 'ACTIVE' AND reports_to = %s
                        RETURNING assignment_id
                        """,
                        (new_assignment["assignment_id"], old["assignment_id"]),
                    )
                    followed = [r["assignment_id"] for r in cur.fetchall()]

                if comment:
                    cur.execute(
                        f"""
                        UPDATE {TABLE_PEOPLE}
                        SET comments = COALESCE(comments || E'\\n', '') || %s
                        WHERE people_id = %s
                        """,
                        (f"[changement de poste {effective.isoformat()}] "
                         f"{old.get('role_name')} → {new_role['role_name']} : {comment}",
                         people_id),
                    )

                warning = _hr_allocation_warning(cur, people_id)

        return success_response(
            {
                "previous_assignment": closed,
                "new_assignment": new_assignment,
                "from_role": {"role_id": old["role_id"], "role_name": old.get("role_name")},
                "to_role": {"role_id": new_role_id, "role_name": new_role["role_name"]},
                "effective_date": effective,
                "direct_reports_on_previous_assignment": dependents,
                "direct_reports_followed_assignment_ids": followed,
                "reports_to_resolution": reports_to_resolution,
                "allocation_warning": warning,
                "history_note": (
                    "L'ancienne affectation est conservée en ENDED : elle reste l'historique de "
                    "carrière (aucune table people_history n'existe dans la base)."
                ),
            },
            f"{person['first_name']} {person['name']} : '{old.get('role_name')}' → "
            f"'{new_role['role_name']}' au {effective.isoformat()}.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("change_person_role failed")
        return error_response("Échec du changement de poste.", exc)


@mcp.tool()
def transfer_person_to_unit(
    people_id: int,
    new_unit_id: int,
    effective_date: Optional[str] = None,
    assignment_id: Optional[int] = None,
    new_role_id: Optional[int] = None,
    update_work_at_unit: bool = True,
    reports_to: Optional[int] = None,
    auto_resolve_reports_to: bool = True,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """
    MUTATION d'usine / de service à rôle inchangé (mobilité géographique).

    Reproduit le pattern des mutations déjà en base (unité 23 → 24) : l'ancienne
    affectation passe ENDED, une nouvelle ACTIVE est créée sur new_unit_id avec
    le même rôle. new_role_id permet de combiner mutation et changement de poste.

    update_work_at_unit=True (défaut) aligne aussi people.work_at_unit_id, sinon
    la fiche resterait rattachée à l'ancienne usine. paid_by_unit_id n'est PAS
    touché : le payeur relève de la paie, pas de l'affectation opérationnelle —
    le modifier explicitement via update_person si nécessaire.

    reports_to est recalculé dans la nouvelle unité par défaut : garder le
    manager de l'usine d'origine est presque toujours une erreur.
    """
    try:
        effective = _hr_parse_date(effective_date, "effective_date", default_today=True)
    except ValueError as exc:
        return error_response(str(exc))

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                person = _hr_get_person(cur, people_id)
                if person is None:
                    return error_response(f"Personne {people_id} introuvable.")
                if not _row_exists(cur, TABLE_UNIT, "unit_id", new_unit_id):
                    return error_response(f"new_unit_id={new_unit_id} n'existe pas dans {TABLE_UNIT}.")

                if assignment_id is not None:
                    old = _hr_get_assignment(cur, assignment_id)
                    if old is None or old["people_id"] != people_id:
                        return error_response(
                            f"assignment_id={assignment_id} introuvable pour people_id={people_id}."
                        )
                    if str(old["assignment_status"]).upper() != "ACTIVE":
                        return error_response(f"assignment_id={assignment_id} n'est pas ACTIVE.")
                else:
                    primary = _hr_active_primary(cur, people_id)
                    if primary is None:
                        return error_response(
                            "Aucune affectation principale ACTIVE : préciser assignment_id."
                        )
                    old = _hr_get_assignment(cur, primary["assignment_id"])

                if old["unit_id"] == new_unit_id and new_role_id in (None, old["role_id"]):
                    return error_response(
                        f"La personne est déjà affectée à l'unité {new_unit_id} sur ce rôle."
                    )

                role_id = new_role_id if new_role_id is not None else old["role_id"]
                role = _hr_get_role(cur, role_id)
                if role is None:
                    return error_response(f"role_id={role_id} n'existe pas.")
                if not role.get("is_active"):
                    return error_response(f"role_id={role_id} ('{role['role_name']}') est inactif.")

                resolved_reports_to = None
                reports_to_resolution = None
                if reports_to is not None:
                    err = _hr_validate_reports_to(cur, reports_to, people_id)
                    if err:
                        return error_response(err)
                    resolved_reports_to = reports_to
                elif auto_resolve_reports_to:
                    reports_to_resolution = _hr_resolve_reports_to(
                        cur, role_id, new_unit_id, people_id, old.get("zone_id")
                    )
                    resolved_reports_to = reports_to_resolution["assignment_id"]

                closed = _hr_close_assignment(cur, old["assignment_id"], effective)

                new_assignment = _hr_insert_assignment(cur, {
                    "people_id": people_id,
                    "role_id": role_id,
                    "unit_id": new_unit_id,
                    "zone_id": old.get("zone_id"),
                    "market_id": old.get("market_id"),
                    "product_line_id": old.get("product_line_id"),
                    "assignment_type": old.get("assignment_type") or "PERMANENT",
                    "allocation_percentage": old.get("allocation_percentage") or 100,
                    "is_primary": bool(old.get("is_primary")),
                    "reports_to": resolved_reports_to,
                    "functional_manager_role_id": old.get("functional_manager_role_id"),
                    "target_role_label": role["role_name"],
                    "start_date": effective,
                    "assignment_status": ASSIGNMENT_STATUS_ACTIVE,
                })

                updated_person = None
                if update_work_at_unit:
                    cur.execute(
                        f"UPDATE {TABLE_PEOPLE} SET work_at_unit_id = %s WHERE people_id = %s RETURNING *",
                        (new_unit_id, people_id),
                    )
                    updated_person = dict(cur.fetchone())

                if comment:
                    cur.execute(
                        f"""
                        UPDATE {TABLE_PEOPLE}
                        SET comments = COALESCE(comments || E'\\n', '') || %s
                        WHERE people_id = %s
                        """,
                        (f"[mutation {effective.isoformat()}] unité {old['unit_id']} → "
                         f"{new_unit_id} : {comment}", people_id),
                    )

                cur.execute(f"SELECT unit_name FROM {TABLE_UNIT} WHERE unit_id = %s", (new_unit_id,))
                new_unit_name = cur.fetchone()["unit_name"]

        return success_response(
            {
                "previous_assignment": closed,
                "new_assignment": new_assignment,
                "from_unit": {"unit_id": old["unit_id"], "unit_name": old.get("unit_name")},
                "to_unit": {"unit_id": new_unit_id, "unit_name": new_unit_name},
                "person": updated_person,
                "reports_to_resolution": reports_to_resolution,
                "paid_by_unit_id_unchanged": person.get("paid_by_unit_id"),
            },
            f"{person['first_name']} {person['name']} muté(e) vers '{new_unit_name}' "
            f"au {effective.isoformat()}.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("transfer_person_to_unit failed")
        return error_response("Échec de la mutation.", exc)


@mcp.tool()
def update_role_assignment(
    assignment_id: int,
    fields: Optional[Dict[str, Any]] = None,
    unit_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
    assignment_type: Optional[str] = None,
    allocation_percentage: Optional[float] = None,
    is_primary: Optional[bool] = None,
    reports_to: Optional[int] = None,
    functional_manager_role_id: Optional[int] = None,
    functional_manager_assignment_id: Optional[int] = None,
    target_role_label: Optional[str] = None,
    assistant_context: Optional[str] = None,
    knowledge_context: Optional[str] = None,
    start_date: Optional[str] = None,
    demote_existing_primary: bool = False,
) -> Dict[str, Any]:
    """
    Corrige une affectation existante — pour les ERREURS DE SAISIE, pas pour les
    évolutions de carrière.

    role_id n'est volontairement PAS modifiable ici : changer le rôle en place
    détruirait l'historique. Passer par change_person_role.
    assignment_status et end_date non modifiables non plus : utiliser
    end_role_assignment.

    Promouvoir cette affectation en principale (is_primary=True) rétrograde
    l'ancienne principale uniquement si demote_existing_primary=True.
    """
    guard_error = _hr_reject_guarded_fields(fields, ASSIGNMENT_UPDATE_GUARDED_FIELDS, "fields")
    if guard_error:
        return guard_error
    try:
        _hr_check_enum(assignment_type, VALID_ASSIGNMENT_TYPES_HR, "assignment_type")
        start = _hr_parse_date(start_date, "start_date")
    except ValueError as exc:
        return error_response(str(exc))
    if allocation_percentage is not None and not (0 < float(allocation_percentage) <= 100):
        return error_response("allocation_percentage doit être dans ]0, 100].")

    forbidden = {"role_id", "people_id", "assignment_status", "end_date"}
    if fields:
        bad = forbidden.intersection({str(k).lower() for k in fields})
        if bad:
            return error_response(
                f"Champ(s) non modifiables ici : {', '.join(sorted(bad))}. "
                "role_id → change_person_role ; assignment_status / end_date → end_role_assignment."
            )

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                current = _hr_get_assignment(cur, assignment_id)
                if current is None:
                    return error_response(f"assignment_id={assignment_id} introuvable.")
                people_id = current["people_id"]

                ref_err = _hr_validate_reference(
                    cur, unit_id=unit_id, zone_id=zone_id,
                    market_id=market_id, product_line_id=product_line_id,
                )
                if ref_err:
                    return error_response(ref_err)

                if reports_to is not None:
                    if reports_to == assignment_id:
                        return error_response("Une affectation ne peut pas se reporter à elle-même.")
                    err = _hr_validate_reports_to(cur, reports_to, people_id)
                    if err:
                        return error_response(err)
                if functional_manager_assignment_id is not None:
                    err = _hr_validate_reports_to(cur, functional_manager_assignment_id, people_id)
                    if err:
                        return error_response("functional_manager_assignment_id : " + err)
                if functional_manager_role_id is not None and not _row_exists(
                    cur, TABLE_ROLE, "role_id", functional_manager_role_id
                ):
                    return error_response(
                        f"functional_manager_role_id={functional_manager_role_id} n'existe pas."
                    )

                demoted = None
                if is_primary is True and str(current["assignment_status"]).upper() == "ACTIVE":
                    existing = _hr_active_primary(cur, people_id, exclude_assignment_id=assignment_id)
                    if existing is not None:
                        if not demote_existing_primary:
                            resp = error_response(
                                f"Une autre affectation principale ACTIVE existe "
                                f"(assignment_id={existing['assignment_id']}, "
                                f"rôle '{existing.get('role_name')}'). "
                                "Passer demote_existing_primary=True pour la rétrograder."
                            )
                            resp["existing_primary"] = json_safe(existing)
                            return resp
                        cur.execute(
                            f"UPDATE {TABLE_PRA} SET is_primary = FALSE, updated_at = now() "
                            "WHERE assignment_id = %s RETURNING assignment_id, role_id",
                            (existing["assignment_id"],),
                        )
                        demoted = dict(cur.fetchone())

                payload = merge_update_payload({
                    "unit_id": unit_id,
                    "zone_id": zone_id,
                    "market_id": market_id,
                    "product_line_id": product_line_id,
                    "assignment_type": assignment_type,
                    "allocation_percentage": allocation_percentage,
                    "is_primary": is_primary,
                    "reports_to": reports_to,
                    "functional_manager_role_id": functional_manager_role_id,
                    "functional_manager_assignment_id": functional_manager_assignment_id,
                    "target_role_label": target_role_label,
                    "assistant_context": assistant_context,
                    "knowledge_context": knowledge_context,
                    "start_date": start,
                }, fields)

            row = update_row(conn, TABLE_PRA, PRA_PK, assignment_id, payload, touch_updated_at=True)
            if row is None:
                return error_response(f"assignment_id={assignment_id} introuvable.")

            with conn.cursor() as cur:
                warning = _hr_allocation_warning(cur, row["people_id"])

        return success_response(
            {
                "assignment": row,
                "demoted_previous_primary": demoted,
                "changed_fields": sorted(payload.keys()),
                "allocation_warning": warning,
            },
            "Affectation mise à jour.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("update_role_assignment failed")
        return error_response("Échec de la mise à jour de l'affectation.", exc)


@mcp.tool()
def end_role_assignment(
    assignment_id: int,
    end_date: Optional[str] = None,
    new_status: str = "ENDED",
    reassign_reports_to_assignment_id: Optional[int] = None,
    promote_assignment_id_to_primary: Optional[int] = None,
    allow_leaving_person_without_primary: bool = False,
) -> Dict[str, Any]:
    """
    Clôt UNE affectation (fin de cumul, fin d'intérim, correction) sans toucher
    à la fiche de la personne. Pour un départ complet, utiliser offboard_person ;
    pour un changement de poste, change_person_role.

    new_status : ENDED (défaut, avec end_date — convention respectée par 100%
    des lignes closes en base) ou INACTIVE (suspension).

    Garde-fous :
      - si c'est la seule affectation principale ACTIVE de la personne, l'outil
        REFUSE : elle sortirait de v_people_assignments et perdrait ses KPI.
        Fournir promote_assignment_id_to_primary (un de ses cumuls actifs) ou
        allow_leaving_person_without_primary=True en connaissance de cause ;
      - les subordonnés qui pointaient sur cette affectation via reports_to sont
        signalés, et reassign_reports_to_assignment_id les rebascule sur un
        autre manager dans la même transaction.
    """
    if new_status.upper() not in {"ENDED", "INACTIVE"}:
        return error_response("new_status doit être 'ENDED' ou 'INACTIVE'.")
    try:
        end = _hr_parse_date(end_date, "end_date", default_today=True)
    except ValueError as exc:
        return error_response(str(exc))

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                current = _hr_get_assignment(cur, assignment_id)
                if current is None:
                    return error_response(f"assignment_id={assignment_id} introuvable.")
                if str(current["assignment_status"]).upper() != "ACTIVE":
                    return error_response(
                        f"assignment_id={assignment_id} est déjà {current['assignment_status']}."
                    )
                people_id = current["people_id"]

                cur.execute(
                    f"""
                    SELECT a.assignment_id, a.role_id, r.role_name, a.unit_id, a.is_primary
                    FROM {TABLE_PRA} a
                    LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
                    WHERE a.people_id = %s AND upper(a.assignment_status) = 'ACTIVE'
                      AND a.assignment_id <> %s
                    ORDER BY a.is_primary DESC, a.assignment_id
                    """,
                    (people_id, assignment_id),
                )
                remaining = [dict(r) for r in cur.fetchall()]

                promoted = None
                if current.get("is_primary"):
                    if promote_assignment_id_to_primary is not None:
                        if promote_assignment_id_to_primary not in {r["assignment_id"] for r in remaining}:
                            resp = error_response(
                                f"promote_assignment_id_to_primary={promote_assignment_id_to_primary} "
                                "n'est pas une autre affectation ACTIVE de cette personne."
                            )
                            resp["remaining_active_assignments"] = json_safe(remaining)
                            return resp
                        cur.execute(
                            f"UPDATE {TABLE_PRA} SET is_primary = TRUE, updated_at = now() "
                            "WHERE assignment_id = %s RETURNING assignment_id, role_id",
                            (promote_assignment_id_to_primary,),
                        )
                        promoted = dict(cur.fetchone())
                    elif not allow_leaving_person_without_primary:
                        resp = error_response(
                            f"C'est l'affectation PRINCIPALE de {current['first_name']} "
                            f"{current['last_name']}. La clore la ferait disparaître de "
                            "v_people_assignments (donc de search_member, des responsables KPI et "
                            "des escalades). Fournir promote_assignment_id_to_primary, utiliser "
                            "change_person_role, ou forcer avec "
                            "allow_leaving_person_without_primary=True."
                        )
                        resp["remaining_active_assignments"] = json_safe(remaining)
                        return resp

                dependents = _hr_dependent_assignments(cur, assignment_id)
                reassigned: List[int] = []
                if dependents and reassign_reports_to_assignment_id is not None:
                    if reassign_reports_to_assignment_id == assignment_id:
                        return error_response(
                            "reassign_reports_to_assignment_id ne peut pas être l'affectation close."
                        )
                    new_boss = _hr_get_assignment(cur, reassign_reports_to_assignment_id)
                    if new_boss is None or str(new_boss["assignment_status"]).upper() != "ACTIVE":
                        return error_response(
                            f"reassign_reports_to_assignment_id={reassign_reports_to_assignment_id} "
                            "doit être une affectation ACTIVE existante."
                        )
                    cur.execute(
                        f"""
                        UPDATE {TABLE_PRA}
                        SET reports_to = %s, updated_at = now()
                        WHERE upper(assignment_status) = 'ACTIVE' AND reports_to = %s
                        RETURNING assignment_id
                        """,
                        (reassign_reports_to_assignment_id, assignment_id),
                    )
                    reassigned = [r["assignment_id"] for r in cur.fetchall()]

                closed = _hr_close_assignment(cur, assignment_id, end, new_status.upper())

        return success_response(
            {
                "closed_assignment": closed,
                "remaining_active_assignments": remaining,
                "promoted_to_primary": promoted,
                "direct_reports_affected": dependents,
                "reports_to_reassigned_assignment_ids": reassigned,
                "person_left_without_primary": bool(current.get("is_primary")) and promoted is None,
            },
            f"Affectation {assignment_id} ('{current.get('role_name')}') passée "
            f"{new_status.upper()} au {end.isoformat()}.",
        )
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("end_role_assignment failed")
        return error_response("Échec de la clôture de l'affectation.", exc)


@mcp.tool()
def get_person_career_history(
    people_id: Optional[int] = None,
    email: Optional[str] = None,
    include_inactive: bool = True,
) -> Dict[str, Any]:
    """
    Historique de carrière complet d'une personne : TOUTES ses affectations
    (ACTIVE, ENDED, INACTIVE) dans l'ordre chronologique, avec rôle, unité,
    manager, allocation et durée en jours.

    C'est la vue RH de référence, parce qu'il n'existe aucune table
    people_history : people_role_assignment porte elle-même l'historique via
    les lignes ENDED. Fournir people_id ou email.
    """
    if people_id is None and not email:
        return error_response("Fournir people_id ou email.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if people_id is None:
                    cur.execute(
                        f"SELECT people_id FROM {TABLE_PEOPLE} WHERE lower(email) = lower(%s)",
                        (email.strip(),),
                    )
                    found = cur.fetchone()
                    if found is None:
                        return error_response(f"Aucune personne avec l'email '{email}'.")
                    people_id = found["people_id"]

                person = _hr_get_person(cur, people_id)
                if person is None:
                    return error_response(f"Personne {people_id} introuvable.")

                status_filter = "" if include_inactive else " AND upper(a.assignment_status) = 'ACTIVE'"
                cur.execute(
                    f"""
                    SELECT a.assignment_id, a.assignment_status, a.assignment_type,
                           a.is_primary, a.allocation_percentage,
                           a.start_date, a.end_date,
                           COALESCE(a.end_date, CURRENT_DATE) - a.start_date AS duration_days,
                           a.role_id, r.role_name, r.role_level, r.grade, r.ceo_level,
                           vr.full_path AS role_full_path,
                           a.unit_id, u.unit_name, u.country,
                           a.zone_id, z.zone_name, a.market_id, m.market_name,
                           a.product_line_id, a.target_role_label,
                           a.reports_to,
                           boss.people_id AS boss_people_id,
                           bp.first_name  AS boss_first_name,
                           bp.name        AS boss_last_name,
                           br.role_name   AS boss_role_name,
                           a.created_at, a.updated_at
                    FROM {TABLE_PRA} a
                    LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
                    LEFT JOIN v_role_tree vr ON vr.role_id = a.role_id
                    LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
                    LEFT JOIN {TABLE_ZONE} z ON z.zone_id = a.zone_id
                    LEFT JOIN {TABLE_MARKET} m ON m.market_id = a.market_id
                    LEFT JOIN {TABLE_PRA} boss ON boss.assignment_id = a.reports_to
                    LEFT JOIN {TABLE_PEOPLE} bp ON bp.people_id = boss.people_id
                    LEFT JOIN {TABLE_ROLE} br ON br.role_id = boss.role_id
                    WHERE a.people_id = %s{status_filter}
                    ORDER BY a.start_date, a.assignment_id
                    """,
                    (people_id,),
                )
                history = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT a.assignment_id, a.people_id, p.first_name, p.name AS last_name,
                           r.role_name, u.unit_name
                    FROM {TABLE_PRA} a
                    JOIN {TABLE_PEOPLE} p ON p.people_id = a.people_id
                    LEFT JOIN {TABLE_ROLE} r ON r.role_id = a.role_id
                    LEFT JOIN {TABLE_UNIT} u ON u.unit_id = a.unit_id
                    WHERE upper(a.assignment_status) = 'ACTIVE'
                      AND a.reports_to IN (
                          SELECT assignment_id FROM {TABLE_PRA}
                          WHERE people_id = %s AND upper(assignment_status) = 'ACTIVE')
                    ORDER BY p.name, p.first_name
                    """,
                    (people_id,),
                )
                current_reports = [dict(r) for r in cur.fetchall()]

        active = [h for h in history if str(h["assignment_status"]).upper() == "ACTIVE"]
        return success_response({
            "person": person,
            "current_assignments": active,
            "current_primary": next((a for a in active if a.get("is_primary")), None),
            "history": history,
            "history_count": len(history),
            "role_changes": max(len({h["role_id"] for h in history if h["role_id"]}) - 1, 0),
            "current_direct_reports": current_reports,
        })
    except Exception as exc:
        logger.exception("get_person_career_history failed")
        return error_response("Échec de la lecture de l'historique de carrière.", exc)


# ============================================================================
# Profil d'outils RH — à fusionner dans la SECTION 2 de server.py
# ============================================================================
#
# 1) Ajouter cette allowlist à côté de KPI_AGENT_TOOL_ALLOWLIST :

HR_AGENT_TOOL_ALLOWLIST = {
    # Lecture — annuaire et organisation
    "search_member",
    "get_member",
    "find_member_by_email",
    "list_members",
    "list_members_by_unit",
    "list_members_by_role",
    "get_direct_reports",
    "get_org_path",
    "list_roles",
    "get_role",
    "get_role_tree",
    "list_kpi_units",
    "get_kpi_unit",
    "list_zones",
    "list_markets",
    "get_person_career_history",
    # Écriture — arborescence des rôles
    "create_role",
    "update_role",
    "deactivate_role",
    "delete_role",
    # Écriture — personnes
    "create_person",
    "update_person",
    "offboard_person",
    # Écriture — affectations
    "assign_role_to_person",
    "change_person_role",
    "transfer_person_to_unit",
    "update_role_assignment",
    "end_role_assignment",
    # Reporting ad hoc en lecture seule
    "execute_readonly_query",
}

# 2) Enregistrer le profil dans _TOOL_PROFILE_ALLOWLISTS :
#
#        "hr_agent":     HR_AGENT_TOOL_ALLOWLIST,
#        "hr-agent":     HR_AGENT_TOOL_ALLOWLIST,
#        "rh_agent":     HR_AGENT_TOOL_ALLOWLIST,
#
#    puis déployer une instance avec MCP_TOOL_PROFILE=hr_agent pour n'exposer
#    que ces 29 outils à l'agent RH.
#
# 3) Dans test_all_readonly_tools, ajouter à la liste `skipped` :
#
#        ("create_role", "write"), ("update_role", "write"),
#        ("deactivate_role", "write"), ("create_person", "write"),
#        ("update_person", "write"), ("offboard_person", "write"),
#        ("assign_role_to_person", "write"), ("change_person_role", "write"),
#        ("transfer_person_to_unit", "write"),
#        ("update_role_assignment", "write"), ("end_role_assignment", "write"),
#
#    et, pour couvrir les deux nouveaux outils de lecture :
#
#        _run_check(checks, "get_role_tree", get_role_tree, max_depth=1)
#        if pid:
#            _run_check(checks, "get_person_career_history",
#                       get_person_career_history, people_id=pid)
# ============================================================================
