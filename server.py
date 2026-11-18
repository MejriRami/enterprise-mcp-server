import logging
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import UUID, uuid4
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor, Json, execute_values
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("enterprise-central-mcp")

MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", os.getenv("PORT", "8000")))

mcp = FastMCP(
    "Enterprise Central MCP",
    host=MCP_HOST,
    port=MCP_PORT,
    sse_path="/sse",
    message_path="/messages/",
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
)
# ── Database selectors ────────────────────────────────────────────────────────
# Five databases are used:
#   "people"  -> PEOPLE_DATABASE_URL       (KPI_DB_Final: people, roles, units, KPIs, customers)
#   "actions" -> ACTION_PLAN_DATABASE_URL  (action-plan database)
#   "rfq"     -> RFQ_DATABASE_URL          (RFQ database)
#   "apqp"    -> APQP_DATABASE_URL         (APQP database)
#   "suppliers" -> SUPPLIERS_DATABASE_URL  (Suppliers management database, read-only tools)
#
# NOTE: "kpi" is kept as a backward-compatible ALIAS of "people", and
#       KPI_DATABASE_URL is used as a fallback if PEOPLE_DATABASE_URL is absent.
# ─────────────────────────────────────────────────────────────────────────────
DB_ENV_BY_NAME = {
    "people":  "PEOPLE_DATABASE_URL",
    "actions": "ACTION_PLAN_DATABASE_URL",
    "rfq":     "RFQ_DATABASE_URL",
    "apqp":    "APQP_DATABASE_URL",
    "suppliers": "SUPPLIERS_DATABASE_URL",
}
DB_SELECTOR_ALIASES = {
    "kpi": "people",
    "supplier": "suppliers",
    "supplier_management": "suppliers",
    "suppliers_management_db": "suppliers",
}
EXTERNAL_CRUD_DATABASES = {"rfq", "apqp"}
EXTERNAL_READ_DATABASES = {"rfq", "apqp", "suppliers"}

def resolve_db_selector(database: str) -> str:
    db = str(database).strip().lower()
    return DB_SELECTOR_ALIASES.get(db, db)
# ── Customer table constants (KPI_DB_Final) ───────────────────────────────────
# Customer data lives in KPI_DB_Final (the `customer` table), accessed through
# the "people" selector — no separate CRM database is used.
TABLE_CUSTOMER          = "customer"
TABLE_CUSTOMER_DELIVERY = "customer_delivery_unit"
TABLE_CUSTOMER_FILE     = "customer_file"
TABLE_CUSTOMER_PROJECT  = "customer_project"

CUSTOMER_PK          = ("customer_id",)
CUSTOMER_DELIVERY_PK = ("customer_delivery_id",)
CUSTOMER_FILE_PK     = ("customer_file_id",)
CUSTOMER_PROJECT_PK  = ("customer_project_id",)

# customer.customer_id has no DEFAULT nextval wired in KPI_DB_Final, so the
# create tool computes the next id explicitly (COALESCE(MAX(...),0)+1).
VALID_CUSTOMER_STATUS = {"Active", "Prospect", "Dormant", "Lost", "Blocked", "Archived"}
VALID_CUSTOMER_TYPE = {
    "Customer Group", "Customer Legal Entity", "Manufacturing Site",
    "Purchasing Center", "Design Center", "R&D Center",
    "Distribution Center", "Sales Office", "Aftermarket Unit",
}
VALID_STRATEGIC_LEVEL = {
    "Strategic", "Key Account", "Standard", "Opportunistic", "Low Priority",
}

# ── KPI_DB_Final table / view constants ───────────────────────────────────────
TABLE_PEOPLE = "people"
TABLE_PRA    = "people_role_assignment"
TABLE_ROLE   = "role"
TABLE_UNIT   = "unit"
TABLE_ZONE   = "zone"
TABLE_MARKET = "market"

TABLE_KPI             = "kpi"
TABLE_KPI_ALLOCATION  = "kpi_target_allocation"
TABLE_KPI_RESULT      = "kpi_result"
TABLE_TRAINING_RESULT = "kpi_training_result"
TABLE_ESCALATION_LOG  = "corrective_action_escalation_log"
TABLE_SUBJECT         = "subject"
TABLE_SUBJECT_KPI     = "subject_kpi"

VIEW_PEOPLE_ASSIGNMENTS = "v_people_assignments"
VIEW_PEOPLE_WITH_BOSS   = "v_people_with_boss"
VIEW_KPI_FULL_CONTEXT   = "v_kpi_full_context"
VIEW_TRAINING_DASHBOARD = "v_training_dashboard"

# ── KPI monitoring (V1) table / view constants ────────────────────────────────
TABLE_KPI_MON_STATUS  = "kpi_monitoring_status"
TABLE_KPI_MON_HISTORY = "kpi_monitoring_history"
TABLE_KPI_RELATION    = "kpi_relationship"
TABLE_KPI_ACTION_LINK = "kpi_action_link"
VIEW_KPI_MONITORING   = "v_kpi_monitoring"

VALID_MON_STATUS        = {"Green", "Yellow", "Red", "Critical"}
VALID_RELATION_TYPES    = {"leading", "driver", "correlation"}
VALID_ACTION_LINK_TYPES = {"root_cause", "symptom", "prevention"}

PEOPLE_PK = ("people_id",)
PRA_PK    = ("assignment_id",)
ROLE_PK   = ("role_id",)
UNIT_PK   = ("unit_id",)
KPI_PK    = ("kpi_id",)
KPI_ALLOCATION_PK = ("kpi_target_allocation_id",)

# kpi.status / kpi_target_allocation.target_status are varchar columns. The
# update tools normalize Active/Inactive to keep them consistent with is_active.
# target_direction is the one KPI field with a small known vocabulary.
VALID_TARGET_DIRECTION = {"up", "down", "stable", "range"}
ACTIVE_STATUS_NORMALIZATION = {
    "active": ("Active", True),
    "inactive": ("Inactive", False),
}

# Data convention (verified in KPI_DB_Final):
#   people.status                          → 'Active' / 'Inactive'   (mixed case)
#   people_role_assignment.assignment_status → 'ACTIVE' / 'INACTIVE' (upper case)
# All people_status comparisons below are therefore case-insensitive.
PEOPLE_STATUS_ACTIVE     = "Active"
ASSIGNMENT_STATUS_ACTIVE = "ACTIVE"

# ── Action DB table constants ─────────────────────────────────────────────────
TABLE_ACTION         = "action"
TABLE_SUJET          = "sujet"
TABLE_ATTACHMENT     = "action_attachment"
TABLE_EVENT_LOG      = "action_event_log"
TABLE_STATUS_COMMENT = "action_status_comment"

ACTION_PK     = ("id",)
SUJET_PK      = ("id",)
ATTACHMENT_PK = ("id",)

VALID_STATUSES            = {"open", "in_progress", "done", "cancelled", "on_hold"}
VALID_TYPES               = {"corrective", "preventive", "improvement", "observation","action"}
VALID_ASSIGNMENT_STATUSES = {"ACTIVE", "INACTIVE", "ENDED"}
VALID_TRAINING_STATUSES   = {
    "training_sent", "training_done", "test_sent", "test_overdue",
    "completed_pass", "completed_fail", "escalated",
}

READONLY_QUERY_ALLOWED_START      = {"select", "with"}
READONLY_QUERY_FORBIDDEN_KEYWORDS = {
    "alter", "analyze", "begin", "call", "cluster", "comment", "commit",
    "copy", "create", "deallocate", "delete", "discard", "do", "drop",
    "execute", "grant", "insert", "into", "listen", "lock", "merge",
    "notify", "prepare", "reassign", "refresh", "reindex", "reset",
    "revoke", "rollback", "security", "set", "truncate", "update",
    "vacuum",
}
READONLY_QUERY_MAX_ROWS_LIMIT = 5000
BATCH_ACTION_STATUS_MAX_IDS   = 500


# ============================================================================
# HTTP utility routes
# ============================================================================

@mcp.custom_route("/", methods=["GET"], include_in_schema=False)
async def root_info(request: Request) -> JSONResponse:
    return JSONResponse({
        "status":           "ok",
        "service":          "Enterprise Central MCP",
        "message":          "This is an MCP server. Use /mcp from ChatGPT or another MCP client.",
        "mcp_endpoint":     "/mcp",
        "health_endpoint":  "/health",
        "transport":        MCP_TRANSPORT,
        "registered_tools": len(mcp._tool_manager._tools),
    })


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({
        "status":           "ok",
        "service":          "Enterprise Central MCP",
        "transport":        MCP_TRANSPORT,
        "registered_tools": len(mcp._tool_manager._tools),
        "environment": {
            db: {"env_var": env, "present": bool(os.getenv(env))}
            for db, env in DB_ENV_BY_NAME.items()
        },
    })


# ============================================================================
# DB helpers
# ============================================================================

@contextmanager
def db_connection(database: str = "people"):
    conn = None
    try:
        selector = resolve_db_selector(database)
        env_name = DB_ENV_BY_NAME.get(selector)
        if env_name is None:
            raise ValueError(f"Unknown database selector: {database}.")

        database_url = os.getenv(env_name)

        # Fallback: if PEOPLE_DATABASE_URL is absent, try the legacy KPI_DATABASE_URL
        if not database_url and selector == "people":
            database_url = os.getenv("KPI_DATABASE_URL")

        if not database_url:
            raise RuntimeError(f"{env_name} environment variable is required.")

        conn = psycopg2.connect(
            database_url,
            cursor_factory=RealDictCursor,
            connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
            sslmode=os.getenv("PGSSLMODE", "require"),
            application_name="enterprise-central-mcp",
            # Per-statement safety net: no query can hang the session forever.
            options=f"-c statement_timeout={int(os.getenv('DB_STATEMENT_TIMEOUT_MS', '30000'))}",
            # TCP keepalives — important on Azure PostgreSQL to avoid silently
            # dropped idle connections.
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        # Force UTF-8 so accented data (customer names, comments, etc.) is always
        # decoded correctly, regardless of the database's server_encoding.
        conn.set_client_encoding("UTF8")
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def normalize_identifier(value: str) -> str:
    return "".join(char.lower() for char in str(value) if char.isalnum())


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(i) for i in value]
    return str(value)


def success_response(data: Any = None, message: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    r: Dict[str, Any] = {"success": True}
    if message is not None:
        r["message"] = message
    if data is not None:
        r["data"] = json_safe(data)
    r.update({k: json_safe(v) for k, v in extra.items()})
    return r


def error_response(message: str, exc: Optional[Exception] = None) -> Dict[str, Any]:
    r: Dict[str, Any] = {"success": False, "error": message}
    if exc is not None:
        detail = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
        if detail:
            r["details"] = detail
    return r


def validate_limit_offset(
    limit: Optional[int],
    offset: int = 0,
    *,
    max_limit: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if limit is not None:
        if limit <= 0:
            return error_response("limit must be greater than 0.")
        if max_limit is not None and limit > max_limit:
            return error_response(f"limit must be \u2264 {max_limit}.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    return None


def get_table_columns(conn, table_name: str) -> List[str]:
    """Writable columns only — excludes GENERATED ALWAYS columns."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname AS column_name
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class     c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND n.nspname = ANY(current_schemas(true))
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND a.attgenerated = ''
            ORDER BY a.attnum
            """,
            (table_name,),
        )
        return [row["column_name"] for row in cur.fetchall()]


def require_columns(conn, table_name: str) -> List[str]:
    cols = get_table_columns(conn, table_name)
    if not cols:
        raise ValueError(f'Table "{table_name}" not found or has no visible columns.')
    return cols


def find_column(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    col_list = list(columns)
    col_set  = set(col_list)
    for c in candidates:
        if c in col_set:
            return c
    norm = {normalize_identifier(c): c for c in col_list}
    for c in candidates:
        matched = norm.get(normalize_identifier(c))
        if matched is not None:
            return matched
    return None


def require_primary_key(columns: Iterable[str], candidates: Iterable[str], table_name: str) -> str:
    pk = find_column(columns, candidates)
    if pk is None:
        raise ValueError(
            f'Could not determine PK for "{table_name}". '
            f'Checked: {", ".join(candidates)}.'
        )
    return pk


def merge_create_payload(known: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {k: v for k, v in known.items() if v is not None}
    if extra is not None:
        if not isinstance(extra, dict):
            raise ValueError("extra_fields must be a JSON object.")
        payload.update(extra)
    return payload


def merge_update_payload(known: Dict[str, Any], fields: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {k: v for k, v in known.items() if v is not None}
    if fields is not None:
        if not isinstance(fields, dict):
            raise ValueError("fields must be a JSON object.")
        payload.update(fields)
    if not payload:
        raise ValueError("No fields provided for update.")
    return payload


def _normalized_payload_key(payload: Dict[str, Any], column_name: str) -> Optional[str]:
    target = normalize_identifier(column_name)
    for key in payload:
        if normalize_identifier(str(key)) == target:
            return key
    return None


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1"}:
            return True
        if normalized in {"false", "f", "no", "n", "0"}:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field_name} must be a boolean.")


def _invalid_active_status_message(status_field_name: str) -> str:
    if status_field_name == "status":
        return "Invalid KPI status. Allowed values: Active, Inactive."
    return f"Invalid {status_field_name}. Allowed values: Active, Inactive."


def normalize_active_status(
    status: Any,
    is_active: Any,
    status_field_name: str,
) -> Tuple[Optional[str], Optional[bool]]:
    normalized_status: Optional[str] = None
    normalized_is_active: Optional[bool] = None

    if status is not None:
        if not isinstance(status, str):
            raise ValueError(_invalid_active_status_message(status_field_name))
        match = ACTIVE_STATUS_NORMALIZATION.get(status.strip().lower())
        if match is None:
            raise ValueError(_invalid_active_status_message(status_field_name))
        normalized_status, expected_is_active = match
        if is_active is not None:
            provided_is_active = _coerce_bool(is_active, "is_active")
            if provided_is_active != expected_is_active:
                raise ValueError(f"{status_field_name} and is_active are inconsistent")
        normalized_is_active = expected_is_active
    elif is_active is not None:
        normalized_is_active = _coerce_bool(is_active, "is_active")
        normalized_status = "Active" if normalized_is_active else "Inactive"

    return normalized_status, normalized_is_active


def _validate_no_status_activity_conflicts(
    known_payload: Dict[str, Any],
    fields: Optional[Dict[str, Any]],
    status_field_name: str,
) -> None:
    if fields is None:
        return
    if not isinstance(fields, dict):
        raise ValueError("fields must be a JSON object.")

    for column_name in (status_field_name, "is_active"):
        known_key = _normalized_payload_key(known_payload, column_name)
        fields_key = _normalized_payload_key(fields, column_name)
        if known_key is None or fields_key is None:
            continue

        known_value = known_payload[known_key]
        fields_value = fields[fields_key]
        if column_name == status_field_name:
            known_normalized, _ = normalize_active_status(known_value, None, status_field_name)
            fields_normalized, _ = normalize_active_status(fields_value, None, status_field_name)
            if known_normalized != fields_normalized:
                raise ValueError(f"{status_field_name} values are inconsistent")
        else:
            if _coerce_bool(known_value, str(known_key)) != _coerce_bool(fields_value, str(fields_key)):
                raise ValueError("is_active values are inconsistent")


def normalize_status_activity_payload(
    payload: Dict[str, Any],
    status_field_name: str,
) -> Tuple[Optional[str], Optional[bool]]:
    status_key = _normalized_payload_key(payload, status_field_name)
    is_active_key = _normalized_payload_key(payload, "is_active")

    if status_key is not None and payload[status_key] is None:
        raise ValueError(_invalid_active_status_message(status_field_name))
    if is_active_key is not None and payload[is_active_key] is None:
        raise ValueError("is_active must be a boolean.")

    status_value = payload[status_key] if status_key is not None else None
    is_active_value = payload[is_active_key] if is_active_key is not None else None
    normalized_status, normalized_is_active = normalize_active_status(
        status_value,
        is_active_value,
        status_field_name,
    )

    if normalized_status is not None:
        if status_key is not None and status_key != status_field_name:
            payload.pop(status_key, None)
        payload[status_field_name] = normalized_status
    if normalized_is_active is not None:
        if is_active_key is not None and is_active_key != "is_active":
            payload.pop(is_active_key, None)
        payload["is_active"] = normalized_is_active

    return normalized_status, normalized_is_active


def merge_update_payload_with_status_activity(
    known: Dict[str, Any],
    fields: Optional[Dict[str, Any]],
    status_field_name: str,
) -> Tuple[Dict[str, Any], Optional[str], Optional[bool]]:
    known_payload = {k: v for k, v in known.items() if v is not None}
    _validate_no_status_activity_conflicts(known_payload, fields, status_field_name)
    payload = merge_update_payload(known, fields)
    normalized_status, normalized_is_active = normalize_status_activity_payload(payload, status_field_name)
    return payload, normalized_status, normalized_is_active


def resolve_payload_columns(
    payload: Dict[str, Any],
    columns: Iterable[str],
    disallowed_columns: Iterable[str] = (),
) -> Tuple[Dict[str, Any], List[str]]:
    col_list   = list(columns)
    exact      = set(col_list)
    norm       = {normalize_identifier(c): c for c in col_list}
    disallowed = set(disallowed_columns)
    resolved: Dict[str, Any] = {}
    unknown:  List[str]       = []
    for k, v in payload.items():
        kt  = str(k)
        col = kt if kt in exact else norm.get(normalize_identifier(kt))
        if col is None:
            unknown.append(kt)
            continue
        if col in disallowed:
            raise ValueError(f'Column "{col}" cannot be updated.')
        resolved[col] = v
    return resolved, unknown


def raise_for_unknown_fields(unknown: List[str], columns: Iterable[str], table_name: str) -> None:
    if unknown:
        raise ValueError(
            f'Unknown field(s) for "{table_name}": {", ".join(unknown)}. '
            f'Allowed: {", ".join(columns)}.'
        )


def insert_row(conn, table_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    columns = require_columns(conn, table_name)
    resolved, unknown = resolve_payload_columns(payload, columns)
    raise_for_unknown_fields(unknown, columns, table_name)
    with conn.cursor() as cur:
        if resolved:
            q = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) RETURNING *").format(
                t=sql.Identifier(table_name),
                c=sql.SQL(", ").join(sql.Identifier(c) for c in resolved),
                v=sql.SQL(", ").join(sql.Placeholder() for _ in resolved),
            )
            cur.execute(q, list(resolved.values()))
        else:
            q = sql.SQL("INSERT INTO {t} DEFAULT VALUES RETURNING *").format(t=sql.Identifier(table_name))
            cur.execute(q)
        return dict(cur.fetchone())


def get_row(conn, table_name: str, pk_candidates: Iterable[str], row_id: Any) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class     c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND n.nspname = ANY(current_schemas(true))
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
            """,
            (table_name,),
        )
        all_cols = [r["attname"] for r in cur.fetchall()]
    if not all_cols:
        raise ValueError(f'Table "{table_name}" not found.')
    pk = require_primary_key(all_cols, pk_candidates, table_name)
    q  = sql.SQL("SELECT * FROM {t} WHERE {pk} = %s").format(
        t=sql.Identifier(table_name), pk=sql.Identifier(pk)
    )
    with conn.cursor() as cur:
        cur.execute(q, (row_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_rows(
    conn, table_name: str, pk_candidates: Iterable[str],
    limit: Optional[int] = None, offset: int = 0,
) -> List[Dict[str, Any]]:
    if limit is not None and limit <= 0:
        raise ValueError("limit must be > 0.")
    if offset < 0:
        raise ValueError("offset must be \u2265 0.")
    cols = require_columns(conn, table_name)
    pk   = find_column(cols, pk_candidates)
    order  = sql.SQL(" ORDER BY {pk}").format(pk=sql.Identifier(pk)) if pk else sql.SQL("")
    q      = sql.SQL("SELECT * FROM {t}{o}").format(t=sql.Identifier(table_name), o=order)
    params: List[Any] = []
    if limit is not None:
        q = sql.SQL("{q} LIMIT %s").format(q=q)
        params.append(limit)
    if offset:
        q = sql.SQL("{q} OFFSET %s").format(q=q)
        params.append(offset)
    with conn.cursor() as cur:
        cur.execute(q, params)
        return [dict(r) for r in cur.fetchall()]


def update_row(
    conn,
    table_name: str,
    pk_candidates: Iterable[str],
    row_id: Any,
    payload: Dict[str, Any],
    *,
    touch_updated_at: bool = False,
) -> Optional[Dict[str, Any]]:
    if not payload:
        raise ValueError("No fields provided for update.")
    cols = require_columns(conn, table_name)
    pk   = require_primary_key(cols, pk_candidates, table_name)
    resolved, unknown = resolve_payload_columns(payload, cols, disallowed_columns=(pk,))
    raise_for_unknown_fields(unknown, cols, table_name)
    if not resolved:
        raise ValueError("No valid fields provided for update.")

    assignments: List[sql.SQL] = [
        sql.SQL("{c} = %s").format(c=sql.Identifier(c)) for c in resolved
    ]
    params = list(resolved.values())
    updated_at_col = find_column(cols, ("updated_at",)) if touch_updated_at else None
    if updated_at_col and updated_at_col not in resolved:
        assignments.append(sql.SQL("{c} = NOW()").format(c=sql.Identifier(updated_at_col)))

    q = sql.SQL("UPDATE {t} SET {a} WHERE {pk} = %s RETURNING *").format(
        t=sql.Identifier(table_name), a=sql.SQL(", ").join(assignments), pk=sql.Identifier(pk)
    )
    with conn.cursor() as cur:
        cur.execute(q, params + [row_id])
        row = cur.fetchone()
    return dict(row) if row else None


def delete_row(conn, table_name: str, pk_candidates: Iterable[str], row_id: Any) -> Optional[Dict[str, Any]]:
    cols = require_columns(conn, table_name)
    pk   = require_primary_key(cols, pk_candidates, table_name)
    q    = sql.SQL("DELETE FROM {t} WHERE {pk} = %s RETURNING *").format(
        t=sql.Identifier(table_name), pk=sql.Identifier(pk)
    )
    with conn.cursor() as cur:
        cur.execute(q, (row_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def _valid_values_text(values: Iterable[str]) -> str:
    return ", ".join(sorted(values))


# ============================================================================
# External database tools (RFQ / APQP CRUD, Suppliers read-only)
# ============================================================================

EXTERNAL_CRUD_DEFAULT_SCHEMA = "public"
EXTERNAL_CRUD_MAX_LIMIT = 500
EXTERNAL_CRUD_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXTERNAL_CRUD_BLOCKED_SCHEMAS = {"pg_catalog", "information_schema"}


def _normalize_external_read_db(database: str) -> str:
    db = resolve_db_selector(database)
    if db not in EXTERNAL_READ_DATABASES:
        raise ValueError(f"database must be one of: {_valid_values_text(EXTERNAL_READ_DATABASES)}.")
    return db


def _normalize_external_write_db(database: str) -> str:
    db = resolve_db_selector(database)
    if db not in EXTERNAL_CRUD_DATABASES:
        raise ValueError(
            f"database must be one of: {_valid_values_text(EXTERNAL_CRUD_DATABASES)}. "
            "Writes are not enabled for suppliers."
        )
    return db


def _normalize_external_db(database: str) -> str:
    return _normalize_external_read_db(database)

def _validate_external_identifier(name: str, kind: str) -> str:
    ident = str(name or "").strip()
    if not ident:
        raise ValueError(f"{kind} is required.")
    if len(ident) > 63:
        raise ValueError(f"{kind} '{ident}' is too long; PostgreSQL identifiers are max 63 characters.")
    if not EXTERNAL_CRUD_IDENTIFIER_RE.match(ident):
        raise ValueError(f"{kind} '{ident}' is invalid. Use letters, digits, and '_' only; start with a letter or '_'.")
    return ident


def _normalize_external_schema(schema: str) -> str:
    schema_name = _validate_external_identifier(schema or EXTERNAL_CRUD_DEFAULT_SCHEMA, "schema")
    if schema_name.lower() in EXTERNAL_CRUD_BLOCKED_SCHEMAS:
        raise ValueError(f"schema '{schema_name}' is not allowed.")
    return schema_name


def _normalize_external_table(table_name: str) -> str:
    return _validate_external_identifier(table_name, "table_name")


def _get_external_relation_type(conn, schema: str, table_name: str, *, allow_views: bool) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_type
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
            """,
            (schema, table_name),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError(f'Table "{schema}.{table_name}" not found.')
    table_type = row["table_type"]
    if table_type != "BASE TABLE" and not allow_views:
        raise ValueError(f'"{schema}.{table_name}" is {table_type}; writes require a BASE TABLE.')
    return table_type


def _get_external_columns(conn, schema: str, table_name: str) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                column_name,
                data_type,
                udt_name,
                is_nullable,
                column_default,
                ordinal_position,
                is_generated,
                is_identity
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table_name),
        )
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        raise ValueError(f'Table "{schema}.{table_name}" has no visible columns.')
    return rows


def _get_external_primary_key(conn, schema: str, table_name: str) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_schema = tc.constraint_schema
             AND kcu.constraint_name = tc.constraint_name
             AND kcu.table_schema = tc.table_schema
             AND kcu.table_name = tc.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s
            ORDER BY kcu.ordinal_position
            """,
            (schema, table_name),
        )
        return [row["column_name"] for row in cur.fetchall()]


def _external_table_info(conn, schema: str, table_name: str, *, allow_views: bool) -> Dict[str, Any]:
    table_type = _get_external_relation_type(conn, schema, table_name, allow_views=allow_views)
    columns = _get_external_columns(conn, schema, table_name)
    primary_key = _get_external_primary_key(conn, schema, table_name)
    return {"table_type": table_type, "columns": columns, "primary_key": primary_key}


def _external_column_names(columns: List[Dict[str, Any]], *, writable_only: bool = False) -> List[str]:
    if writable_only:
        return [c["column_name"] for c in columns if c.get("is_generated") != "ALWAYS"]
    return [c["column_name"] for c in columns]


def _external_json_columns(columns: List[Dict[str, Any]]) -> set:
    return {
        c["column_name"] for c in columns
        if c.get("data_type") in {"json", "jsonb"} or c.get("udt_name") in {"json", "jsonb"}
    }


def _adapt_external_payload_values(
    payload: Dict[str, Any],
    columns: List[Dict[str, Any]],
) -> Dict[str, Any]:
    json_columns = _external_json_columns(columns)
    return {
        key: Json(value) if key in json_columns and value is not None else value
        for key, value in payload.items()
    }


def _resolve_external_fields(
    payload: Dict[str, Any],
    columns: Iterable[str],
    table_name: str,
    *,
    disallowed_columns: Iterable[str] = (),
    label: str = "fields",
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object.")
    resolved, unknown = resolve_payload_columns(payload, columns, disallowed_columns=disallowed_columns)
    raise_for_unknown_fields(unknown, columns, table_name)
    return resolved


def _where_from_resolved_fields(resolved: Dict[str, Any]) -> Tuple[sql.SQL, List[Any]]:
    conditions: List[sql.SQL] = []
    params: List[Any] = []
    for column_name, value in resolved.items():
        if value is None:
            conditions.append(sql.SQL("{c} IS NULL").format(c=sql.Identifier(column_name)))
        else:
            conditions.append(sql.SQL("{c} = %s").format(c=sql.Identifier(column_name)))
            params.append(value)
    if not conditions:
        return sql.SQL(""), []
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions), params


def _resolve_external_key_fields(
    columns: List[Dict[str, Any]],
    primary_key: List[str],
    table_name: str,
    *,
    row_id: Optional[Any] = None,
    key_fields: Optional[Dict[str, Any]] = None,
    pk_column: Optional[str] = None,
) -> Dict[str, Any]:
    column_names = _external_column_names(columns)
    if key_fields is not None:
        resolved = _resolve_external_fields(key_fields, column_names, table_name, label="key_fields")
        if not resolved:
            raise ValueError("key_fields must contain at least one field.")
        return resolved

    if pk_column:
        if row_id is None:
            raise ValueError("row_id is required when pk_column is provided.")
        resolved_pk = find_column(column_names, [pk_column])
        if resolved_pk is None:
            raise ValueError(f'Column "{pk_column}" not found in "{table_name}".')
        return {resolved_pk: row_id}

    if len(primary_key) == 1:
        if row_id is None:
            raise ValueError(f"row_id is required for primary key column '{primary_key[0]}'.")
        return {primary_key[0]: row_id}

    if len(primary_key) > 1:
        raise ValueError(
            f'"{table_name}" has a composite primary key ({", ".join(primary_key)}). '
            "Provide key_fields as a JSON object."
        )

    raise ValueError(f'"{table_name}" has no detected primary key. Provide key_fields or pk_column + row_id.')


@mcp.tool()
def list_external_database_tables(
    database: str,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    include_views: bool = False,
) -> Dict[str, Any]:
    """
    List base tables in the RFQ, APQP, or Suppliers database. Set include_views=True to
    include views for read-only exploration. database must be 'rfq', 'apqp', or 'suppliers'.
    """
    try:
        db = _normalize_external_db(database)
        schema_name = _normalize_external_schema(schema)
        allowed_types = ["BASE TABLE", "VIEW"] if include_views else ["BASE TABLE"]
        with db_connection(db) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_schema, table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema = %s
                      AND table_type = ANY(%s)
                    ORDER BY table_name
                    """,
                    (schema_name, allowed_types),
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, database=db, schema=schema_name, count=len(rows))
    except Exception as exc:
        logger.exception("list_external_database_tables failed")
        return error_response("Failed to list external database tables.", exc)


@mcp.tool()
def describe_external_database_table(
    database: str,
    table_name: str,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
) -> Dict[str, Any]:
    """
    Describe one RFQ/APQP/Suppliers table or view: relation type, columns, defaults,
    nullable flags, identity/generated flags, and primary key columns.
    """
    try:
        db = _normalize_external_db(database)
        schema_name = _normalize_external_schema(schema)
        table = _normalize_external_table(table_name)
        with db_connection(db) as conn:
            info = _external_table_info(conn, schema_name, table, allow_views=True)
        return success_response({"database": db, "schema": schema_name, "table_name": table, **info})
    except Exception as exc:
        logger.exception("describe_external_database_table failed")
        return error_response("Failed to describe external database table.", exc)


@mcp.tool()
def list_external_database_rows(
    database: str,
    table_name: str,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 100,
    offset: int = 0,
    order_by: Optional[str] = None,
    descending: bool = False,
) -> Dict[str, Any]:
    """
    List rows from an RFQ/APQP/Suppliers table or view. filters is an optional JSON object
    of equality filters, for example {"status": "OPEN"}. Limit is capped at 500.
    """
    invalid = validate_limit_offset(limit, offset, max_limit=EXTERNAL_CRUD_MAX_LIMIT)
    if invalid is not None:
        return invalid
    try:
        db = _normalize_external_db(database)
        schema_name = _normalize_external_schema(schema)
        table = _normalize_external_table(table_name)
        with db_connection(db) as conn:
            info = _external_table_info(conn, schema_name, table, allow_views=True)
            column_names = _external_column_names(info["columns"])
            filter_fields = {} if filters is None else _resolve_external_fields(filters, column_names, table, label="filters")
            where_sql, params = _where_from_resolved_fields(filter_fields)

            if order_by:
                order_column = find_column(column_names, [order_by])
                if order_column is None:
                    return error_response(f'Column "{order_by}" not found in "{table}".')
            elif info["primary_key"]:
                order_column = info["primary_key"][0]
            else:
                order_column = column_names[0]

            order_direction = sql.SQL(" DESC") if descending else sql.SQL(" ASC")
            q = sql.SQL("SELECT * FROM {t}{w} ORDER BY {o}{d} LIMIT %s OFFSET %s").format(
                t=sql.Identifier(schema_name, table),
                w=where_sql,
                o=sql.Identifier(order_column),
                d=order_direction,
            )
            with conn.cursor() as cur:
                cur.execute(q, params + [limit, offset])
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(
            rows,
            database=db,
            schema=schema_name,
            table_name=table,
            count=len(rows),
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.exception("list_external_database_rows failed")
        return error_response("Failed to list external database rows.", exc)


@mcp.tool()
def get_external_database_row(
    database: str,
    table_name: str,
    row_id: Optional[Any] = None,
    key_fields: Optional[Dict[str, Any]] = None,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    pk_column: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get one row from an RFQ/APQP/Suppliers table or view. For single-column primary keys,
    pass row_id. For composite keys or tables without a PK, pass key_fields.
    """
    try:
        db = _normalize_external_db(database)
        schema_name = _normalize_external_schema(schema)
        table = _normalize_external_table(table_name)
        with db_connection(db) as conn:
            info = _external_table_info(conn, schema_name, table, allow_views=True)
            key = _resolve_external_key_fields(
                info["columns"], info["primary_key"], table,
                row_id=row_id, key_fields=key_fields, pk_column=pk_column,
            )
            where_sql, params = _where_from_resolved_fields(key)
            q = sql.SQL("SELECT * FROM {t}{w} LIMIT 2").format(
                t=sql.Identifier(schema_name, table), w=where_sql,
            )
            with conn.cursor() as cur:
                cur.execute(q, params)
                rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return error_response(f'No row found in "{schema_name}.{table}" for the provided key.')
        if len(rows) > 1:
            return error_response("Provided key matched multiple rows; provide a more specific key_fields object.")
        return success_response(rows[0], database=db, schema=schema_name, table_name=table)
    except Exception as exc:
        logger.exception("get_external_database_row failed")
        return error_response("Failed to get external database row.", exc)


@mcp.tool()
def create_external_database_row(
    database: str,
    table_name: str,
    fields: Optional[Dict[str, Any]] = None,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
) -> Dict[str, Any]:
    """
    Create one row in an RFQ/APQP base table. fields must be a JSON object whose
    keys are table columns. Generated columns are not accepted.
    """
    try:
        db = _normalize_external_write_db(database)
        schema_name = _normalize_external_schema(schema)
        table = _normalize_external_table(table_name)
        with db_connection(db) as conn:
            info = _external_table_info(conn, schema_name, table, allow_views=False)
            writable_columns = _external_column_names(info["columns"], writable_only=True)
            payload = {} if fields is None else _resolve_external_fields(fields, writable_columns, table)
            adapted_payload = _adapt_external_payload_values(payload, info["columns"])
            with conn.cursor() as cur:
                if payload:
                    q = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({vals}) RETURNING *").format(
                        t=sql.Identifier(schema_name, table),
                        cols=sql.SQL(", ").join(sql.Identifier(c) for c in payload),
                        vals=sql.SQL(", ").join(sql.Placeholder() for _ in payload),
                    )
                    cur.execute(q, list(adapted_payload.values()))
                else:
                    q = sql.SQL("INSERT INTO {t} DEFAULT VALUES RETURNING *").format(
                        t=sql.Identifier(schema_name, table),
                    )
                    cur.execute(q)
                row = dict(cur.fetchone())
        return success_response(row, "External database row created.", database=db, schema=schema_name, table_name=table)
    except Exception as exc:
        logger.exception("create_external_database_row failed")
        return error_response("Failed to create external database row.", exc)


@mcp.tool()
def update_external_database_row(
    database: str,
    table_name: str,
    fields: Dict[str, Any],
    row_id: Optional[Any] = None,
    key_fields: Optional[Dict[str, Any]] = None,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    pk_column: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update one row in an RFQ/APQP base table. For single-column primary keys,
    pass row_id. For composite keys or tables without a PK, pass key_fields.
    """
    try:
        db = _normalize_external_write_db(database)
        schema_name = _normalize_external_schema(schema)
        table = _normalize_external_table(table_name)
        with db_connection(db) as conn:
            info = _external_table_info(conn, schema_name, table, allow_views=False)
            key = _resolve_external_key_fields(
                info["columns"], info["primary_key"], table,
                row_id=row_id, key_fields=key_fields, pk_column=pk_column,
            )
            writable_columns = _external_column_names(info["columns"], writable_only=True)
            payload = _resolve_external_fields(fields, writable_columns, table, disallowed_columns=key.keys())
            if not payload:
                return error_response("No valid fields provided for update.")
            adapted_payload = _adapt_external_payload_values(payload, info["columns"])
            assignments = sql.SQL(", ").join(
                sql.SQL("{c} = %s").format(c=sql.Identifier(c)) for c in payload
            )
            where_sql, where_params = _where_from_resolved_fields(key)
            count_q = sql.SQL("SELECT COUNT(*) AS match_count FROM {t}{w}").format(
                t=sql.Identifier(schema_name, table), w=where_sql,
            )
            q = sql.SQL("UPDATE {t} SET {a}{w} RETURNING *").format(
                t=sql.Identifier(schema_name, table), a=assignments, w=where_sql,
            )
            with conn.cursor() as cur:
                cur.execute(count_q, where_params)
                match_count = cur.fetchone()["match_count"]
                if match_count == 0:
                    return error_response(f'No row found in "{schema_name}.{table}" for the provided key.')
                if match_count > 1:
                    return error_response("Provided key matched multiple rows; update refused.")
                cur.execute(q, list(adapted_payload.values()) + where_params)
                row = cur.fetchone()
        if row is None:
            return error_response(f'No row found in "{schema_name}.{table}" for the provided key.')
        return success_response(dict(row), "External database row updated.", database=db, schema=schema_name, table_name=table)
    except Exception as exc:
        logger.exception("update_external_database_row failed")
        return error_response("Failed to update external database row.", exc)


@mcp.tool()
def delete_external_database_row(
    database: str,
    table_name: str,
    row_id: Optional[Any] = None,
    key_fields: Optional[Dict[str, Any]] = None,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    pk_column: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Delete one row from an RFQ/APQP base table. For single-column primary keys,
    pass row_id. For composite keys or tables without a PK, pass key_fields.
    """
    try:
        db = _normalize_external_write_db(database)
        schema_name = _normalize_external_schema(schema)
        table = _normalize_external_table(table_name)
        with db_connection(db) as conn:
            info = _external_table_info(conn, schema_name, table, allow_views=False)
            key = _resolve_external_key_fields(
                info["columns"], info["primary_key"], table,
                row_id=row_id, key_fields=key_fields, pk_column=pk_column,
            )
            where_sql, params = _where_from_resolved_fields(key)
            count_q = sql.SQL("SELECT COUNT(*) AS match_count FROM {t}{w}").format(
                t=sql.Identifier(schema_name, table), w=where_sql,
            )
            q = sql.SQL("DELETE FROM {t}{w} RETURNING *").format(
                t=sql.Identifier(schema_name, table), w=where_sql,
            )
            with conn.cursor() as cur:
                cur.execute(count_q, params)
                match_count = cur.fetchone()["match_count"]
                if match_count == 0:
                    return error_response(f'No row found in "{schema_name}.{table}" for the provided key.')
                if match_count > 1:
                    return error_response("Provided key matched multiple rows; delete refused.")
                cur.execute(q, params)
                row = cur.fetchone()
        if row is None:
            return error_response(f'No row found in "{schema_name}.{table}" for the provided key.')
        return success_response(dict(row), "External database row deleted.", database=db, schema=schema_name, table_name=table)
    except Exception as exc:
        logger.exception("delete_external_database_row failed")
        return error_response("Failed to delete external database row.", exc)


# ============================================================================
# Suppliers management read-only tools
# ============================================================================

SUPPLIER_DATABASE_SELECTOR = "suppliers"


@mcp.tool()
def list_supplier_database_tables(
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    include_views: bool = False,
) -> Dict[str, Any]:
    """
    List base tables in Suppliers_management_db. Set include_views=True to
    include views. This tool is read-only.
    """
    return list_external_database_tables(SUPPLIER_DATABASE_SELECTOR, schema, include_views)


@mcp.tool()
def describe_supplier_database_table(
    table_name: str,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
) -> Dict[str, Any]:
    """Describe one Suppliers_management_db table or view. This tool is read-only."""
    return describe_external_database_table(SUPPLIER_DATABASE_SELECTOR, table_name, schema)


@mcp.tool()
def list_supplier_database_rows(
    table_name: str,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 100,
    offset: int = 0,
    order_by: Optional[str] = None,
    descending: bool = False,
) -> Dict[str, Any]:
    """
    List rows from a Suppliers_management_db table or view with optional equality
    filters, pagination, and ordering. This tool is read-only.
    """
    return list_external_database_rows(
        SUPPLIER_DATABASE_SELECTOR,
        table_name,
        schema,
        filters,
        limit,
        offset,
        order_by,
        descending,
    )


@mcp.tool()
def get_supplier_database_row(
    table_name: str,
    row_id: Optional[Any] = None,
    key_fields: Optional[Dict[str, Any]] = None,
    schema: str = EXTERNAL_CRUD_DEFAULT_SCHEMA,
    pk_column: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get one row from a Suppliers_management_db table or view. For single-column
    primary keys, pass row_id. For composite keys or tables without a PK, pass
    key_fields. This tool is read-only.
    """
    return get_external_database_row(
        SUPPLIER_DATABASE_SELECTOR,
        table_name,
        row_id,
        key_fields,
        schema,
        pk_column,
    )


@mcp.tool()
def query_suppliers_readonly(
    query: str,
    params: Optional[List[Any]] = None,
    max_rows: int = 500,
    timeout_ms: int = 15000,
) -> Dict[str, Any]:
    """
    Execute one guarded read-only SELECT/WITH query on Suppliers_management_db.
    INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, COPY, TRUNCATE, and SELECT
    locking clauses are rejected by execute_readonly_query.
    """
    return execute_readonly_query(SUPPLIER_DATABASE_SELECTOR, query, params, max_rows, timeout_ms)


# ============================================================================
# Structured Suppliers / Purchasing domain read-only tools
# ============================================================================

SUP_GROUP = "supplier_group"
SUP_UNIT = "supplier_unit"
SUP_RELATION = "supplier_site_relation"
SUP_SITE = "company_site"
SUP_CONTACT = "contact"
SUP_CERT = "supplier_certification"
SUP_CARBON = "supplier_carbon_footprint"
SUP_SPEND = "supplier_spend_by_year"
SUP_STATUS_HIST = "supplier_status_history"

SUP_CYCLE = "evaluation_cycle"
SUP_OPER_INPUT = "operational_evaluation_input"
SUP_IMPACT_INPUT = "impact_evaluation_input"
SUP_PLD_INPUT = "pld_class_evaluation_input"
SUP_CLASSIF = "classification"
SUP_SCORECARD = "score_card"

SUP_OPPORTUNITY = "opportunity"
SUP_PROJECT = "project"
SUP_FIN_LINE = "financial_line"
SUP_MONTHLY_FIN = "monthly_financial"
SUP_BUDGET_YEAR = "opportunity_budget_year"

SUPPLIERS_MAX_LIMIT = 500


@contextmanager
def supplier_readonly_connection():
    with db_connection(SUPPLIER_DATABASE_SELECTOR) as conn:
        conn.set_session(readonly=True)
        yield conn


def _sup_deleted_clause(alias: str, include_deleted: bool) -> str:
    return "" if include_deleted else f" AND {alias}.is_deleted = false"


@mcp.tool()
def list_supplier_groups(
    query: Optional[str] = None,
    supplier_scope: Optional[str] = None,
    only_active: bool = True,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List supplier groups from Suppliers_management_db. query matches nom; each
    row includes a count of supplier units.
    """
    if limit < 1 or limit > SUPPLIERS_MAX_LIMIT:
        return error_response(f"limit must be between 1 and {SUPPLIERS_MAX_LIMIT}.")
    if offset < 0:
        return error_response("offset must be >= 0.")
    try:
        conditions = ["1=1"]
        params: List[Any] = []
        if not include_deleted:
            conditions.append("g.is_deleted = false")
        if only_active:
            conditions.append("g.is_active = true")
        if query and query.strip():
            conditions.append("g.nom ILIKE %s")
            params.append(f"%{query.strip()}%")
        if supplier_scope:
            conditions.append("g.supplier_scope = %s")
            params.append(supplier_scope)
        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT g.id_group, g.nom, g.supplier_scope,
                           g.group_supplier_owner_email, g.multi_site,
                           g.validation_status, g.is_active,
                           (SELECT COUNT(*) FROM {SUP_UNIT} u
                             WHERE u.id_group = g.id_group AND u.is_deleted = false) AS unit_count
                    FROM {SUP_GROUP} g
                    {where}
                    ORDER BY g.nom
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_supplier_groups failed")
        return error_response("Failed to list supplier groups.", exc)


@mcp.tool()
def search_supplier_units(
    query: str,
    country: Optional[str] = None,
    commodity: Optional[str] = None,
    family: Optional[str] = None,
    strategic_only: bool = False,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Search supplier units by supplier_name, city, commodity, or family.
    Optional filters: country, commodity, family, strategic_only.
    """
    if not query or not query.strip():
        return error_response("query is required.")
    if limit < 1 or limit > 200:
        return error_response("limit must be between 1 and 200.")
    if offset < 0:
        return error_response("offset must be >= 0.")
    try:
        like = f"%{query.strip()}%"
        conditions = [
            "(u.supplier_name ILIKE %s OR u.city ILIKE %s OR u.commodity ILIKE %s OR u.family ILIKE %s)"
        ]
        params: List[Any] = [like, like, like, like]
        if not include_deleted:
            conditions.append("u.is_deleted = false")
        if country:
            conditions.append("u.country ILIKE %s")
            params.append(f"%{country}%")
        if commodity:
            conditions.append("u.commodity ILIKE %s")
            params.append(f"%{commodity}%")
        if family:
            conditions.append("u.family ILIKE %s")
            params.append(f"%{family}%")
        if strategic_only:
            conditions.append("u.strategique = true")
        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT u.id_supplier_unit, u.id_group, g.nom AS group_name,
                           u.supplier_name, u.city, u.country, u.continent, u.area,
                           u.family, u.sub_family, u.product_line, u.commodity,
                           u.strategique, u.monopolistique, u.directed,
                           u.carbon_footprint, u.green_electricity_pct,
                           u.website, u.is_active
                    FROM {SUP_UNIT} u
                    LEFT JOIN {SUP_GROUP} g ON g.id_group = u.id_group
                    {where}
                    ORDER BY u.supplier_name
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("search_supplier_units failed")
        return error_response("Failed to search supplier units.", exc)


@mcp.tool()
def list_supplier_relations(
    id_supplier_unit: Optional[int] = None,
    id_site: Optional[int] = None,
    supplier_status: Optional[str] = None,
    buyer_owner: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List supplier to the company site relations. This is the supplier-site hub
    for buyer owner, status, grades, spend, and evaluation dates.
    """
    if limit < 1 or limit > SUPPLIERS_MAX_LIMIT:
        return error_response(f"limit must be between 1 and {SUPPLIERS_MAX_LIMIT}.")
    if offset < 0:
        return error_response("offset must be >= 0.")
    try:
        conditions = ["1=1"]
        params: List[Any] = []
        if not include_deleted:
            conditions.append("r.is_deleted = false")
        if id_supplier_unit is not None:
            conditions.append("r.id_supplier_unit = %s")
            params.append(id_supplier_unit)
        if id_site is not None:
            conditions.append("r.id_site = %s")
            params.append(id_site)
        if supplier_status:
            conditions.append("r.supplier_status = %s")
            params.append(supplier_status)
        if buyer_owner:
            conditions.append("r.buyer_owner ILIKE %s")
            params.append(f"%{buyer_owner}%")
        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT r.id_relation, r.id_supplier_unit, u.supplier_name,
                           r.id_site, s.site_name AS company_site,
                           r.alias_1, r.buyer_owner, r.supplier_status, r.global_status,
                           r.operational_grade, r.class_value, r.final_grade,
                           r.strategic_mention, r.panel_decision,
                           r.evaluation_frequency, r.last_evaluation_date,
                           r.next_evaluation_date, r.last_eval_score,
                           r.annual_spend_value, r.validation_status, r.is_active
                    FROM {SUP_RELATION} r
                    LEFT JOIN {SUP_UNIT} u ON u.id_supplier_unit = r.id_supplier_unit
                    LEFT JOIN {SUP_SITE} s ON s.id_site = r.id_site
                    {where}
                    ORDER BY u.supplier_name, s.site_name
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_supplier_relations failed")
        return error_response("Failed to list supplier relations.", exc)


@mcp.tool()
def get_supplier_unit_360(
    id_supplier_unit: int,
    include_deleted: bool = False,
) -> Dict[str, Any]:
    """
    Full read-only profile of one supplier unit: parent group, site relations,
    contacts, certifications, spend by year, and recent carbon footprint.
    """
    try:
        del_r = _sup_deleted_clause("r", include_deleted)
        del_c = _sup_deleted_clause("c", include_deleted)
        del_ce = _sup_deleted_clause("ce", include_deleted)
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT u.*, g.nom AS group_name, g.supplier_scope,
                           g.group_supplier_owner_email
                    FROM {SUP_UNIT} u
                    LEFT JOIN {SUP_GROUP} g ON g.id_group = u.id_group
                    WHERE u.id_supplier_unit = %s
                    """,
                    (id_supplier_unit,),
                )
                unit = cur.fetchone()
                if unit is None:
                    return error_response(f"Supplier unit {id_supplier_unit} not found.")
                unit = dict(unit)

                cur.execute(
                    f"""
                    SELECT r.id_relation, r.id_site, s.site_name AS company_site,
                           r.buyer_owner, r.supplier_status, r.final_grade,
                           r.strategic_mention, r.last_evaluation_date,
                           r.next_evaluation_date, r.annual_spend_value, r.last_eval_score
                    FROM {SUP_RELATION} r
                    LEFT JOIN {SUP_SITE} s ON s.id_site = r.id_site
                    WHERE r.id_supplier_unit = %s{del_r}
                    ORDER BY s.site_name
                    """,
                    (id_supplier_unit,),
                )
                unit["relations"] = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT c.id_contact, c.full_name, c.role_label, c.role_name,
                           c.email, c.phone, c.is_primary_contact
                    FROM {SUP_CONTACT} c
                    WHERE c.id_supplier_unit = %s{del_c}
                    ORDER BY c.is_primary_contact DESC NULLS LAST, c.full_name
                    """,
                    (id_supplier_unit,),
                )
                unit["contacts"] = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT ce.id_certification, ce.certification_type, ce.certificate_name,
                           ce.standard_type, ce.start_date, ce.end_date, ce.expiry_mode
                    FROM {SUP_CERT} ce
                    WHERE ce.id_supplier_unit = %s{del_ce}
                    ORDER BY ce.end_date DESC NULLS LAST
                    """,
                    (id_supplier_unit,),
                )
                unit["certifications"] = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT sp.fiscal_year, sp.spend_currency,
                           SUM(sp.spend_value) AS spend_value
                    FROM {SUP_SPEND} sp
                    JOIN {SUP_RELATION} r ON r.id_relation = sp.id_relation
                    WHERE r.id_supplier_unit = %s{del_r}
                    GROUP BY sp.fiscal_year, sp.spend_currency
                    ORDER BY sp.fiscal_year DESC
                    """,
                    (id_supplier_unit,),
                )
                unit["spend_by_year"] = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT cf.year, cf.carbon_fp_grade, cf.production_fp_grade,
                           cf.weighted_footprint, cf.global_fp_impact,
                           cf.purchase_amount
                    FROM {SUP_CARBON} cf
                    WHERE cf.id_supplier_unit = %s AND cf.is_deleted = false
                    ORDER BY cf.year DESC
                    LIMIT 5
                    """,
                    (id_supplier_unit,),
                )
                unit["carbon_footprint"] = [dict(r) for r in cur.fetchall()]
        return success_response(unit)
    except Exception as exc:
        logger.exception("get_supplier_unit_360 failed")
        return error_response("Failed to build supplier 360.", exc)


@mcp.tool()
def list_evaluation_cycles(
    id_relation: Optional[int] = None,
    cycle_status: Optional[str] = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """List supplier evaluation cycles, newest first by period_start."""
    if limit < 1 or limit > SUPPLIERS_MAX_LIMIT:
        return error_response(f"limit must be between 1 and {SUPPLIERS_MAX_LIMIT}.")
    if offset < 0:
        return error_response("offset must be >= 0.")
    try:
        conditions = ["1=1"]
        params: List[Any] = []
        if not include_deleted:
            conditions.append("ec.is_deleted = false")
        if id_relation is not None:
            conditions.append("ec.id_relation = %s")
            params.append(id_relation)
        if cycle_status:
            conditions.append("ec.cycle_status = %s")
            params.append(cycle_status)
        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ec.id_cycle, ec.id_relation, ec.cycle_type, ec.supplier_type,
                           ec.frequency, ec.period_start, ec.period_end, ec.due_date,
                           ec.cycle_status, ec.launched_by, ec.launched_at, ec.completed_at
                    FROM {SUP_CYCLE} ec
                    {where}
                    ORDER BY ec.period_start DESC NULLS LAST, ec.id_cycle DESC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_evaluation_cycles failed")
        return error_response("Failed to list evaluation cycles.", exc)


@mcp.tool()
def get_relation_evaluation(
    id_relation: int,
    id_cycle: Optional[int] = None,
    include_deleted: bool = False,
) -> Dict[str, Any]:
    """
    Get relation header plus latest operational, impact, PLD, classification,
    and scorecard records. Scope to id_cycle when provided.
    """
    try:
        del_o = _sup_deleted_clause("o", include_deleted)
        del_i = _sup_deleted_clause("i", include_deleted)
        del_p = _sup_deleted_clause("p", include_deleted)
        del_cl = _sup_deleted_clause("cl", include_deleted)
        del_sc = _sup_deleted_clause("sc", include_deleted)
        cycle_o = " AND o.id_cycle = %s" if id_cycle is not None else ""
        cycle_i = " AND i.id_cycle = %s" if id_cycle is not None else ""
        cycle_p = " AND p.id_cycle = %s" if id_cycle is not None else ""
        cycle_cl = " AND cl.id_cycle = %s" if id_cycle is not None else ""
        cycle_sc = " AND sc.id_cycle = %s" if id_cycle is not None else ""
        params = [id_relation] + ([id_cycle] if id_cycle is not None else [])
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT r.id_relation, u.supplier_name, s.site_name AS company_site,
                           r.buyer_owner, r.supplier_status, r.final_grade,
                           r.operational_grade, r.class_value, r.strategic_mention,
                           r.last_evaluation_date, r.next_evaluation_date, r.last_eval_score
                    FROM {SUP_RELATION} r
                    LEFT JOIN {SUP_UNIT} u ON u.id_supplier_unit = r.id_supplier_unit
                    LEFT JOIN {SUP_SITE} s ON s.id_site = r.id_site
                    WHERE r.id_relation = %s
                    """,
                    (id_relation,),
                )
                header = cur.fetchone()
                if header is None:
                    return error_response(f"Relation {id_relation} not found.")
                result: Dict[str, Any] = {"relation": dict(header)}

                cur.execute(
                    f"""
                    SELECT o.* FROM {SUP_OPER_INPUT} o
                    WHERE o.id_relation = %s{cycle_o}{del_o}
                    ORDER BY o.entered_at DESC LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                result["operational_input"] = dict(row) if row else None

                cur.execute(
                    f"""
                    SELECT i.* FROM {SUP_IMPACT_INPUT} i
                    WHERE i.id_relation = %s{cycle_i}{del_i}
                    ORDER BY i.entered_at DESC LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                result["impact_input"] = dict(row) if row else None

                cur.execute(
                    f"""
                    SELECT p.* FROM {SUP_PLD_INPUT} p
                    WHERE p.id_relation = %s{cycle_p}{del_p}
                    ORDER BY p.entered_at DESC LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                result["pld_class_input"] = dict(row) if row else None

                cur.execute(
                    f"""
                    SELECT cl.* FROM {SUP_CLASSIF} cl
                    WHERE cl.id_relation = %s{cycle_cl}{del_cl}
                    ORDER BY cl.classification_date DESC NULLS LAST, cl.id_classification DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                result["classification"] = dict(row) if row else None

                cur.execute(
                    f"""
                    SELECT sc.* FROM {SUP_SCORECARD} sc
                    WHERE sc.id_relation = %s{cycle_sc}{del_sc}
                    ORDER BY sc.scorecard_date DESC NULLS LAST, sc.id_score_card DESC
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
                result["score_card"] = dict(row) if row else None
        return success_response(result)
    except Exception as exc:
        logger.exception("get_relation_evaluation failed")
        return error_response("Failed to get relation evaluation.", exc)


@mcp.tool()
def get_supplier_status_history(id_relation: int, limit: int = 100) -> Dict[str, Any]:
    """Return newest-first status/class/grade changes for one supplier relation."""
    if limit < 1 or limit > SUPPLIERS_MAX_LIMIT:
        return error_response(f"limit must be between 1 and {SUPPLIERS_MAX_LIMIT}.")
    try:
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id_history, id_relation, old_status, new_status,
                           old_class, new_class, old_grade, new_grade,
                           old_final_grade, new_final_grade,
                           old_strategic_mention, new_strategic_mention,
                           change_reason, changed_by, changed_at
                    FROM {SUP_STATUS_HIST}
                    WHERE id_relation = %s
                    ORDER BY changed_at DESC, id_history DESC
                    LIMIT %s
                    """,
                    (id_relation, limit),
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_supplier_status_history failed")
        return error_response("Failed to get supplier status history.", exc)


@mcp.tool()
def list_opportunities(
    query: Optional[str] = None,
    status: Optional[str] = None,
    phase_status: Optional[str] = None,
    priority_category: Optional[str] = None,
    budget_year: Optional[int] = None,
    plant_id: Optional[int] = None,
    include_deleted: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """List cost-savings opportunities with optional status and budget filters."""
    if limit < 1 or limit > SUPPLIERS_MAX_LIMIT:
        return error_response(f"limit must be between 1 and {SUPPLIERS_MAX_LIMIT}.")
    if offset < 0:
        return error_response("offset must be >= 0.")
    try:
        conditions = ["1=1"]
        params: List[Any] = []
        if not include_deleted:
            conditions.append("o.is_deleted = false")
        if query and query.strip():
            conditions.append("(o.opportunity_name ILIKE %s OR o.description ILIKE %s)")
            like = f"%{query.strip()}%"
            params.extend([like, like])
        if status:
            conditions.append("o.status = %s")
            params.append(status)
        if phase_status:
            conditions.append("o.phase_status = %s")
            params.append(phase_status)
        if priority_category:
            conditions.append("o.priority_category = %s")
            params.append(priority_category)
        if budget_year is not None:
            conditions.append("o.budget_year = %s")
            params.append(budget_year)
        if plant_id is not None:
            conditions.append("o.plant_id = %s")
            params.append(plant_id)
        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT o.opportunity_id, o.opportunity_name, o.opportunity_type,
                           o.status, o.phase_status, o.priority_category, o.priority_score,
                           o.purchasing_owner, o.project_owner, o.plant_id, o.supplier_id,
                           o.proposed_supplier_name,
                           o.expected_annual_saving, o.cash_impact, o.currency,
                           o.budget_year, o.planned_start_date, o.real_start_date,
                           o.validation_status, o.created_at
                    FROM {SUP_OPPORTUNITY} o
                    {where}
                    ORDER BY o.priority_score DESC NULLS LAST, o.opportunity_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_opportunities failed")
        return error_response("Failed to list opportunities.", exc)


@mcp.tool()
def get_opportunity(opportunity_id: int, include_deleted: bool = False) -> Dict[str, Any]:
    """Get one opportunity plus linked projects, financial lines, and budget years."""
    try:
        del_p = _sup_deleted_clause("p", include_deleted)
        del_f = _sup_deleted_clause("f", include_deleted)
        del_b = _sup_deleted_clause("b", include_deleted)
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {SUP_OPPORTUNITY} WHERE opportunity_id = %s",
                    (opportunity_id,),
                )
                opp = cur.fetchone()
                if opp is None:
                    return error_response(f"Opportunity {opportunity_id} not found.")
                opp = dict(opp)

                cur.execute(
                    f"""
                    SELECT p.project_id, p.project_name, p.project_type, p.project_owner,
                           p.phase_status, p.gate_decision, p.status,
                           p.planned_end_date, p.actual_end_date
                    FROM {SUP_PROJECT} p
                    WHERE p.opportunity_id = %s{del_p}
                    ORDER BY p.project_id
                    """,
                    (opportunity_id,),
                )
                opp["projects"] = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT f.financial_line_id, f.line_name, f.budget_status, f.status,
                           f.expected_annual_saving, f.budget_value,
                           f.cumulated_real_saving, f.forecast_eoy_current,
                           f.recovery_status, f.follower, f.is_escalated
                    FROM {SUP_FIN_LINE} f
                    WHERE f.opportunity_id = %s{del_f}
                    ORDER BY f.financial_line_id
                    """,
                    (opportunity_id,),
                )
                opp["financial_lines"] = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT b.id, b.fiscal_year, b.applicable_amount, b.portion_kind,
                           b.budget_status, b.suggested_status, b.is_additional
                    FROM {SUP_BUDGET_YEAR} b
                    WHERE b.opportunity_id = %s{del_b}
                    ORDER BY b.fiscal_year
                    """,
                    (opportunity_id,),
                )
                opp["budget_years"] = [dict(r) for r in cur.fetchall()]
        return success_response(opp)
    except Exception as exc:
        logger.exception("get_opportunity failed")
        return error_response("Failed to get opportunity.", exc)


@mcp.tool()
def get_financial_line_monthly(
    financial_line_id: int,
    include_deleted: bool = False,
    limit: int = 60,
) -> Dict[str, Any]:
    """Get one financial line plus monthly expected, actual, forecast, and cash rows."""
    if limit < 1 or limit > 240:
        return error_response("limit must be between 1 and 240.")
    try:
        del_m = _sup_deleted_clause("m", include_deleted)
        with supplier_readonly_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {SUP_FIN_LINE} WHERE financial_line_id = %s",
                    (financial_line_id,),
                )
                line = cur.fetchone()
                if line is None:
                    return error_response(f"Financial line {financial_line_id} not found.")
                line = dict(line)

                cur.execute(
                    f"""
                    SELECT m.monthly_financial_id, m.period_month,
                           m.expected_saving, m.actual_saving,
                           m.cumulated_expected, m.cumulated_actual,
                           m.delta_vs_expected, m.delta_vs_budget,
                           m.forecast_eoy_saving, m.monthly_outcome,
                           m.cash_expected, m.cash_actual, m.comment
                    FROM {SUP_MONTHLY_FIN} m
                    WHERE m.financial_line_id = %s{del_m}
                    ORDER BY m.period_month
                    LIMIT %s
                    """,
                    (financial_line_id, limit),
                )
                line["monthly"] = [dict(r) for r in cur.fetchall()]
        return success_response(line)
    except Exception as exc:
        logger.exception("get_financial_line_monthly failed")
        return error_response("Failed to get financial line monthly.", exc)


# ============================================================================
# Structured RFQ / APQP project CRUD tools
# ============================================================================

TABLE_RFQ = "rfq"
TABLE_APQP_PROJECT = "projects"

VALID_RFQ_PHASES = {"RFQ", "COSTING", "OFFER", "PO", "PROTOTYPE", "CLOSED"}
VALID_RFQ_SUB_STATUSES = {
    "POTENTIAL", "NEW_RFQ", "PENDING_FOR_VALIDATION", "FEASIBILITY",
    "PRICING", "PREPARATION", "VALIDATION", "GET_PO", "PO_ACCEPTED",
    "MISSION_ACCEPTED", "MISSION_NOT_ACCEPTED", "GET_PROTOTYPE",
    "PROTOTYPE_ONGOING", "LOST", "CANCELED", "PO_SECURED",
    "REVISION_REQUESTED", "RFI_COMPLETED", "AI_APPROVED",
    "PENDING_AI_APPROVAL", "REJECTED_BY_AI",
}
VALID_RFQ_DOCUMENT_TYPES = {"RFQ", "RFI", "POTENTIAL"}


def _drop_none_values(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


def _normalize_enum_value(value: Any, allowed: Iterable[str], field_name: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if value is None:
        return None, None
    normalized = str(value).strip().upper()
    if normalized not in set(allowed):
        return None, error_response(f"{field_name} must be one of: {_valid_values_text(allowed)}.")
    return normalized, None


def _require_text(value: Optional[str], field_name: str) -> Optional[Dict[str, Any]]:
    if value is None or not str(value).strip():
        return error_response(f"{field_name} is required.")
    return None


def _list_external_summary_rows(
    database: str,
    table_name: str,
    select_columns: List[str],
    filters: Dict[str, Any],
    limit: int,
    offset: int,
    order_by: str,
    descending: bool,
    orderable_columns: Iterable[str],
) -> Dict[str, Any]:
    invalid = validate_limit_offset(limit, offset, max_limit=EXTERNAL_CRUD_MAX_LIMIT)
    if invalid is not None:
        return invalid
    if order_by not in set(orderable_columns):
        return error_response(f"order_by must be one of: {_valid_values_text(orderable_columns)}.")
    try:
        db = _normalize_external_db(database)
        where_sql, params = _where_from_resolved_fields(filters)
        order_direction = sql.SQL(" DESC") if descending else sql.SQL(" ASC")
        q = sql.SQL("SELECT {cols} FROM {table}{where} ORDER BY {order_by}{direction} LIMIT %s OFFSET %s").format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in select_columns),
            table=sql.Identifier(EXTERNAL_CRUD_DEFAULT_SCHEMA, table_name),
            where=where_sql,
            order_by=sql.Identifier(order_by),
            direction=order_direction,
        )
        with db_connection(db) as conn:
            with conn.cursor() as cur:
                cur.execute(q, params + [limit, offset])
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(
            rows,
            database=db,
            schema=EXTERNAL_CRUD_DEFAULT_SCHEMA,
            table_name=table_name,
            count=len(rows),
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.exception("_list_external_summary_rows failed")
        return error_response("Failed to list external summary rows.", exc)


@mcp.tool()
def list_rfqs(
    phase: Optional[str] = None,
    sub_status: Optional[str] = None,
    created_by_email: Optional[str] = None,
    zone_manager_email: Optional[str] = None,
    contact_id: Optional[int] = None,
    document_type: Optional[str] = None,
    product_line_acronym: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    order_by: str = "updated_at",
    descending: bool = True,
) -> Dict[str, Any]:
    """
    List RFQs from the RFQ database table public.rfq.
    Optional filters are exact-match filters. Use execute_readonly_query for
    contains/search/reporting questions such as old RFQs by KAM.
    """
    phase_value, err = _normalize_enum_value(phase, VALID_RFQ_PHASES, "phase")
    if err:
        return err
    sub_status_value, err = _normalize_enum_value(sub_status, VALID_RFQ_SUB_STATUSES, "sub_status")
    if err:
        return err
    document_type_value, err = _normalize_enum_value(document_type, VALID_RFQ_DOCUMENT_TYPES, "document_type")
    if err:
        return err
    filters = _drop_none_values({
        "phase": phase_value,
        "sub_status": sub_status_value,
        "created_by_email": created_by_email,
        "zone_manager_email": zone_manager_email,
        "contact_id": contact_id,
        "document_type": document_type_value,
        "product_line_acronym": product_line_acronym,
    })
    return _list_external_summary_rows(
        "rfq",
        TABLE_RFQ,
        [
            "rfq_id", "phase", "sub_status", "document_type",
            "product_line_acronym", "contact_id", "zone_manager_email",
            "created_by_email", "systematic_rfq_id", "created_at", "updated_at",
            "approved_at", "rejected_at", "validated_by", "follow_up_count",
        ],
        filters,
        limit,
        offset,
        order_by,
        descending,
        {
            "updated_at", "created_at", "rfq_id", "phase", "sub_status",
            "created_by_email", "document_type", "product_line_acronym",
        },
    )


@mcp.tool()
def get_rfq(rfq_id: str) -> Dict[str, Any]:
    """Get one RFQ from the RFQ database by rfq_id."""
    if not rfq_id or not str(rfq_id).strip():
        return error_response("rfq_id is required.")
    return get_external_database_row("rfq", TABLE_RFQ, row_id=str(rfq_id).strip())


@mcp.tool()
def create_rfq(
    created_by_email: str,
    phase: str = "RFQ",
    sub_status: str = "NEW_RFQ",
    rfq_id: Optional[str] = None,
    product_line_acronym: Optional[str] = None,
    contact_id: Optional[int] = None,
    zone_manager_email: Optional[str] = None,
    rfq_data: Optional[Dict[str, Any]] = None,
    chat_history: Optional[List[Dict[str, Any]]] = None,
    rejection_reason: Optional[str] = None,
    autopsy_notes: Optional[str] = None,
    costing_files: Optional[List[Dict[str, Any]]] = None,
    document_type: str = "RFQ",
    systematic_rfq_id: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create one RFQ in public.rfq. rfq_id is generated as a UUID string when
    omitted. Required business fields: created_by_email, phase, sub_status.
    """
    err = _require_text(created_by_email, "created_by_email")
    if err:
        return err
    phase_value, err = _normalize_enum_value(phase, VALID_RFQ_PHASES, "phase")
    if err:
        return err
    sub_status_value, err = _normalize_enum_value(sub_status, VALID_RFQ_SUB_STATUSES, "sub_status")
    if err:
        return err
    document_type_value, err = _normalize_enum_value(document_type, VALID_RFQ_DOCUMENT_TYPES, "document_type")
    if err:
        return err
    try:
        payload = merge_create_payload({
            "rfq_id": str(rfq_id or uuid4()),
            "phase": phase_value,
            "sub_status": sub_status_value,
            "product_line_acronym": product_line_acronym,
            "contact_id": contact_id,
            "zone_manager_email": zone_manager_email,
            "created_by_email": created_by_email.strip(),
            "rfq_data": rfq_data,
            "chat_history": chat_history,
            "rejection_reason": rejection_reason,
            "autopsy_notes": autopsy_notes,
            "costing_files": costing_files,
            "document_type": document_type_value,
            "systematic_rfq_id": systematic_rfq_id,
        }, extra_fields)
        return create_external_database_row("rfq", TABLE_RFQ, payload)
    except Exception as exc:
        logger.exception("create_rfq failed")
        return error_response("Failed to create RFQ.", exc)


@mcp.tool()
def update_rfq(
    rfq_id: str,
    fields: Optional[Dict[str, Any]] = None,
    phase: Optional[str] = None,
    sub_status: Optional[str] = None,
    product_line_acronym: Optional[str] = None,
    contact_id: Optional[int] = None,
    zone_manager_email: Optional[str] = None,
    rfq_data: Optional[Dict[str, Any]] = None,
    chat_history: Optional[List[Dict[str, Any]]] = None,
    rejection_reason: Optional[str] = None,
    autopsy_notes: Optional[str] = None,
    costing_files: Optional[List[Dict[str, Any]]] = None,
    approved_at: Optional[str] = None,
    rejected_at: Optional[str] = None,
    validated_by: Optional[str] = None,
    validation_notes: Optional[str] = None,
    revision_notes: Optional[str] = None,
    costing_file_state: Optional[Dict[str, Any]] = None,
    last_notification_sent_at: Optional[str] = None,
    follow_up_count: Optional[int] = None,
    document_type: Optional[str] = None,
    systematic_rfq_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Update one RFQ in public.rfq by rfq_id. Pass only fields to change."""
    if not rfq_id or not str(rfq_id).strip():
        return error_response("rfq_id is required.")
    phase_value, err = _normalize_enum_value(phase, VALID_RFQ_PHASES, "phase")
    if err:
        return err
    sub_status_value, err = _normalize_enum_value(sub_status, VALID_RFQ_SUB_STATUSES, "sub_status")
    if err:
        return err
    document_type_value, err = _normalize_enum_value(document_type, VALID_RFQ_DOCUMENT_TYPES, "document_type")
    if err:
        return err
    try:
        payload = merge_update_payload({
            "phase": phase_value,
            "sub_status": sub_status_value,
            "product_line_acronym": product_line_acronym,
            "contact_id": contact_id,
            "zone_manager_email": zone_manager_email,
            "rfq_data": rfq_data,
            "chat_history": chat_history,
            "rejection_reason": rejection_reason,
            "autopsy_notes": autopsy_notes,
            "costing_files": costing_files,
            "approved_at": approved_at,
            "rejected_at": rejected_at,
            "validated_by": validated_by,
            "validation_notes": validation_notes,
            "revision_notes": revision_notes,
            "costing_file_state": costing_file_state,
            "last_notification_sent_at": last_notification_sent_at,
            "follow_up_count": follow_up_count,
            "document_type": document_type_value,
            "systematic_rfq_id": systematic_rfq_id,
        }, fields)
        payload.setdefault("updated_at", datetime.utcnow())
        return update_external_database_row("rfq", TABLE_RFQ, payload, row_id=str(rfq_id).strip())
    except Exception as exc:
        logger.exception("update_rfq failed")
        return error_response("Failed to update RFQ.", exc)


@mcp.tool()
def delete_rfq(rfq_id: str) -> Dict[str, Any]:
    """Delete one RFQ from public.rfq by rfq_id."""
    if not rfq_id or not str(rfq_id).strip():
        return error_response("rfq_id is required.")
    return delete_external_database_row("rfq", TABLE_RFQ, row_id=str(rfq_id).strip())


@mcp.tool()
def list_apqp_projects(
    project_status: Optional[str] = None,
    project_phase: Optional[int] = None,
    project_manager_email: Optional[str] = None,
    project_owner_email: Optional[str] = None,
    customer: Optional[str] = None,
    plant: Optional[str] = None,
    product_line: Optional[str] = None,
    project_code: Optional[str] = None,
    is_archived: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
    order_by: str = "updated_at",
    descending: bool = True,
) -> Dict[str, Any]:
    """
    List APQP projects from the APQP database table public.projects.
    Optional filters are exact-match filters.
    """
    filters = _drop_none_values({
        "project_status": project_status,
        "project_phase": project_phase,
        "project_manager_email": project_manager_email,
        "project_owner_email": project_owner_email,
        "customer": customer,
        "plant": plant,
        "product_line": product_line,
        "project_code": project_code,
        "is_archived": is_archived,
    })
    return _list_external_summary_rows(
        "apqp",
        TABLE_APQP_PROJECT,
        [
            "id", "source_request_id", "name", "project_code", "customer",
            "plant", "product_line", "product_group", "project_phase",
            "project_status", "project_timeline_status", "project_delay_category",
            "project_manager", "project_manager_email", "project_owner_name",
            "project_owner_email", "vp_function", "project_category", "year",
            "sop_target_date", "sop_actual_date", "sop_planned_date",
            "ppap_submission_target_date", "ppap_submission_actual_date",
            "plant_delay", "customer_delay", "risk_level", "ppap_status",
            "is_status", "is_archived", "created_at", "updated_at",
        ],
        filters,
        limit,
        offset,
        order_by,
        descending,
        {
            "updated_at", "created_at", "id", "name", "project_code",
            "project_phase", "project_status", "customer", "plant",
            "product_line", "project_manager_email", "is_archived",
        },
    )


@mcp.tool()
def get_apqp_project(project_id: str) -> Dict[str, Any]:
    """Get one APQP project from public.projects by id."""
    if not project_id or not str(project_id).strip():
        return error_response("project_id is required.")
    return get_external_database_row("apqp", TABLE_APQP_PROJECT, row_id=str(project_id).strip())


@mcp.tool()
def create_apqp_project(
    name: str,
    project_code: str,
    project_id: Optional[str] = None,
    source_request_id: Optional[str] = None,
    customer: Optional[str] = None,
    plant: Optional[str] = None,
    product_line: Optional[str] = None,
    product_group: Optional[str] = None,
    project_phase: int = 0,
    project_status: Optional[str] = "Active",
    project_timeline_status: Optional[str] = None,
    project_delay_category: Optional[str] = None,
    project_manager: Optional[str] = None,
    project_manager_email: Optional[str] = None,
    project_owner_name: Optional[str] = None,
    project_owner_email: Optional[str] = None,
    vp_function: Optional[str] = None,
    vp_email: Optional[str] = None,
    project_category: Optional[str] = None,
    year: Optional[int] = None,
    sop_target_date: Optional[str] = None,
    sop_planned_date: Optional[str] = None,
    part_number: Optional[str] = None,
    share_point_link: Optional[str] = None,
    monday_planning_board: Optional[str] = None,
    project_updates: Optional[List[Dict[str, Any]]] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create one APQP project in public.projects. project_id is generated as a
    UUID string when omitted. Required fields: name and project_code.
    """
    err = _require_text(name, "name")
    if err:
        return err
    err = _require_text(project_code, "project_code")
    if err:
        return err
    try:
        payload = merge_create_payload({
            "id": str(project_id or uuid4()),
            "source_request_id": source_request_id,
            "name": name.strip(),
            "project_code": project_code.strip(),
            "customer": customer,
            "plant": plant,
            "product_line": product_line,
            "product_group": product_group,
            "project_phase": project_phase,
            "project_status": project_status,
            "project_timeline_status": project_timeline_status,
            "project_delay_category": project_delay_category,
            "project_manager": project_manager,
            "project_manager_email": project_manager_email,
            "project_owner_name": project_owner_name,
            "project_owner_email": project_owner_email,
            "vp_function": vp_function,
            "vp_email": vp_email,
            "project_category": project_category,
            "year": year,
            "sop_target_date": sop_target_date,
            "sop_planned_date": sop_planned_date,
            "part_number": part_number,
            "share_point_link": share_point_link,
            "monday_planning_board": monday_planning_board,
            "project_updates": [] if project_updates is None else project_updates,
        }, extra_fields)
        return create_external_database_row("apqp", TABLE_APQP_PROJECT, payload)
    except Exception as exc:
        logger.exception("create_apqp_project failed")
        return error_response("Failed to create APQP project.", exc)


@mcp.tool()
def update_apqp_project(
    project_id: str,
    fields: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
    source_request_id: Optional[str] = None,
    project_code: Optional[str] = None,
    customer: Optional[str] = None,
    plant: Optional[str] = None,
    product_line: Optional[str] = None,
    product_group: Optional[str] = None,
    project_phase: Optional[int] = None,
    project_status: Optional[str] = None,
    project_timeline_status: Optional[str] = None,
    project_delay_category: Optional[str] = None,
    project_manager: Optional[str] = None,
    project_manager_email: Optional[str] = None,
    project_owner_name: Optional[str] = None,
    project_owner_email: Optional[str] = None,
    vp_function: Optional[str] = None,
    vp_email: Optional[str] = None,
    project_category: Optional[str] = None,
    year: Optional[int] = None,
    sop_target_date: Optional[str] = None,
    sop_actual_date: Optional[str] = None,
    sop_planned_date: Optional[str] = None,
    ppap_submission_target_date: Optional[str] = None,
    ppap_submission_actual_date: Optional[str] = None,
    part_number: Optional[str] = None,
    project_updates: Optional[List[Dict[str, Any]]] = None,
    deliverables: Optional[List[Dict[str, Any]]] = None,
    handover_steps: Optional[List[Dict[str, Any]]] = None,
    attachment_metadata: Optional[List[Dict[str, Any]]] = None,
    is_archived: Optional[bool] = None,
    risk_level: Optional[str] = None,
    ppap_status: Optional[str] = None,
    is_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Update one APQP project in public.projects by id. Pass only fields to change."""
    if not project_id or not str(project_id).strip():
        return error_response("project_id is required.")
    try:
        payload = merge_update_payload({
            "name": name,
            "source_request_id": source_request_id,
            "project_code": project_code,
            "customer": customer,
            "plant": plant,
            "product_line": product_line,
            "product_group": product_group,
            "project_phase": project_phase,
            "project_status": project_status,
            "project_timeline_status": project_timeline_status,
            "project_delay_category": project_delay_category,
            "project_manager": project_manager,
            "project_manager_email": project_manager_email,
            "project_owner_name": project_owner_name,
            "project_owner_email": project_owner_email,
            "vp_function": vp_function,
            "vp_email": vp_email,
            "project_category": project_category,
            "year": year,
            "sop_target_date": sop_target_date,
            "sop_actual_date": sop_actual_date,
            "sop_planned_date": sop_planned_date,
            "ppap_submission_target_date": ppap_submission_target_date,
            "ppap_submission_actual_date": ppap_submission_actual_date,
            "part_number": part_number,
            "project_updates": project_updates,
            "deliverables": deliverables,
            "handover_steps": handover_steps,
            "attachment_metadata": attachment_metadata,
            "is_archived": is_archived,
            "risk_level": risk_level,
            "ppap_status": ppap_status,
            "is_status": is_status,
        }, fields)
        payload.setdefault("updated_at", datetime.utcnow())
        return update_external_database_row("apqp", TABLE_APQP_PROJECT, payload, row_id=str(project_id).strip())
    except Exception as exc:
        logger.exception("update_apqp_project failed")
        return error_response("Failed to update APQP project.", exc)


@mcp.tool()
def delete_apqp_project(project_id: str) -> Dict[str, Any]:
    """Delete one APQP project from public.projects by id."""
    if not project_id or not str(project_id).strip():
        return error_response("project_id is required.")
    return delete_external_database_row("apqp", TABLE_APQP_PROJECT, row_id=str(project_id).strip())

def _log_event(
    conn, action_id: int, event_type: str,
    old_value: Optional[str] = None, new_value: Optional[str] = None,
    details: Optional[str] = None, created_by: Optional[str] = None,
) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT action_event_log_sp")
            cur.execute(
                """INSERT INTO action_event_log
                   (action_id, event_type, old_value, new_value, details, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (action_id, event_type, old_value, new_value, details, created_by),
            )
            cur.execute("RELEASE SAVEPOINT action_event_log_sp")
    except Exception:
        try:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT action_event_log_sp")
                cur.execute("RELEASE SAVEPOINT action_event_log_sp")
        except Exception:
            pass
        logger.warning("Could not write action_event_log for action_id=%s", action_id, exc_info=True)


# ============================================================================
# Customer tools  (KPI_DB_Final → `customer` table, via "people" selector)
#
# Replaces the former client/CRM tools. All operations target KPI_DB_Final.
# Tables: customer, customer_delivery_unit, customer_file, customer_project.
# customer.customer_id has no DEFAULT nextval, so create_customer assigns the
# next id explicitly within the same transaction.
# ============================================================================

@mcp.tool()
def create_customer(
    customer_name: str,
    customer_type: str,
    customer_status: str = "Active",
    customer_code: Optional[str] = None,
    parent_customer_id: Optional[int] = None,
    headquarter_country: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    main_sales_people_id: Optional[int] = None,
    main_kam_people_id: Optional[int] = None,
    strategic_level: Optional[str] = None,
    customer_scope: Optional[str] = None,
    customer_files_link: Optional[str] = None,
    comments: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a customer in KPI_DB_Final.
    customer_type: Customer Group | Customer Legal Entity | Manufacturing Site |
                   Purchasing Center | Design Center | R&D Center |
                   Distribution Center | Sales Office | Aftermarket Unit.
    customer_status: Active | Prospect | Dormant | Lost | Blocked | Archived.
    strategic_level (optional): Strategic | Key Account | Standard |
                                Opportunistic | Low Priority.
    """
    if not customer_name or not customer_name.strip():
        return error_response("customer_name is required.")
    if customer_type not in VALID_CUSTOMER_TYPE:
        return error_response(f"customer_type must be one of: {_valid_values_text(VALID_CUSTOMER_TYPE)}.")
    if customer_status not in VALID_CUSTOMER_STATUS:
        return error_response(f"customer_status must be one of: {_valid_values_text(VALID_CUSTOMER_STATUS)}.")
    if strategic_level is not None and strategic_level not in VALID_STRATEGIC_LEVEL:
        return error_response(f"strategic_level must be one of: {_valid_values_text(VALID_STRATEGIC_LEVEL)}.")
    try:
        payload = merge_create_payload({
            "customer_name": customer_name, "customer_type": customer_type,
            "customer_status": customer_status, "customer_code": customer_code,
            "parent_customer_id": parent_customer_id,
            "headquarter_country": headquarter_country,
            "country": country, "city": city,
            "zone_id": zone_id, "market_id": market_id,
            "main_sales_people_id": main_sales_people_id,
            "main_kam_people_id": main_kam_people_id,
            "strategic_level": strategic_level,
            "customer_scope": customer_scope,
            "customer_files_link": customer_files_link,
            "comments": comments,
        }, extra_fields)
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                # customer_id is not auto-generated — assign next id atomically.
                cur.execute(f"SELECT COALESCE(MAX(customer_id), 0) + 1 AS next_id FROM {TABLE_CUSTOMER}")
                payload["customer_id"] = cur.fetchone()["next_id"]
            row = insert_row(conn, TABLE_CUSTOMER, payload)
        return success_response(row, "Customer created.")
    except Exception as exc:
        logger.exception("create_customer failed")
        return error_response("Failed to create customer.", exc)


@mcp.tool()
def get_customer(customer_id: int, include_related: bool = True) -> Dict[str, Any]:
    """
    Get a customer from KPI_DB_Final by customer_id.
    When include_related=True, also returns delivery units, files, projects,
    direct child customers, and the resolved sales/KAM contact names.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT c.*,
                           z.zone_name, m.market_name,
                           (sp.first_name || ' ' || sp.name) AS main_sales_name,
                           sp.email AS main_sales_email,
                           (kam.first_name || ' ' || kam.name) AS main_kam_name,
                           kam.email AS main_kam_email,
                           pc.customer_name AS parent_customer_name
                    FROM {TABLE_CUSTOMER} c
                    LEFT JOIN {TABLE_ZONE} z   ON z.zone_id = c.zone_id
                    LEFT JOIN {TABLE_MARKET} m ON m.market_id = c.market_id
                    LEFT JOIN {TABLE_PEOPLE} sp  ON sp.people_id = c.main_sales_people_id
                    LEFT JOIN {TABLE_PEOPLE} kam ON kam.people_id = c.main_kam_people_id
                    LEFT JOIN {TABLE_CUSTOMER} pc ON pc.customer_id = c.parent_customer_id
                    WHERE c.customer_id = %s
                    """,
                    (customer_id,),
                )
                customer = cur.fetchone()
                if customer is None:
                    return error_response(f"Customer {customer_id} not found.")
                customer = dict(customer)

                if include_related:
                    cur.execute(
                        f"""
                        SELECT d.customer_delivery_id, d.unit_id, u.unit_name,
                               d.product_id, p.product_name,
                               d.delivery_status, d.start_date, d.end_date, d.comments
                        FROM {TABLE_CUSTOMER_DELIVERY} d
                        LEFT JOIN {TABLE_UNIT} u ON u.unit_id = d.unit_id
                        LEFT JOIN product p ON p.product_id = d.product_id
                        WHERE d.customer_id = %s
                        ORDER BY d.customer_delivery_id
                        """,
                        (customer_id,),
                    )
                    customer["delivery_units"] = [dict(r) for r in cur.fetchall()]

                    cur.execute(
                        f"""
                        SELECT customer_file_id, file_name, file_type, file_url,
                               uploaded_at, uploaded_by_people_id
                        FROM {TABLE_CUSTOMER_FILE}
                        WHERE customer_id = %s
                        ORDER BY uploaded_at DESC NULLS LAST
                        """,
                        (customer_id,),
                    )
                    customer["files"] = [dict(r) for r in cur.fetchall()]

                    cur.execute(
                        f"""
                        SELECT customer_project_id, project_id,
                               customer_role_in_project, comments
                        FROM {TABLE_CUSTOMER_PROJECT}
                        WHERE customer_id = %s
                        ORDER BY customer_project_id
                        """,
                        (customer_id,),
                    )
                    customer["projects"] = [dict(r) for r in cur.fetchall()]

                    cur.execute(
                        f"""
                        SELECT customer_id, customer_name, customer_type, customer_status
                        FROM {TABLE_CUSTOMER}
                        WHERE parent_customer_id = %s
                        ORDER BY customer_name
                        """,
                        (customer_id,),
                    )
                    customer["child_customers"] = [dict(r) for r in cur.fetchall()]

        return success_response(customer)
    except Exception as exc:
        logger.exception("get_customer failed")
        return error_response("Failed to get customer.", exc)


@mcp.tool()
def list_customers(
    customer_status: Optional[str] = None,
    customer_type: Optional[str] = None,
    country: Optional[str] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    parent_customer_id: Optional[int] = None,
    strategic_level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List customers from KPI_DB_Final with optional filters.
    customer_status / customer_type / strategic_level are matched exactly
    (see create_customer for allowed values); country is matched with ILIKE.
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if customer_status:
            conditions.append("c.customer_status = %s"); params.append(customer_status)
        if customer_type:
            conditions.append("c.customer_type = %s"); params.append(customer_type)
        if country:
            conditions.append("c.country ILIKE %s"); params.append(f"%{country}%")
        if zone_id is not None:
            conditions.append("c.zone_id = %s"); params.append(zone_id)
        if market_id is not None:
            conditions.append("c.market_id = %s"); params.append(market_id)
        if parent_customer_id is not None:
            conditions.append("c.parent_customer_id = %s"); params.append(parent_customer_id)
        if strategic_level:
            conditions.append("c.strategic_level = %s"); params.append(strategic_level)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT c.customer_id, c.customer_name, c.customer_code,
                           c.customer_type, c.customer_status, c.strategic_level,
                           c.parent_customer_id, c.country, c.city,
                           c.zone_id, z.zone_name, c.market_id, m.market_name
                    FROM {TABLE_CUSTOMER} c
                    LEFT JOIN {TABLE_ZONE} z   ON z.zone_id = c.zone_id
                    LEFT JOIN {TABLE_MARKET} m ON m.market_id = c.market_id
                    {where}
                    ORDER BY c.customer_name
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_customers failed")
        return error_response("Failed to list customers.", exc)


@mcp.tool()
def search_customers(query: str, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """
    Search customers in KPI_DB_Final by customer_name, customer_code,
    tmp_global_name, or city (ILIKE).
    """
    if not query or not query.strip():
        return error_response("query is required.")
    if limit < 1 or limit > 200:
        return error_response("limit must be between 1 and 200.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    try:
        like = f"%{query.strip()}%"
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT c.customer_id, c.customer_name, c.customer_code,
                           c.customer_type, c.customer_status, c.strategic_level,
                           c.country, c.city, c.zone_id, c.market_id
                    FROM {TABLE_CUSTOMER} c
                    WHERE c.customer_name ILIKE %s
                       OR c.customer_code ILIKE %s
                       OR c.tmp_global_name ILIKE %s
                       OR c.city ILIKE %s
                    ORDER BY c.customer_name
                    LIMIT %s OFFSET %s
                    """,
                    (like, like, like, like, limit, offset),
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("search_customers failed")
        return error_response("Failed to search customers.", exc)


@mcp.tool()
def update_customer(
    customer_id: int,
    fields: Optional[Dict[str, Any]] = None,
    customer_name: Optional[str] = None,
    customer_type: Optional[str] = None,
    customer_status: Optional[str] = None,
    customer_code: Optional[str] = None,
    parent_customer_id: Optional[int] = None,
    headquarter_country: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    main_sales_people_id: Optional[int] = None,
    main_kam_people_id: Optional[int] = None,
    strategic_level: Optional[str] = None,
    customer_scope: Optional[str] = None,
    customer_files_link: Optional[str] = None,
    comments: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update a customer in KPI_DB_Final. Validated enum fields (customer_type,
    customer_status, strategic_level) are checked when provided.
    """
    if customer_type is not None and customer_type not in VALID_CUSTOMER_TYPE:
        return error_response(f"customer_type must be one of: {_valid_values_text(VALID_CUSTOMER_TYPE)}.")
    if customer_status is not None and customer_status not in VALID_CUSTOMER_STATUS:
        return error_response(f"customer_status must be one of: {_valid_values_text(VALID_CUSTOMER_STATUS)}.")
    if strategic_level is not None and strategic_level not in VALID_STRATEGIC_LEVEL:
        return error_response(f"strategic_level must be one of: {_valid_values_text(VALID_STRATEGIC_LEVEL)}.")
    try:
        payload = merge_update_payload({
            "customer_name": customer_name, "customer_type": customer_type,
            "customer_status": customer_status, "customer_code": customer_code,
            "parent_customer_id": parent_customer_id,
            "headquarter_country": headquarter_country,
            "country": country, "city": city,
            "zone_id": zone_id, "market_id": market_id,
            "main_sales_people_id": main_sales_people_id,
            "main_kam_people_id": main_kam_people_id,
            "strategic_level": strategic_level,
            "customer_scope": customer_scope,
            "customer_files_link": customer_files_link,
            "comments": comments,
        }, fields)
        with db_connection("people") as conn:
            row = update_row(conn, TABLE_CUSTOMER, CUSTOMER_PK, customer_id, payload)
        if row is None:
            return error_response(f"Customer {customer_id} not found.")
        return success_response(row, "Customer updated.")
    except Exception as exc:
        logger.exception("update_customer failed")
        return error_response("Failed to update customer.", exc)


@mcp.tool()
def delete_customer(customer_id: int) -> Dict[str, Any]:
    """
    Delete a customer from KPI_DB_Final.
    NOTE: customer_delivery_unit, customer_file and customer_project rows are
    removed by ON DELETE CASCADE. Deletion is blocked by the database if the
    customer is still referenced by a kpi_target_allocation.
    """
    try:
        with db_connection("people") as conn:
            row = delete_row(conn, TABLE_CUSTOMER, CUSTOMER_PK, customer_id)
        if row is None:
            return error_response(f"Customer {customer_id} not found.")
        return success_response(row, "Customer deleted.")
    except Exception as exc:
        logger.exception("delete_customer failed")
        return error_response("Failed to delete customer.", exc)


# ============================================================================
# People DB — People & directory tools  (PEOPLE_DATABASE_URL → KPI_DB_Final)
# ============================================================================

def _fetch_people_assignments(
    conn, where_clause: str, params: List[Any], limit: int, offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Query v_people_assignments with an arbitrary WHERE clause.
    Returns one row per active role assignment (people with no assignment
    appear once with NULL assignment columns).
    """
    with conn.cursor() as cur:
        sql_text = f"""
            SELECT
                people_id, first_name, last_name, email, people_status,
                hr_employee_id, hr_site,
                work_unit_id, work_unit_name, work_unit_type, work_country,
                assignment_id, assignment_status, assignment_type,
                is_primary, start_date, end_date,
                role_id, role_name, role_level, is_managerial,
                role_full_path, role_depth,
                role_unit_id, role_unit_name, role_unit_full_path,
                factory_name, dept_name,
                zone_id, zone_name,
                market_id, market_name
            FROM {VIEW_PEOPLE_ASSIGNMENTS}
            {where_clause}
            ORDER BY last_name, first_name, is_primary DESC, role_name
            LIMIT %s OFFSET %s
        """
        cur.execute(sql_text, params + [limit, offset])
        return [dict(r) for r in cur.fetchall()]


def _group_assignments_by_person(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse multiple assignment rows for the same person into a single record
    with an `assignments` list.

    NOTE: v_people_assignments LEFT JOINs assignments, so people with no
    active assignment come back with assignment_id = NULL. Those rows are
    kept (the person record exists) but no NULL assignment entry is added.
    """
    from collections import OrderedDict
    people: "OrderedDict[int, Dict[str, Any]]" = OrderedDict()
    for row in rows:
        pid = row["people_id"]
        if pid not in people:
            people[pid] = {
                "people_id":      row["people_id"],
                "first_name":     row["first_name"],
                "last_name":      row["last_name"],
                "email":          row["email"],
                "people_status":  row["people_status"],
                "hr_employee_id": row["hr_employee_id"],
                "hr_site":        row["hr_site"],
                "work_unit_id":   row["work_unit_id"],
                "work_unit_name": row["work_unit_name"],
                "work_unit_type": row["work_unit_type"],
                "work_country":   row["work_country"],
                "assignments":    [],
            }
        if row.get("assignment_id") is None:
            continue  # person without active assignment — keep profile, skip NULL entry
        people[pid]["assignments"].append({
            "assignment_id":       row["assignment_id"],
            "assignment_status":   row["assignment_status"],
            "assignment_type":     row["assignment_type"],
            "is_primary":          row["is_primary"],
            "start_date":          row["start_date"],
            "end_date":            row["end_date"],
            "role_id":             row["role_id"],
            "role_name":           row["role_name"],
            "role_level":          row["role_level"],
            "is_managerial":       row["is_managerial"],
            "role_full_path":      row["role_full_path"],
            "role_depth":          row["role_depth"],
            "role_unit_id":        row["role_unit_id"],
            "role_unit_name":      row["role_unit_name"],
            "role_unit_full_path": row["role_unit_full_path"],
            "factory_name":        row["factory_name"],
            "dept_name":           row["dept_name"],
            "zone_id":             row["zone_id"],
            "zone_name":           row["zone_name"],
            "market_id":           row["market_id"],
            "market_name":         row["market_name"],
        })
    return list(people.values())


# ── People tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def search_member(
    query: str,
    country: Optional[str] = None,
    factory_name: Optional[str] = None,
    dept_name: Optional[str] = None,
    role_level: Optional[str] = None,
    zone_name: Optional[str] = None,
    market_name: Optional[str] = None,
    people_status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Search KPI_DB_Final people by name, first_name, or email (ILIKE).
    Optional filters: country, factory_name, dept_name, role_level,
    zone_name, market_name, people_status (case-insensitive, e.g. 'Active').
    Returns grouped results — one record per person with their assignments list.
    """
    if not query or not query.strip():
        return error_response("query is required.")
    if limit < 1 or limit > 200:
        return error_response("limit must be between 1 and 200.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")

    try:
        like = f"%{query.strip()}%"
        # Also match full names in either order ("Ines Gharbi" / "Gharbi Ines"):
        # assistants usually pass the whole name, which no single column contains.
        conditions = ["(last_name ILIKE %s OR first_name ILIKE %s OR email ILIKE %s"
                      " OR (first_name || ' ' || last_name) ILIKE %s OR (last_name || ' ' || first_name) ILIKE %s)"]
        params: List[Any] = [like, like, like, like, like]

        if country:
            conditions.append("work_country ILIKE %s")
            params.append(f"%{country}%")
        if factory_name:
            conditions.append("factory_name ILIKE %s")
            params.append(f"%{factory_name}%")
        if dept_name:
            conditions.append("dept_name ILIKE %s")
            params.append(f"%{dept_name}%")
        if role_level:
            conditions.append("role_level = %s")
            params.append(role_level)
        if zone_name:
            conditions.append("zone_name ILIKE %s")
            params.append(f"%{zone_name}%")
        if market_name:
            conditions.append("market_name ILIKE %s")
            params.append(f"%{market_name}%")
        if people_status:
            # KPI_DB_Final stores 'Active' (mixed case) — compare case-insensitively
            conditions.append("UPPER(people_status) = UPPER(%s)")
            params.append(people_status)

        where = "WHERE " + " AND ".join(conditions)

        with db_connection("people") as conn:
            rows = _fetch_people_assignments(conn, where, params, limit * 3, offset)
            grouped = _group_assignments_by_person(rows)[:limit]

            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(DISTINCT people_id) AS total FROM {VIEW_PEOPLE_ASSIGNMENTS} {where}",
                    params,
                )
                total = cur.fetchone()["total"]

        return success_response(grouped, count=len(grouped), total=total)
    except Exception as exc:
        logger.exception("search_member failed")
        return error_response("Failed to search members.", exc)


@mcp.tool()
def get_member(people_id: int) -> Dict[str, Any]:
    """
    Get a person from KPI_DB_Final by people_id.
    Returns the person's core profile plus all active role assignments.
    """
    try:
        with db_connection("people") as conn:
            rows = _fetch_people_assignments(
                conn, "WHERE people_id = %s", [people_id], limit=50,
            )
        if not rows:
            return error_response(f"Person {people_id} not found.")
        grouped = _group_assignments_by_person(rows)
        return success_response(grouped[0])
    except Exception as exc:
        logger.exception("get_member failed")
        return error_response("Failed to get member.", exc)


@mcp.tool()
def find_member_by_email(email: str) -> Dict[str, Any]:
    """
    Resolve a person in KPI_DB_Final by exact email (case-insensitive).
    Returns the person profile with all their active role assignments.
    """
    if not email or "@" not in email:
        return error_response("A valid email address is required.")
    try:
        with db_connection("people") as conn:
            rows = _fetch_people_assignments(
                conn, "WHERE email ILIKE %s", [email.strip()], limit=50,
            )
        if not rows:
            return error_response(f"No person found with email '{email}'.")
        grouped = _group_assignments_by_person(rows)
        return success_response(grouped[0])
    except Exception as exc:
        logger.exception("find_member_by_email failed")
        return error_response("Failed to find member by email.", exc)


@mcp.tool()
def list_members(
    people_status: str = PEOPLE_STATUS_ACTIVE,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List people from KPI_DB_Final.
    Defaults to people_status = 'Active' (data convention in KPI_DB_Final;
    comparison is case-insensitive, so 'ACTIVE' also works).
    Pass people_status='' to get all.
    Returns grouped results — one record per person with their assignments list.
    """
    invalid = validate_limit_offset(limit, offset, max_limit=500)
    if invalid is not None:
        return invalid
    try:
        if people_status:
            where  = "WHERE UPPER(people_status) = UPPER(%s)"
            params: List[Any] = [people_status]
        else:
            where  = ""
            params = []

        effective_limit = (limit or 200)
        with db_connection("people") as conn:
            rows    = _fetch_people_assignments(conn, where, params, effective_limit * 3, offset)
            grouped = _group_assignments_by_person(rows)[:effective_limit]
        return success_response(grouped, count=len(grouped))
    except Exception as exc:
        logger.exception("list_members failed")
        return error_response("Failed to list members.", exc)


@mcp.tool()
def list_members_by_unit(
    unit_id: Optional[int] = None,
    unit_name: Optional[str] = None,
    include_sub_units: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List all people assigned to a specific org unit (factory or department)
    in KPI_DB_Final. Provide either unit_id or unit_name (partial match).
    Set include_sub_units=True to include people in child units.
    """
    if unit_id is None and not unit_name:
        return error_response("Provide unit_id or unit_name.")
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        conditions: List[str] = []
        params: List[Any] = []

        if unit_id is not None:
            if include_sub_units:
                conditions.append("""
                    role_unit_id IN (
                        SELECT unit_id FROM v_unit_tree
                        WHERE %s = ANY(id_path)
                    )
                """)
                params.append(unit_id)
            else:
                conditions.append("role_unit_id = %s")
                params.append(unit_id)
        elif unit_name:
            if include_sub_units:
                conditions.append("""
                    role_unit_id IN (
                        SELECT unit_id FROM v_unit_tree
                        WHERE full_path ILIKE %s
                    )
                """)
                params.append(f"%{unit_name}%")
            else:
                conditions.append("role_unit_name ILIKE %s")
                params.append(f"%{unit_name}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with db_connection("people") as conn:
            rows    = _fetch_people_assignments(conn, where, params, limit * 3, offset)
            grouped = _group_assignments_by_person(rows)[:limit]
        return success_response(grouped, count=len(grouped))
    except Exception as exc:
        logger.exception("list_members_by_unit failed")
        return error_response("Failed to list members by unit.", exc)


@mcp.tool()
def list_members_by_role(
    role_id: Optional[int] = None,
    role_name: Optional[str] = None,
    include_sub_roles: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List all people holding a specific role in KPI_DB_Final.
    Provide role_id or role_name (partial match).
    Set include_sub_roles=True to include all child roles in the hierarchy.
    """
    if role_id is None and not role_name:
        return error_response("Provide role_id or role_name.")
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        conditions: List[str] = []
        params: List[Any] = []

        if role_id is not None:
            if include_sub_roles:
                conditions.append("""
                    role_id IN (
                        SELECT role_id FROM v_role_tree
                        WHERE %s = ANY(id_path)
                    )
                """)
                params.append(role_id)
            else:
                conditions.append("role_id = %s")
                params.append(role_id)
        elif role_name:
            if include_sub_roles:
                conditions.append("""
                    role_id IN (
                        SELECT role_id FROM v_role_tree
                        WHERE role_full_path ILIKE %s OR role_name ILIKE %s
                    )
                """)
                params.extend([f"%{role_name}%", f"%{role_name}%"])
            else:
                conditions.append("role_name ILIKE %s")
                params.append(f"%{role_name}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with db_connection("people") as conn:
            rows    = _fetch_people_assignments(conn, where, params, limit * 3, offset)
            grouped = _group_assignments_by_person(rows)[:limit]
        return success_response(grouped, count=len(grouped))
    except Exception as exc:
        logger.exception("list_members_by_role failed")
        return error_response("Failed to list members by role.", exc)


@mcp.tool()
def get_direct_reports(manager_people_id: int) -> Dict[str, Any]:
    """
    Return all people who directly report to the given manager in KPI_DB_Final.
    Uses v_people_with_boss.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        people_id, first_name, last_name, person, email,
                        role_id, role_name, role_level,
                        unit_id, factory, country,
                        zone_id, zone_name,
                        boss_people_id, boss_person, boss_email,
                        boss_role_id, boss_role, hierarchy_path
                    FROM {VIEW_PEOPLE_WITH_BOSS}
                    WHERE boss_people_id = %s
                    ORDER BY last_name, first_name
                    """,
                    (manager_people_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_direct_reports failed")
        return error_response("Failed to get direct reports.", exc)


@mcp.tool()
def get_org_path(people_id: int) -> Dict[str, Any]:
    """
    Walk up the org hierarchy from a person to the root manager using a single
    recursive CTE on v_people_with_boss.
    Returns an ordered list from the person (depth=0) up to the root.
    A depth guard of 20 levels prevents infinite loops on circular data.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH RECURSIVE org_path AS (
                        SELECT
                            people_id, person, email,
                            role_name, role_level, factory,
                            boss_people_id, 0 AS depth
                        FROM {VIEW_PEOPLE_WITH_BOSS}
                        WHERE people_id = %s

                        UNION ALL

                        SELECT
                            v.people_id, v.person, v.email,
                            v.role_name, v.role_level, v.factory,
                            v.boss_people_id, op.depth + 1
                        FROM {VIEW_PEOPLE_WITH_BOSS} v
                        INNER JOIN org_path op ON op.boss_people_id = v.people_id
                        WHERE op.boss_people_id IS NOT NULL
                          AND op.depth < 20
                    )
                    SELECT people_id, person, email, role_name,
                           role_level, factory, depth
                    FROM org_path
                    ORDER BY depth ASC
                    """,
                    (people_id,),
                )
                path = [dict(r) for r in cur.fetchall()]

        if not path:
            return error_response(f"Person {people_id} not found.")

        return success_response(path, count=len(path))
    except Exception as exc:
        logger.exception("get_org_path failed")
        return error_response("Failed to get org path.", exc)


# ── Reference data tools (roles, units, zones, markets) ──────────────────────

@mcp.tool()
def list_roles(
    is_active: bool = True,
    role_level: Optional[str] = None,
    is_managerial: Optional[bool] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List roles from KPI_DB_Final with optional filters.
    Includes role hierarchy path from v_role_tree.
    """
    invalid = validate_limit_offset(limit, offset, max_limit=500)
    if invalid is not None:
        return invalid
    try:
        conditions = ["r.is_active = %s"]
        params: List[Any] = [is_active]

        if role_level:
            conditions.append("r.role_level = %s")
            params.append(role_level)
        if is_managerial is not None:
            conditions.append("r.is_managerial = %s")
            params.append(is_managerial)

        where = " AND ".join(conditions)

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                lim_clause = " LIMIT %s"  if limit  else ""
                off_clause = " OFFSET %s" if offset else ""
                if limit:
                    params.append(limit)
                if offset:
                    params.append(offset)

                cur.execute(
                    f"""
                    SELECT r.role_id, r.role_name, r.normalized_role_name,
                           r.role_slug, r.role_level, r.role_family,
                           r.is_managerial, r.is_active,
                           r.parent_role_id,
                           vr.full_path AS role_full_path,
                           vr.depth AS role_depth
                    FROM {TABLE_ROLE} r
                    LEFT JOIN v_role_tree vr ON vr.role_id = r.role_id
                    WHERE {where}
                    ORDER BY vr.depth NULLS LAST, r.role_name
                    {lim_clause}{off_clause}
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_roles failed")
        return error_response("Failed to list roles.", exc)


@mcp.tool()
def get_role(role_id: int) -> Dict[str, Any]:
    """
    Get full details of a role from KPI_DB_Final including hierarchy path,
    the people currently holding it (via v_people_assignments), and child roles.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT r.*, vr.full_path AS role_full_path, vr.depth AS role_depth
                    FROM {TABLE_ROLE} r
                    LEFT JOIN v_role_tree vr ON vr.role_id = r.role_id
                    WHERE r.role_id = %s
                    """,
                    (role_id,),
                )
                role = cur.fetchone()
                if role is None:
                    return error_response(f"Role {role_id} not found.")
                role = dict(role)

                cur.execute(
                    f"""
                    SELECT
                        people_id, first_name, last_name, email,
                        assignment_id,
                        work_unit_id, work_unit_name, work_country,
                        zone_id, zone_name,
                        is_primary, assignment_type
                    FROM {VIEW_PEOPLE_ASSIGNMENTS}
                    WHERE role_id = %s
                      AND assignment_status = 'ACTIVE'
                    ORDER BY last_name, first_name
                    """,
                    (role_id,),
                )
                holders = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""
                    SELECT role_id, role_name, role_level, is_managerial, is_active
                    FROM {TABLE_ROLE}
                    WHERE parent_role_id = %s AND is_active = TRUE
                    ORDER BY role_name
                    """,
                    (role_id,),
                )
                children = [dict(r) for r in cur.fetchall()]

        role["current_holders"] = holders
        role["child_roles"]     = children
        return success_response(role)
    except Exception as exc:
        logger.exception("get_role failed")
        return error_response("Failed to get role.", exc)


@mcp.tool()
def list_kpi_units(
    country: Optional[str] = None,
    unit_type_name: Optional[str] = None,
    parent_unit_id: Optional[int] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List org units from KPI_DB_Final with optional filters.
    Includes full hierarchy path from v_unit_tree.
    """
    invalid = validate_limit_offset(limit, offset, max_limit=500)
    if invalid is not None:
        return invalid
    try:
        conditions: List[str] = []
        params: List[Any] = []

        if country:
            conditions.append("u.country ILIKE %s")
            params.append(f"%{country}%")
        if unit_type_name:
            conditions.append("ut.unit_type_name ILIKE %s")
            params.append(f"%{unit_type_name}%")
        if parent_unit_id is not None:
            conditions.append("u.parent_unit_id = %s")
            params.append(parent_unit_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                lim_clause = " LIMIT %s"  if limit  else ""
                off_clause = " OFFSET %s" if offset else ""
                if limit:
                    params.append(limit)
                if offset:
                    params.append(offset)

                cur.execute(
                    f"""
                    SELECT u.unit_id, u.parent_unit_id, u.unit_name, u.unit_short_name,
                           u.country, u.city, u.status_id,
                           ut.unit_type_name,
                           vut.full_path AS unit_full_path,
                           vut.depth AS unit_depth,
                           vut.factory_name, vut.dept_name
                    FROM {TABLE_UNIT} u
                    JOIN unit_type ut ON ut.unit_type_id = u.unit_type_id
                    LEFT JOIN v_unit_tree vut ON vut.unit_id = u.unit_id
                    {where}
                    ORDER BY vut.depth NULLS LAST, u.unit_name
                    {lim_clause}{off_clause}
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_kpi_units failed")
        return error_response("Failed to list KPI units.", exc)


@mcp.tool()
def get_kpi_unit(unit_id: int) -> Dict[str, Any]:
    """
    Get full details of an org unit from KPI_DB_Final including its hierarchy
    path and a headcount of active people assigned to it.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT u.*, ut.unit_type_name,
                           vut.full_path AS unit_full_path,
                           vut.depth AS unit_depth,
                           vut.factory_name, vut.dept_name
                    FROM {TABLE_UNIT} u
                    JOIN unit_type ut ON ut.unit_type_id = u.unit_type_id
                    LEFT JOIN v_unit_tree vut ON vut.unit_id = u.unit_id
                    WHERE u.unit_id = %s
                    """,
                    (unit_id,),
                )
                unit = cur.fetchone()
                if unit is None:
                    return error_response(f"Unit {unit_id} not found.")
                unit = dict(unit)

                # Headcount — people_status is 'Active' (mixed case) in
                # KPI_DB_Final, assignment_status is 'ACTIVE': compare safely.
                cur.execute(
                    f"""
                    SELECT COUNT(DISTINCT people_id) AS headcount
                    FROM {VIEW_PEOPLE_ASSIGNMENTS}
                    WHERE work_unit_id = %s
                      AND assignment_status = 'ACTIVE'
                      AND UPPER(people_status) = 'ACTIVE'
                    """,
                    (unit_id,),
                )
                unit["active_headcount"] = cur.fetchone()["headcount"]

                if unit.get("manager_id"):
                    cur.execute(
                        f"""
                        SELECT people_id, first_name, last_name, email
                        FROM {VIEW_PEOPLE_ASSIGNMENTS}
                        WHERE people_id = %s
                        LIMIT 1
                        """,
                        (unit["manager_id"],),
                    )
                    mgr = cur.fetchone()
                    unit["manager"] = dict(mgr) if mgr else None

                cur.execute(
                    f"""
                    SELECT u2.unit_id, u2.unit_name, ut2.unit_type_name, u2.country
                    FROM {TABLE_UNIT} u2
                    JOIN unit_type ut2 ON ut2.unit_type_id = u2.unit_type_id
                    WHERE u2.parent_unit_id = %s
                    ORDER BY u2.unit_name
                    """,
                    (unit_id,),
                )
                unit["child_units"] = [dict(r) for r in cur.fetchall()]

        return success_response(unit)
    except Exception as exc:
        logger.exception("get_kpi_unit failed")
        return error_response("Failed to get KPI unit.", exc)


@mcp.tool()
def list_zones(limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """List all zones from KPI_DB_Final."""
    invalid = validate_limit_offset(limit, offset, max_limit=200)
    if invalid is not None:
        return invalid
    try:
        with db_connection("people") as conn:
            rows = list_rows(conn, TABLE_ZONE, ("zone_id",), limit, offset)
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_zones failed")
        return error_response("Failed to list zones.", exc)


@mcp.tool()
def list_markets(limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """List all markets from KPI_DB_Final."""
    invalid = validate_limit_offset(limit, offset, max_limit=200)
    if invalid is not None:
        return invalid
    try:
        with db_connection("people") as conn:
            rows = list_rows(conn, TABLE_MARKET, ("market_id",), limit, offset)
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_markets failed")
        return error_response("Failed to list markets.", exc)


# ============================================================================
# KPI tools  (KPI_DB_Final) — attributes, targets, values, responsibles
# ============================================================================

KPI_ATTRIBUTE_COLUMNS = """
    k.kpi_id, k.kpi_name, k.kpi_sub_title, k.kpi_code,
    k.kpi_explanation, k.kpi_formula,
    k.target_value, k.target_direction, k.target_auto_adjustment,
    k.min_value, k.min_type, k.max_value, k.max_type,
    k.low_limit, k.high_limit,
    k.tolerance_type, k.low_tolerance, k.up_tolerance,
    k.unit, k.frequency, k.nombre_periode,
    k.calculation_mode, k.calculation_on,
    k.display_trend, k.regression,
    k.reference_kpi_id, k.owner_role_id,
    k.status, k.comments, k.created_at, k.updated_at,
    k.kpi_knowledge_updated_date
"""


@mcp.tool()
def list_kpis(
    query: Optional[str] = None,
    status: Optional[str] = None,
    frequency: Optional[str] = None,
    owner_role_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List KPI definitions (attributes) from KPI_DB_Final.
    query searches kpi_name / kpi_code / kpi_sub_title / kpi_explanation (ILIKE).
    Optional filters: status (e.g. 'Active'), frequency, owner_role_id.
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    try:
        conditions: List[str] = []
        params: List[Any] = []

        if query and query.strip():
            like = f"%{query.strip()}%"
            conditions.append(
                "(k.kpi_name ILIKE %s OR k.kpi_code ILIKE %s "
                "OR k.kpi_sub_title ILIKE %s OR k.kpi_explanation ILIKE %s)"
            )
            params.extend([like, like, like, like])
        if status:
            conditions.append("UPPER(k.status) = UPPER(%s)")
            params.append(status)
        if frequency:
            conditions.append("k.frequency ILIKE %s")
            params.append(frequency)
        if owner_role_id is not None:
            conditions.append("k.owner_role_id = %s")
            params.append(owner_role_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {KPI_ATTRIBUTE_COLUMNS},
                           owner_r.role_name AS owner_role_name,
                           (SELECT COUNT(*) FROM {TABLE_KPI_ALLOCATION} a
                             WHERE a.kpi_id = k.kpi_id) AS allocation_count
                    FROM {TABLE_KPI} k
                    LEFT JOIN {TABLE_ROLE} owner_r ON owner_r.role_id = k.owner_role_id
                    {where}
                    ORDER BY k.kpi_name
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_kpis failed")
        return error_response("Failed to list KPIs.", exc)


@mcp.tool()
def get_kpi(kpi_id: int, include_knowledge_base: bool = False) -> Dict[str, Any]:
    """
    Get the full attributes of one KPI: definition, formula, tolerances,
    owner role, primary subject, and its target allocations summary.
    Set include_knowledge_base=True to also return the kpi_knowledge_base jsonb.
    """
    try:
        kb_col = ", k.kpi_knowledge_base" if include_knowledge_base else ""
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {KPI_ATTRIBUTE_COLUMNS}{kb_col},
                           owner_r.role_name AS owner_role_name,
                           s.subject_id AS primary_subject_id,
                           s.subject_name AS primary_subject_name
                    FROM {TABLE_KPI} k
                    LEFT JOIN {TABLE_ROLE} owner_r ON owner_r.role_id = k.owner_role_id
                    LEFT JOIN {TABLE_SUBJECT_KPI} sk
                           ON sk.kpi_id = k.kpi_id AND sk.is_primary = TRUE
                    LEFT JOIN {TABLE_SUBJECT} s ON s.subject_id = sk.subject_id
                    WHERE k.kpi_id = %s
                    """,
                    (kpi_id,),
                )
                kpi = cur.fetchone()
                if kpi is None:
                    return error_response(f"KPI {kpi_id} not found.")
                kpi = dict(kpi)

                cur.execute(
                    f"""
                    SELECT a.kpi_target_allocation_id, a.target_value, a.target_unit,
                           a.target_status, a.kpi_type,
                           a.target_start_date, a.target_end_date,
                           a.plant_id, plant.unit_name AS plant_name,
                           a.unit_id, d.unit_name AS unit_name,
                           a.role_id, r.role_name,
                           a.zone_id, z.zone_name
                    FROM {TABLE_KPI_ALLOCATION} a
                    LEFT JOIN {TABLE_UNIT} plant ON plant.unit_id = a.plant_id
                    LEFT JOIN {TABLE_UNIT} d     ON d.unit_id = a.unit_id
                    LEFT JOIN {TABLE_ROLE} r     ON r.role_id = a.role_id
                    LEFT JOIN {TABLE_ZONE} z     ON z.zone_id = a.zone_id
                    WHERE a.kpi_id = %s
                    ORDER BY a.target_status, plant.unit_name NULLS LAST
                    """,
                    (kpi_id,),
                )
                kpi["allocations"] = [dict(r) for r in cur.fetchall()]
        return success_response(kpi)
    except Exception as exc:
        logger.exception("get_kpi failed")
        return error_response("Failed to get KPI.", exc)


@mcp.tool()
def get_kpi_full_context(
    kpi_id: Optional[int] = None,
    plant_name: Optional[str] = None,
    role_name: Optional[str] = None,
    target_status: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    Query the flat v_kpi_full_context view: one row per KPI x allocation with
    plant, unit, zone, role, owner role, primary subject and market resolved.
    Filter by kpi_id, plant_name (ILIKE), role_name (ILIKE), target_status.
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if kpi_id is not None:
            conditions.append("kpi_id = %s")
            params.append(kpi_id)
        if plant_name:
            conditions.append("plant_name ILIKE %s")
            params.append(f"%{plant_name}%")
        if role_name:
            conditions.append("role_name ILIKE %s")
            params.append(f"%{role_name}%")
        if target_status:
            conditions.append("UPPER(target_status) = UPPER(%s)")
            params.append(target_status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT *
                    FROM {VIEW_KPI_FULL_CONTEXT}
                    {where}
                    ORDER BY kpi_name, plant_name NULLS LAST
                    LIMIT %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_kpi_full_context failed")
        return error_response("Failed to get KPI full context.", exc)


@mcp.tool()
def list_kpi_allocations(
    kpi_id: Optional[int] = None,
    plant_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    role_id: Optional[int] = None,
    target_status: Optional[str] = None,
    include_responsibles: bool = True,
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    List KPI target allocations (targets per plant / unit / role / zone).
    When include_responsibles=True, each allocation includes the people who
    actively hold the allocated role (the KPI responsibles), resolved through
    v_people_assignments and scoped to the allocation's unit when one is set.
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if kpi_id is not None:
            conditions.append("a.kpi_id = %s")
            params.append(kpi_id)
        if plant_id is not None:
            conditions.append("a.plant_id = %s")
            params.append(plant_id)
        if unit_id is not None:
            conditions.append("a.unit_id = %s")
            params.append(unit_id)
        if role_id is not None:
            conditions.append("a.role_id = %s")
            params.append(role_id)
        if target_status:
            conditions.append("UPPER(a.target_status) = UPPER(%s)")
            params.append(target_status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        responsibles_select = ""
        responsibles_join = ""
        if include_responsibles:
            responsibles_select = ", resp.responsibles"
            responsibles_join = f"""
                LEFT JOIN LATERAL (
                    SELECT json_agg(json_build_object(
                               'people_id', va.people_id,
                               'first_name', va.first_name,
                               'last_name', va.last_name,
                               'email', va.email,
                               'work_unit_name', va.work_unit_name,
                               'is_primary', va.is_primary
                           ) ORDER BY va.last_name, va.first_name) AS responsibles
                    FROM {VIEW_PEOPLE_ASSIGNMENTS} va
                    WHERE va.role_id = a.role_id
                      AND va.assignment_status = 'ACTIVE'
                      AND (a.unit_id IS NULL
                           OR va.role_unit_id = a.unit_id
                           OR va.work_unit_id  = a.unit_id)
                ) resp ON TRUE
            """

        params.extend([limit, offset])
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT a.kpi_target_allocation_id, a.kpi_id,
                           k.kpi_name, k.kpi_code, k.unit AS kpi_unit, k.frequency,
                           a.target_value, a.target_unit, a.local_currency,
                           a.kpi_type, a.target_status,
                           a.target_setup_date, a.target_start_date, a.target_end_date,
                           a.last_best_target, a.previous_target_value,
                           a.plant_id, plant.unit_name AS plant_name,
                           plant.country AS plant_country,
                           a.unit_id, d.unit_name AS unit_name,
                           a.role_id, r.role_name, r.role_level,
                           a.zone_id, z.zone_name,
                           a.market_id, m.market_name,
                           a.set_by_people_id, a.approved_by_people_id,
                           a.comments
                           {responsibles_select}
                    FROM {TABLE_KPI_ALLOCATION} a
                    JOIN {TABLE_KPI} k        ON k.kpi_id = a.kpi_id
                    LEFT JOIN {TABLE_UNIT} plant ON plant.unit_id = a.plant_id
                    LEFT JOIN {TABLE_UNIT} d     ON d.unit_id = a.unit_id
                    LEFT JOIN {TABLE_ROLE} r     ON r.role_id = a.role_id
                    LEFT JOIN {TABLE_ZONE} z     ON z.zone_id = a.zone_id
                    LEFT JOIN {TABLE_MARKET} m   ON m.market_id = a.market_id
                    {responsibles_join}
                    {where}
                    ORDER BY k.kpi_name, plant.unit_name NULLS LAST,
                             a.kpi_target_allocation_id
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_kpi_allocations failed")
        return error_response("Failed to list KPI allocations.", exc)


@mcp.tool()
def get_kpi_results(
    kpi_id: Optional[int] = None,
    kpi_target_allocation_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    period_type: Optional[str] = None,
    week: Optional[str] = None,
    result_status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Get KPI values (kpi_result rows) with their targets and limits.
    Filter by kpi_id and/or kpi_target_allocation_id, a date range
    (date_from / date_to, ISO 'YYYY-MM-DD'), period_type, week
    (e.g. '2026-Week23'), or result_status.
    Results are ordered most recent first and include plant/unit context.
    """
    if kpi_id is None and kpi_target_allocation_id is None:
        return error_response("Provide kpi_id or kpi_target_allocation_id.")
    if limit < 1 or limit > 1000:
        return error_response("limit must be between 1 and 1000.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if kpi_id is not None:
            conditions.append("res.kpi_id = %s")
            params.append(kpi_id)
        if kpi_target_allocation_id is not None:
            conditions.append("res.kpi_target_allocation_id = %s")
            params.append(kpi_target_allocation_id)
        if date_from:
            conditions.append("res.result_date >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("res.result_date <= %s")
            params.append(date_to)
        if period_type:
            conditions.append("res.period_type ILIKE %s")
            params.append(period_type)
        if week:
            conditions.append("res.week = %s")
            params.append(week)
        if result_status:
            conditions.append("UPPER(res.result_status) = UPPER(%s)")
            params.append(result_status)

        where = "WHERE " + " AND ".join(conditions)
        params.extend([limit, offset])

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT res.kpi_result_id, res.kpi_id, k.kpi_name,
                           res.kpi_target_allocation_id,
                           res.result_date, res.week, res.period_type, res.period_label,
                           res.raw_value, res.target_value,
                           res.lower_limit, res.upper_limit,
                           res.best_new_target, res.unit, res.kpi_type,
                           res.result_status, res.data_source,
                           res.recorded_by_people_id, res.recorded_at, res.comments,
                           plant.unit_name AS plant_name,
                           d.unit_name AS unit_name
                    FROM {TABLE_KPI_RESULT} res
                    JOIN {TABLE_KPI} k ON k.kpi_id = res.kpi_id
                    LEFT JOIN {TABLE_KPI_ALLOCATION} a
                           ON a.kpi_target_allocation_id = res.kpi_target_allocation_id
                    LEFT JOIN {TABLE_UNIT} plant ON plant.unit_id = a.plant_id
                    LEFT JOIN {TABLE_UNIT} d     ON d.unit_id = a.unit_id
                    {where}
                    ORDER BY res.result_date DESC, res.kpi_result_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_kpi_results failed")
        return error_response("Failed to get KPI results.", exc)


@mcp.tool()
def get_kpi_responsibles(kpi_id: int) -> Dict[str, Any]:
    """
    Resolve the people responsible for a KPI: for each active allocation of
    the KPI, the people actively holding the allocated role (scoped to the
    allocation unit when set), with their emails — ready for notifications.
    """
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT kpi_id, kpi_name, kpi_code FROM {TABLE_KPI} WHERE kpi_id = %s",
                    (kpi_id,),
                )
                kpi = cur.fetchone()
                if kpi is None:
                    return error_response(f"KPI {kpi_id} not found.")
                kpi = dict(kpi)

                cur.execute(
                    f"""
                    SELECT a.kpi_target_allocation_id, a.target_status,
                           a.plant_id, plant.unit_name AS plant_name,
                           a.unit_id, d.unit_name AS unit_name,
                           a.role_id, r.role_name,
                           va.people_id, va.first_name, va.last_name,
                           va.email, va.is_primary, va.work_unit_name
                    FROM {TABLE_KPI_ALLOCATION} a
                    LEFT JOIN {TABLE_UNIT} plant ON plant.unit_id = a.plant_id
                    LEFT JOIN {TABLE_UNIT} d     ON d.unit_id = a.unit_id
                    LEFT JOIN {TABLE_ROLE} r     ON r.role_id = a.role_id
                    LEFT JOIN {VIEW_PEOPLE_ASSIGNMENTS} va
                           ON va.role_id = a.role_id
                          AND va.assignment_status = 'ACTIVE'
                          AND (a.unit_id IS NULL
                               OR va.role_unit_id = a.unit_id
                               OR va.work_unit_id  = a.unit_id)
                    WHERE a.kpi_id = %s
                    ORDER BY a.kpi_target_allocation_id, va.last_name, va.first_name
                    """,
                    (kpi_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]

        allocations: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            alloc_id = row["kpi_target_allocation_id"]
            if alloc_id not in allocations:
                allocations[alloc_id] = {
                    "kpi_target_allocation_id": alloc_id,
                    "target_status": row["target_status"],
                    "plant_id":   row["plant_id"],
                    "plant_name": row["plant_name"],
                    "unit_id":    row["unit_id"],
                    "unit_name":  row["unit_name"],
                    "role_id":    row["role_id"],
                    "role_name":  row["role_name"],
                    "responsibles": [],
                }
            if row.get("people_id") is not None:
                allocations[alloc_id]["responsibles"].append({
                    "people_id":      row["people_id"],
                    "first_name":     row["first_name"],
                    "last_name":      row["last_name"],
                    "email":          row["email"],
                    "is_primary":     row["is_primary"],
                    "work_unit_name": row["work_unit_name"],
                })

        all_emails = sorted({
            p["email"] for a in allocations.values()
            for p in a["responsibles"] if p.get("email")
        })
        return success_response({
            "kpi":         kpi,
            "allocations": list(allocations.values()),
            "all_emails":  all_emails,
        })
    except Exception as exc:
        logger.exception("get_kpi_responsibles failed")
        return error_response("Failed to get KPI responsibles.", exc)


@mcp.tool()
def get_kpi_export(
    kpi_id: int,
    results_limit: int = 200,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One-call export of everything about a KPI, designed for the assistant to
    write a report file: attributes, all allocations with responsibles, and
    the most recent results (optionally bounded by date_from / date_to).
    """
    if results_limit < 1 or results_limit > 2000:
        return error_response("results_limit must be between 1 and 2000.")
    try:
        kpi_resp = get_kpi(kpi_id)
        if not kpi_resp.get("success"):
            return kpi_resp
        responsibles_resp = get_kpi_responsibles(kpi_id)
        results_resp = get_kpi_results(
            kpi_id=kpi_id, date_from=date_from, date_to=date_to,
            limit=results_limit,
        )
        return success_response({
            "kpi":          kpi_resp.get("data"),
            "responsibles": (responsibles_resp.get("data") or {}).get("allocations")
                            if responsibles_resp.get("success") else None,
            "all_emails":   (responsibles_resp.get("data") or {}).get("all_emails")
                            if responsibles_resp.get("success") else None,
            "results":      results_resp.get("data") if results_resp.get("success") else None,
            "results_count": results_resp.get("count", 0),
            "exported_at":  datetime.utcnow().isoformat() + "Z",
        }, "KPI export assembled.")
    except Exception as exc:
        logger.exception("get_kpi_export failed")
        return error_response("Failed to export KPI.", exc)

# ============================================================================
# KPI authoring tools  (KPI_DB_Final) — create/update KPI + target allocations
# Both kpi.kpi_id and kpi_target_allocation.kpi_target_allocation_id are
# GENERATED ALWAYS AS IDENTITY, so the id is NEVER supplied on insert — the DB
# assigns it and insert_row() already excludes generated columns.
# ============================================================================

def _row_exists(cur, table: str, pk_col: str, value: Any) -> bool:
    """True if a row with pk_col = value exists (FK pre-check → clear errors)."""
    cur.execute(
        sql.SQL("SELECT 1 FROM {t} WHERE {c} = %s LIMIT 1").format(
            t=sql.Identifier(table), c=sql.Identifier(pk_col)
        ),
        (value,),
    )
    return cur.fetchone() is not None


def _validate_allocation_fks(cur, alloc: Dict[str, Any]) -> Optional[str]:
    """Check every FK in an allocation payload. Returns first error or None.
    Only keys present (and non-None) are checked."""
    fk_checks = [
        ("plant_id",             TABLE_UNIT,     "unit_id"),
        ("unit_id",              TABLE_UNIT,     "unit_id"),
        ("role_id",              TABLE_ROLE,     "role_id"),
        ("zone_id",              TABLE_ZONE,     "zone_id"),
        ("market_id",            TABLE_MARKET,   "market_id"),
        ("customer_id",          TABLE_CUSTOMER, "customer_id"),
        ("unit_type_id",         "unit_type",    "unit_type_id"),
        ("product_id",           "product",      "product_id"),
        ("product_line_id",      "product_line", "product_line_id"),
        ("set_by_people_id",      TABLE_PEOPLE,  "people_id"),
        ("approved_by_people_id", TABLE_PEOPLE,  "people_id"),
    ]
    for field, table, pk_col in fk_checks:
        val = alloc.get(field)
        if val is not None and not _row_exists(cur, table, pk_col, val):
            return f"{field}={val} does not exist in {table}."
    return None


def _auto_resolve_set_by_people_id(cur, alloc: Dict[str, Any]) -> Optional[str]:
    """
    Populate set_by_people_id from the unique active role holder for the
    allocation scope. Returns an error message when resolution is required but
    impossible or ambiguous; returns None on success/no-op.
    """
    if alloc.get("set_by_people_id") is not None:
        return None

    role_id = alloc.get("role_id")
    scope_unit_id = alloc.get("unit_id")
    scope_field = "unit_id"
    if scope_unit_id is None:
        scope_unit_id = alloc.get("plant_id")
        scope_field = "plant_id"

    if role_id is None or scope_unit_id is None:
        return None

    cur.execute(
        f"""
        SELECT DISTINCT ON (va.people_id)
               va.people_id, va.first_name, va.last_name, va.email
        FROM {VIEW_PEOPLE_ASSIGNMENTS} va
        WHERE va.assignment_status = %s
          AND va.role_id = %s
          AND (va.role_unit_id = %s OR va.work_unit_id = %s)
        ORDER BY va.people_id, va.is_primary DESC NULLS LAST,
                 va.last_name, va.first_name
        """,
        (ASSIGNMENT_STATUS_ACTIVE, role_id, scope_unit_id, scope_unit_id),
    )
    candidates = [dict(r) for r in cur.fetchall()]

    if len(candidates) == 1:
        alloc["set_by_people_id"] = candidates[0]["people_id"]
        return None

    scope_text = f"role_id={role_id} and {scope_field}={scope_unit_id}"
    if not candidates:
        return (
            f"No active holder found for {scope_text}. "
            "Provide set_by_people_id explicitly to bypass automatic resolution."
        )

    candidate_labels = []
    for c in candidates:
        name = " ".join(
            str(part) for part in (c.get("first_name"), c.get("last_name")) if part
        ).strip()
        candidate_labels.append(
            f"{c['people_id']} ({name or c.get('email') or 'unknown name'})"
        )
    candidate_text = ", ".join(candidate_labels)
    return (
        f"Multiple active holders found for {scope_text}: {candidate_text}. "
        "Provide set_by_people_id explicitly."
    )



def _require_named_column(columns: Iterable[str], table_name: str, column_name: str) -> str:
    col = find_column(columns, (column_name,))
    if col is None:
        raise ValueError(f'Column "{column_name}" not found in "{table_name}".')
    return col


def _deactivate_kpi_allocations(conn, kpi_id: int) -> Dict[str, Any]:
    columns = require_columns(conn, TABLE_KPI_ALLOCATION)
    kpi_col = _require_named_column(columns, TABLE_KPI_ALLOCATION, "kpi_id")
    target_status_col = _require_named_column(columns, TABLE_KPI_ALLOCATION, "target_status")
    is_active_col = _require_named_column(columns, TABLE_KPI_ALLOCATION, "is_active")
    updated_at_col = find_column(columns, ("updated_at",))

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT COUNT(*) AS count FROM {t} WHERE {kpi_col} = %s").format(
                t=sql.Identifier(TABLE_KPI_ALLOCATION),
                kpi_col=sql.Identifier(kpi_col),
            ),
            (kpi_id,),
        )
        found = int(cur.fetchone()["count"])

        assignments: List[sql.SQL] = [
            sql.SQL("{c} = %s").format(c=sql.Identifier(target_status_col)),
            sql.SQL("{c} = %s").format(c=sql.Identifier(is_active_col)),
        ]
        params: List[Any] = ["Inactive", False]
        if updated_at_col:
            assignments.append(sql.SQL("{c} = NOW()").format(c=sql.Identifier(updated_at_col)))

        cur.execute(
            sql.SQL("UPDATE {t} SET {assignments} WHERE {kpi_col} = %s").format(
                t=sql.Identifier(TABLE_KPI_ALLOCATION),
                assignments=sql.SQL(", ").join(assignments),
                kpi_col=sql.Identifier(kpi_col),
            ),
            params + [kpi_id],
        )
        updated = cur.rowcount if cur.rowcount is not None else 0

    return {
        "applied": True,
        "allocations_found": found,
        "allocations_updated": updated,
    }



def _sync_kpi_knowledge_base_status(conn, kpi_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    if "kpi_knowledge_base" not in row:
        return row
    status_value = row.get("status")
    is_active_value = row.get("is_active")
    if status_value is None or is_active_value is None:
        return row

    columns = require_columns(conn, TABLE_KPI)
    kpi_col = _require_named_column(columns, TABLE_KPI, "kpi_id")
    knowledge_col = find_column(columns, ("kpi_knowledge_base",))
    if knowledge_col is None:
        return row

    assignments: List[sql.SQL] = [
        sql.SQL(
            "{knowledge_col} = jsonb_set("
            "jsonb_set(COALESCE({knowledge_col}, '{{}}'::jsonb), "
            "ARRAY['status'], to_jsonb(%s::text), true), "
            "ARRAY['is_active'], to_jsonb(%s::boolean), true)"
        ).format(knowledge_col=sql.Identifier(knowledge_col))
    ]
    updated_at_col = find_column(columns, ("updated_at",))
    if updated_at_col:
        assignments.append(sql.SQL("{c} = NOW()").format(c=sql.Identifier(updated_at_col)))
    knowledge_updated_col = find_column(columns, ("kpi_knowledge_updated_date",))
    if knowledge_updated_col:
        assignments.append(sql.SQL("{c} = NOW()").format(c=sql.Identifier(knowledge_updated_col)))

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("UPDATE {t} SET {assignments} WHERE {kpi_col} = %s RETURNING *").format(
                t=sql.Identifier(TABLE_KPI),
                assignments=sql.SQL(", ").join(assignments),
                kpi_col=sql.Identifier(kpi_col),
            ),
            [status_value, bool(is_active_value), kpi_id],
        )
        refreshed = cur.fetchone()

    return dict(refreshed) if refreshed else row


@mcp.tool()
def create_kpi(
    kpi_name: str,
    kpi_explanation: str,
    unit: str,
    status: str = "Active",
    kpi_sub_title: Optional[str] = None,
    kpi_code: Optional[str] = None,
    kpi_formula: Optional[str] = None,
    target_value: Optional[float] = None,
    target_direction: Optional[str] = None,
    low_limit: Optional[float] = None,
    high_limit: Optional[float] = None,
    frequency: Optional[str] = None,
    owner_role_id: Optional[int] = None,
    reference_kpi_id: Optional[int] = None,
    importance: Optional[str] = None,
    comments: Optional[str] = None,
    created_by_people_id: Optional[int] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a KPI definition in KPI_DB_Final.
    Required (NOT NULL): kpi_name, kpi_explanation, unit; plus status (default 'Active').
    kpi_id is auto-generated (identity) — never pass it.
    target_direction (optional): up | down | stable | range.
    owner_role_id / reference_kpi_id / created_by_people_id validated when provided.
    Use extra_fields for any other kpi column (e.g. min_value, tolerance_type,
    calculation_mode, kpi_knowledge_base).
    """
    if not kpi_name or not kpi_name.strip():
        return error_response("kpi_name is required.")
    if not kpi_explanation or not kpi_explanation.strip():
        return error_response("kpi_explanation is required.")
    if not unit or not unit.strip():
        return error_response("unit is required.")
    if not status or not status.strip():
        return error_response("status is required.")
    if target_direction is not None and target_direction.strip().lower() not in VALID_TARGET_DIRECTION:
        return error_response(f"target_direction must be one of: {_valid_values_text(VALID_TARGET_DIRECTION)}.")
    try:
        payload = merge_create_payload({
            "kpi_name": kpi_name, "kpi_explanation": kpi_explanation,
            "unit": unit, "status": status,
            "kpi_sub_title": kpi_sub_title, "kpi_code": kpi_code,
            "kpi_formula": kpi_formula,
            "target_value": target_value, "target_direction": target_direction,
            "low_limit": low_limit, "high_limit": high_limit,
            "frequency": frequency, "owner_role_id": owner_role_id,
            "reference_kpi_id": reference_kpi_id, "importance": importance,
            "comments": comments, "created_by_people_id": created_by_people_id,
        }, extra_fields)
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if owner_role_id is not None and not _row_exists(cur, TABLE_ROLE, "role_id", owner_role_id):
                    return error_response(f"owner_role_id={owner_role_id} does not exist in {TABLE_ROLE}.")
                if reference_kpi_id is not None and not _row_exists(cur, TABLE_KPI, "kpi_id", reference_kpi_id):
                    return error_response(f"reference_kpi_id={reference_kpi_id} does not exist in {TABLE_KPI}.")
                if created_by_people_id is not None and not _row_exists(cur, TABLE_PEOPLE, "people_id", created_by_people_id):
                    return error_response(f"created_by_people_id={created_by_people_id} does not exist in {TABLE_PEOPLE}.")
            row = insert_row(conn, TABLE_KPI, payload)
        return success_response(row, "KPI created.")
    except Exception as exc:
        logger.exception("create_kpi failed")
        return error_response("Failed to create KPI.", exc)


@mcp.tool()
def create_kpi_allocation(
    kpi_id: int,
    target_value: Optional[float] = None,
    target_unit: Optional[str] = None,
    target_status: Optional[str] = None,
    kpi_type: Optional[str] = None,
    plant_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    role_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    product_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
    unit_type_id: Optional[int] = None,
    low_limit: Optional[float] = None,
    high_limit: Optional[float] = None,
    target_start_date: Optional[str] = None,
    target_end_date: Optional[str] = None,
    set_by_people_id: Optional[int] = None,
    approved_by_people_id: Optional[int] = None,
    comments: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create one target allocation for an EXISTING KPI in KPI_DB_Final.
    Only kpi_id is required; every scoping/target column is optional.
    kpi_target_allocation_id is auto-generated (identity) — never pass it.
    When set_by_people_id is omitted, it is auto-filled from the unique ACTIVE
    holder of role_id scoped to unit_id, or plant_id when unit_id is absent.
    All foreign keys are validated before insert. Use extra_fields for other
    columns (e.g. local_currency, target_setup_date, previous_target_value).
    """
    try:
        payload = merge_create_payload({
            "kpi_id": kpi_id,
            "target_value": target_value, "target_unit": target_unit,
            "target_status": target_status, "kpi_type": kpi_type,
            "plant_id": plant_id, "unit_id": unit_id, "role_id": role_id,
            "zone_id": zone_id, "market_id": market_id,
            "customer_id": customer_id, "product_id": product_id,
            "product_line_id": product_line_id, "unit_type_id": unit_type_id,
            "low_limit": low_limit, "high_limit": high_limit,
            "target_start_date": target_start_date,
            "target_end_date": target_end_date,
            "set_by_people_id": set_by_people_id,
            "approved_by_people_id": approved_by_people_id,
            "comments": comments,
        }, extra_fields)
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if not _row_exists(cur, TABLE_KPI, "kpi_id", kpi_id):
                    return error_response(f"KPI {kpi_id} not found.")
                setter_err = _auto_resolve_set_by_people_id(cur, payload)
                if setter_err:
                    return error_response(setter_err)
                fk_err = _validate_allocation_fks(cur, payload)
                if fk_err:
                    return error_response(fk_err)
            row = insert_row(conn, TABLE_KPI_ALLOCATION, payload)
        return success_response(row, "KPI target allocation created.")
    except Exception as exc:
        logger.exception("create_kpi_allocation failed")
        return error_response("Failed to create KPI allocation.", exc)


@mcp.tool()
def create_kpi_with_allocations(
    kpi_name: str,
    kpi_explanation: str,
    unit: str,
    allocations: List[Dict[str, Any]],
    status: str = "Active",
    kpi_code: Optional[str] = None,
    kpi_formula: Optional[str] = None,
    target_value: Optional[float] = None,
    target_direction: Optional[str] = None,
    low_limit: Optional[float] = None,
    high_limit: Optional[float] = None,
    frequency: Optional[str] = None,
    owner_role_id: Optional[int] = None,
    importance: Optional[str] = None,
    comments: Optional[str] = None,
    created_by_people_id: Optional[int] = None,
    subject_id: Optional[int] = None,
    subject_is_primary: bool = True,
    kpi_extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a KPI AND its target allocations in ONE transaction (all-or-nothing).
    allocations: non-empty list of objects, same keys as create_kpi_allocation
    MINUS kpi_id (filled automatically), e.g.
        [{"plant_id": 12, "role_id": 5, "target_value": 95, "target_status": "Active"}]
    When set_by_people_id is omitted on an allocation, it is auto-filled from
    the unique ACTIVE holder of role_id scoped to unit_id, or plant_id when
    unit_id is absent.
    Optionally links the KPI to a subject (subject_kpi) when subject_id is given.
    All foreign keys are validated before any write.
    """
    if not kpi_name or not kpi_name.strip():
        return error_response("kpi_name is required.")
    if not kpi_explanation or not kpi_explanation.strip():
        return error_response("kpi_explanation is required.")
    if not unit or not unit.strip():
        return error_response("unit is required.")
    if not status or not status.strip():
        return error_response("status is required.")
    if target_direction is not None and target_direction.strip().lower() not in VALID_TARGET_DIRECTION:
        return error_response(f"target_direction must be one of: {_valid_values_text(VALID_TARGET_DIRECTION)}.")
    if not isinstance(allocations, list) or not allocations:
        return error_response("allocations must be a non-empty array. Use create_kpi for a KPI with no allocations.")
    for i, a in enumerate(allocations):
        if not isinstance(a, dict):
            return error_response(f"allocations[{i}] must be a JSON object.")
        if "kpi_id" in a:
            return error_response(f"allocations[{i}] must not set kpi_id — it is assigned automatically.")

    try:
        kpi_payload = merge_create_payload({
            "kpi_name": kpi_name, "kpi_explanation": kpi_explanation,
            "unit": unit, "status": status, "kpi_code": kpi_code,
            "kpi_formula": kpi_formula, "target_value": target_value,
            "target_direction": target_direction,
            "low_limit": low_limit, "high_limit": high_limit,
            "frequency": frequency, "owner_role_id": owner_role_id,
            "importance": importance, "comments": comments,
            "created_by_people_id": created_by_people_id,
        }, kpi_extra_fields)

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if owner_role_id is not None and not _row_exists(cur, TABLE_ROLE, "role_id", owner_role_id):
                    return error_response(f"owner_role_id={owner_role_id} does not exist in {TABLE_ROLE}.")
                if created_by_people_id is not None and not _row_exists(cur, TABLE_PEOPLE, "people_id", created_by_people_id):
                    return error_response(f"created_by_people_id={created_by_people_id} does not exist in {TABLE_PEOPLE}.")
                if subject_id is not None and not _row_exists(cur, TABLE_SUBJECT, "subject_id", subject_id):
                    return error_response(f"subject_id={subject_id} does not exist in {TABLE_SUBJECT}.")
                prepared_allocations: List[Dict[str, Any]] = []
                for i, a in enumerate(allocations):
                    prepared = dict(a)
                    setter_err = _auto_resolve_set_by_people_id(cur, prepared)
                    if setter_err:
                        return error_response(f"allocations[{i}]: {setter_err}")
                    fk_err = _validate_allocation_fks(cur, prepared)
                    if fk_err:
                        return error_response(f"allocations[{i}]: {fk_err}")
                    prepared_allocations.append(prepared)

            # Same connection/transaction — any failure rolls back everything.
            kpi_row = insert_row(conn, TABLE_KPI, kpi_payload)
            new_kpi_id = kpi_row["kpi_id"]

            created_allocs: List[Dict[str, Any]] = []
            for a in prepared_allocations:
                alloc_payload = merge_create_payload({"kpi_id": new_kpi_id}, a)
                created_allocs.append(insert_row(conn, TABLE_KPI_ALLOCATION, alloc_payload))

            subject_link = None
            if subject_id is not None:
                subject_link = insert_row(conn, TABLE_SUBJECT_KPI, {
                    "subject_id": subject_id,
                    "kpi_id": new_kpi_id,
                    "is_primary": bool(subject_is_primary),
                })

        return success_response({
            "kpi": kpi_row,
            "allocations": created_allocs,
            "allocation_count": len(created_allocs),
            "subject_link": subject_link,
        }, f"KPI created with {len(created_allocs)} allocation(s).")
    except Exception as exc:
        logger.exception("create_kpi_with_allocations failed")
        return error_response("Failed to create KPI with allocations.", exc)


@mcp.tool()
def update_kpi(
    kpi_id: int,
    fields: Optional[Dict[str, Any]] = None,
    kpi_name: Optional[str] = None,
    kpi_explanation: Optional[str] = None,
    unit: Optional[str] = None,
    status: Optional[str] = None,
    is_active: Optional[bool] = None,
    kpi_sub_title: Optional[str] = None,
    kpi_code: Optional[str] = None,
    kpi_formula: Optional[str] = None,
    target_value: Optional[float] = None,
    target_direction: Optional[str] = None,
    low_limit: Optional[float] = None,
    high_limit: Optional[float] = None,
    frequency: Optional[str] = None,
    owner_role_id: Optional[int] = None,
    reference_kpi_id: Optional[int] = None,
    importance: Optional[str] = None,
    comments: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update a KPI definition in KPI_DB_Final. Pass only the fields to change.
    status accepts Active/Inactive only and is synchronized with is_active.
    When status is set to Inactive, is_active is automatically set to false and
    all linked KPI target allocations are deactivated in the same transaction.
    Reactivating a KPI does not reactivate allocations. target_direction is
    validated; owner_role_id / reference_kpi_id are validated against role / kpi;
    reference_kpi_id must differ from kpi_id. Use fields for any other kpi
    column.
    """
    if target_direction is not None and target_direction.strip().lower() not in VALID_TARGET_DIRECTION:
        return error_response(f"target_direction must be one of: {_valid_values_text(VALID_TARGET_DIRECTION)}.")
    if reference_kpi_id is not None and reference_kpi_id == kpi_id:
        return error_response("reference_kpi_id cannot equal kpi_id.")
    try:
        payload, _, normalized_is_active = merge_update_payload_with_status_activity({
            "kpi_name": kpi_name, "kpi_explanation": kpi_explanation,
            "unit": unit, "status": status, "is_active": is_active,
            "kpi_sub_title": kpi_sub_title, "kpi_code": kpi_code,
            "kpi_formula": kpi_formula,
            "target_value": target_value, "target_direction": target_direction,
            "low_limit": low_limit, "high_limit": high_limit,
            "frequency": frequency, "owner_role_id": owner_role_id,
            "reference_kpi_id": reference_kpi_id, "importance": importance,
            "comments": comments,
        }, fields, "status")

        cascade = {"applied": False, "allocations_found": 0, "allocations_updated": 0}
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if not _row_exists(cur, TABLE_KPI, "kpi_id", kpi_id):
                    return error_response(f"KPI {kpi_id} not found.")
                if owner_role_id is not None and not _row_exists(cur, TABLE_ROLE, "role_id", owner_role_id):
                    return error_response(f"owner_role_id={owner_role_id} does not exist in {TABLE_ROLE}.")
                if reference_kpi_id is not None and not _row_exists(cur, TABLE_KPI, "kpi_id", reference_kpi_id):
                    return error_response(f"reference_kpi_id={reference_kpi_id} does not exist in {TABLE_KPI}.")

            row = update_row(conn, TABLE_KPI, KPI_PK, kpi_id, payload, touch_updated_at=True)
            if row is not None and normalized_is_active is not None:
                row = _sync_kpi_knowledge_base_status(conn, kpi_id, row)
            if normalized_is_active is False:
                cascade = _deactivate_kpi_allocations(conn, kpi_id)

        if row is None:
            return error_response(f"KPI {kpi_id} not found.")
        return success_response(row, "KPI updated.", cascade=cascade)
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("update_kpi failed")
        return error_response("Failed to update KPI.", exc)


@mcp.tool()
def update_kpi_allocation(
    kpi_target_allocation_id: int,
    fields: Optional[Dict[str, Any]] = None,
    target_value: Optional[float] = None,
    target_unit: Optional[str] = None,
    target_status: Optional[str] = None,
    is_active: Optional[bool] = None,
    kpi_type: Optional[str] = None,
    plant_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    role_id: Optional[int] = None,
    zone_id: Optional[int] = None,
    market_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    product_id: Optional[int] = None,
    product_line_id: Optional[int] = None,
    unit_type_id: Optional[int] = None,
    low_limit: Optional[float] = None,
    high_limit: Optional[float] = None,
    target_start_date: Optional[str] = None,
    target_end_date: Optional[str] = None,
    set_by_people_id: Optional[int] = None,
    approved_by_people_id: Optional[int] = None,
    comments: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update one KPI target allocation in KPI_DB_Final. Pass only fields to change.
    target_status accepts Active/Inactive only and target_status and is_active
    are automatically synchronized. Contradictory values are rejected. kpi_id is
    NOT updatable here. All FKs present in the update are validated. Use fields
    for any other allocation column.
    """
    try:
        payload, _, _ = merge_update_payload_with_status_activity({
            "target_value": target_value, "target_unit": target_unit,
            "target_status": target_status, "is_active": is_active,
            "kpi_type": kpi_type,
            "plant_id": plant_id, "unit_id": unit_id, "role_id": role_id,
            "zone_id": zone_id, "market_id": market_id,
            "customer_id": customer_id, "product_id": product_id,
            "product_line_id": product_line_id, "unit_type_id": unit_type_id,
            "low_limit": low_limit, "high_limit": high_limit,
            "target_start_date": target_start_date,
            "target_end_date": target_end_date,
            "set_by_people_id": set_by_people_id,
            "approved_by_people_id": approved_by_people_id,
            "comments": comments,
        }, fields, "target_status")
        if _normalized_payload_key(payload, "kpi_id") is not None:
            return error_response("kpi_id cannot be changed via update_kpi_allocation.")
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if not _row_exists(cur, TABLE_KPI_ALLOCATION,
                                   "kpi_target_allocation_id", kpi_target_allocation_id):
                    return error_response(f"Allocation {kpi_target_allocation_id} not found.")
                fk_err = _validate_allocation_fks(cur, payload)
                if fk_err:
                    return error_response(fk_err)
            row = update_row(conn, TABLE_KPI_ALLOCATION, KPI_ALLOCATION_PK,
                             kpi_target_allocation_id, payload, touch_updated_at=True)
        if row is None:
            return error_response(f"Allocation {kpi_target_allocation_id} not found.")
        return success_response(row, "KPI allocation updated.")
    except ValueError as exc:
        return error_response(str(exc))
    except Exception as exc:
        logger.exception("update_kpi_allocation failed")
        return error_response("Failed to update KPI allocation.", exc)

# ── Training & escalation tools ──────────────────────────────────────────────

@mcp.tool()
def get_training_dashboard(
    people_id: Optional[int] = None,
    kpi_id: Optional[int] = None,
    status: Optional[str] = None,
    only_pending_tests: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Query v_training_dashboard: training + test lifecycle per person per KPI
    with scores and escalation level.
    status: training_sent | training_done | test_sent | test_overdue |
            completed_pass | completed_fail | escalated.
    only_pending_tests=True restricts to rows whose test is not completed.
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if status and status not in VALID_TRAINING_STATUSES:
        return error_response(f"status must be one of: {_valid_values_text(VALID_TRAINING_STATUSES)}.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if people_id is not None:
            conditions.append("people_id = %s")
            params.append(people_id)
        if kpi_id is not None:
            conditions.append("kpi_id = %s")
            params.append(kpi_id)
        if status:
            conditions.append("status = %s")
            params.append(status)
        if only_pending_tests:
            conditions.append("test_completed_at IS NULL")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT *
                    FROM {VIEW_TRAINING_DASHBOARD}
                    {where}
                    ORDER BY test_due_date NULLS LAST, result_id
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_training_dashboard failed")
        return error_response("Failed to get training dashboard.", exc)


@mcp.tool()
def get_overdue_training_tests(limit: int = 100) -> Dict[str, Any]:
    """
    List training results whose test is overdue (test_due_date < today and
    status in test_sent / test_overdue), with the person, their email, the KPI
    and the current escalation level — the work queue for the escalation agent.
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT tr.result_id, tr.kpi_id, k.kpi_name,
                           tr.people_id,
                           (p.first_name || ' ' || p.name) AS person_name,
                           p.email,
                           tr.test_sent_at, tr.test_due_date,
                           tr.escalation_level, tr.last_reminder_sent_at,
                           tr.next_reminder_due_at, tr.status,
                           (CURRENT_DATE - tr.test_due_date) AS days_overdue
                    FROM {TABLE_TRAINING_RESULT} tr
                    JOIN {TABLE_KPI} k    ON k.kpi_id = tr.kpi_id
                    JOIN {TABLE_PEOPLE} p ON p.people_id = tr.people_id
                    WHERE tr.test_due_date < CURRENT_DATE
                      AND tr.status IN ('test_sent', 'test_overdue', 'escalated')
                      AND tr.test_completed_at IS NULL
                    ORDER BY tr.test_due_date, tr.escalation_level DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_overdue_training_tests failed")
        return error_response("Failed to get overdue training tests.", exc)


@mcp.tool()
def get_escalation_target(people_id: int, escalation_level: int = 0) -> Dict[str, Any]:
    """
    Resolve who should receive an escalation email for a person:
    escalation_level=0 → the person's direct boss, 1 → the boss's boss, etc.
    Walks v_people_with_boss; if the chain is shorter than requested, the
    highest manager found is returned (is_chain_top = true).
    """
    if escalation_level < 0 or escalation_level > 10:
        return error_response("escalation_level must be between 0 and 10.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH RECURSIVE chain AS (
                        SELECT people_id, person, email, role_name,
                               boss_people_id, boss_person, boss_email, boss_role,
                               0 AS lvl
                        FROM {VIEW_PEOPLE_WITH_BOSS}
                        WHERE people_id = %s
                        UNION ALL
                        SELECT v.people_id, v.person, v.email, v.role_name,
                               v.boss_people_id, v.boss_person, v.boss_email, v.boss_role,
                               c.lvl + 1
                        FROM {VIEW_PEOPLE_WITH_BOSS} v
                        JOIN chain c ON c.boss_people_id = v.people_id
                        WHERE c.boss_people_id IS NOT NULL AND c.lvl < 12
                    )
                    SELECT * FROM chain ORDER BY lvl
                    """,
                    (people_id,),
                )
                chain = [dict(r) for r in cur.fetchall()]

        if not chain:
            return error_response(f"Person {people_id} not found in v_people_with_boss.")

        idx = min(escalation_level, len(chain) - 1)
        node = chain[idx]
        is_top = node.get("boss_people_id") is None or idx < escalation_level
        target = None
        if node.get("boss_people_id") is not None:
            target = {
                "people_id": node["boss_people_id"],
                "person":    node["boss_person"],
                "email":     node["boss_email"],
                "role":      node["boss_role"],
            }
        return success_response({
            "person": {
                "people_id": chain[0]["people_id"],
                "person":    chain[0]["person"],
                "email":     chain[0]["email"],
            },
            "escalation_level": escalation_level,
            "target":           target,
            "is_chain_top":     is_top,
            "chain": [
                {"lvl": c["lvl"], "people_id": c["people_id"],
                 "person": c["person"], "email": c["email"]}
                for c in chain
            ],
        })
    except Exception as exc:
        logger.exception("get_escalation_target failed")
        return error_response("Failed to get escalation target.", exc)


@mcp.tool()
def escalate_training_result(
    result_id: int,
    mark_escalated: bool = True,
    next_reminder_in_days: int = 7,
    comments: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record one escalation step on a kpi_training_result:
    increments escalation_level, stamps last_reminder_sent_at = now(),
    sets next_reminder_due_at = now() + next_reminder_in_days, optionally
    sets status = 'escalated', and appends comments.
    Returns the updated row plus the resolved escalation target (the manager
    to notify at the NEW escalation level) so the agent can send the email.
    """
    if next_reminder_in_days < 1 or next_reminder_in_days > 60:
        return error_response("next_reminder_in_days must be between 1 and 60.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT result_id, people_id, kpi_id, escalation_level,
                           status, test_completed_at, comments
                    FROM {TABLE_TRAINING_RESULT}
                    WHERE result_id = %s
                    FOR UPDATE
                    """,
                    (result_id,),
                )
                current = cur.fetchone()
                if current is None:
                    return error_response(f"Training result {result_id} not found.")
                current = dict(current)
                if current["test_completed_at"] is not None:
                    return error_response(
                        f"Training result {result_id} already has a completed test; "
                        "no escalation needed."
                    )

                new_level = int(current["escalation_level"] or 0) + 1
                new_status = "escalated" if mark_escalated else current["status"]
                merged_comments = current.get("comments") or ""
                if comments:
                    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                    merged_comments = (merged_comments + "\n" if merged_comments else "") \
                                      + f"[escalation L{new_level} {stamp}] {comments}"

                cur.execute(
                    f"""
                    UPDATE {TABLE_TRAINING_RESULT}
                    SET escalation_level     = %s,
                        status               = %s,
                        last_reminder_sent_at = now(),
                        next_reminder_due_at  = now() + (%s || ' days')::interval,
                        comments             = %s
                    WHERE result_id = %s
                    RETURNING *
                    """,
                    (new_level, new_status, str(next_reminder_in_days),
                     merged_comments or None, result_id),
                )
                updated = dict(cur.fetchone())

        target_resp = get_escalation_target(current["people_id"], max(new_level - 1, 0))
        target = target_resp.get("data") if target_resp.get("success") else None

        return success_response({
            "training_result":   updated,
            "escalation_target": target,
        }, f"Escalation level set to {new_level}.")
    except Exception as exc:
        logger.exception("escalate_training_result failed")
        return error_response("Failed to escalate training result.", exc)


@mcp.tool()
def log_corrective_action_escalation(
    corrective_action_id: int,
    escalation_stage: str,
    sent_to_email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Log an escalation email for a corrective action into
    corrective_action_escalation_log (KPI_DB_Final).
    corrective_action_id refers to an action id in the action-plan database
    (cross-database reference, not FK-enforced).
    (corrective_action_id, escalation_stage) is UNIQUE — logging the same
    stage twice returns the existing row instead of failing.
    """
    if not escalation_stage or not escalation_stage.strip():
        return error_response("escalation_stage is required.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TABLE_ESCALATION_LOG}
                        (corrective_action_id, escalation_stage, sent_to_email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (corrective_action_id, escalation_stage)
                    DO UPDATE SET sent_to_email = COALESCE(EXCLUDED.sent_to_email,
                                                           {TABLE_ESCALATION_LOG}.sent_to_email)
                    RETURNING *, (xmax = 0) AS inserted
                    """,
                    (corrective_action_id, escalation_stage.strip(), sent_to_email),
                )
                row = dict(cur.fetchone())
        inserted = row.pop("inserted", True)
        msg = "Escalation logged." if inserted else "Escalation stage already logged (updated email)."
        return success_response(row, msg)
    except Exception as exc:
        logger.exception("log_corrective_action_escalation failed")
        return error_response("Failed to log corrective action escalation.", exc)


# ============================================================================
# Action plan tools  (ACTION_PLAN_DATABASE_URL)
# ============================================================================

@mcp.tool()
def create_sujet(
    titre: str, code: Optional[str] = None, description: Optional[str] = None,
    parent_sujet_id: Optional[int] = None, inserted_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a sujet (action group) in the action-plan DB."""
    if not titre or not titre.strip():
        return error_response("titre is required.")
    try:
        payload = merge_create_payload(
            {"titre": titre, "code": code, "description": description,
             "parent_sujet_id": parent_sujet_id, "inserted_by": inserted_by}
        )
        with db_connection("actions") as conn:
            row = insert_row(conn, TABLE_SUJET, payload)
        return success_response(row, "Sujet created.")
    except Exception as exc:
        logger.exception("create_sujet failed")
        return error_response("Failed to create sujet.", exc)


@mcp.tool()
def get_sujet(sujet_id: int) -> Dict[str, Any]:
    """Get a sujet by ID with its action count."""
    try:
        with db_connection("actions") as conn:
            sujet = get_row(conn, TABLE_SUJET, SUJET_PK, sujet_id)
            if sujet is None:
                return error_response(f"Sujet {sujet_id} not found.")
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) AS action_count FROM {TABLE_ACTION} WHERE sujet_id = %s",
                    (sujet_id,),
                )
                count = cur.fetchone()["action_count"]
        return success_response({**sujet, "action_count": count})
    except Exception as exc:
        logger.exception("get_sujet failed")
        return error_response("Failed to get sujet.", exc)


@mcp.tool()
def list_sujets(limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """List all sujets ordered by code and title."""
    invalid = validate_limit_offset(limit, offset, max_limit=500)
    if invalid is not None:
        return invalid
    try:
        params: List[Any] = []
        lim_clause = " LIMIT %s"  if limit  is not None else ""
        off_clause = " OFFSET %s" if offset else ""
        if limit is not None:
            params.append(limit)
        if offset:
            params.append(offset)
        q = f"SELECT * FROM {TABLE_SUJET} ORDER BY code NULLS LAST, titre{lim_clause}{off_clause}"
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(q, params)
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_sujets failed")
        return error_response("Failed to list sujets.", exc)


@mcp.tool()
def create_action(
    sujet_id: int, type: str, titre: str,
    description: Optional[str] = None, status: str = "open",
    priorite: Optional[int] = None,
    responsable: Optional[str] = None, email_responsable: Optional[str] = None,
    demandeur: Optional[str] = None, email_demandeur: Optional[str] = None,
    due_date: Optional[str] = None, parent_action_id: Optional[int] = None,
    importance: Optional[str] = None, urgency: Optional[str] = None,
    kpi: Optional[str] = None, estimated_duration_days: Optional[int] = None,
    ordre: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create an action in the action plan.
    type: corrective | preventive | improvement | observation.
    status: open | in_progress | done | cancelled | on_hold.
    """
    if not titre or not titre.strip():
        return error_response("titre is required.")
    if type not in VALID_TYPES:
        return error_response(f"type must be one of: {_valid_values_text(VALID_TYPES)}.")
    if status not in VALID_STATUSES:
        return error_response(f"status must be one of: {_valid_values_text(VALID_STATUSES)}.")
    try:
        payload = merge_create_payload({
            "sujet_id": sujet_id, "type": type, "titre": titre,
            "description": description, "status": status,
            "priorite": priorite, "responsable": responsable,
            "email_responsable": email_responsable,
            "demandeur": demandeur, "email_demandeur": email_demandeur,
            "due_date": due_date, "parent_action_id": parent_action_id,
            "importance": importance, "urgency": urgency,
            "kpi": kpi, "estimated_duration_days": estimated_duration_days,
            "ordre": ordre,
        })
        with db_connection("actions") as conn:
            row = insert_row(conn, TABLE_ACTION, payload)
            _log_event(conn, row["id"], "created", new_value=status, created_by=email_responsable or demandeur)
        return success_response(row, "Action created.")
    except Exception as exc:
        logger.exception("create_action failed")
        return error_response("Failed to create action.", exc)


@mcp.tool()
def get_action(action_id: int) -> Dict[str, Any]:
    """Get a full action with sub-actions, attachments, and recent status comments."""
    try:
        with db_connection("actions") as conn:
            action = get_row(conn, TABLE_ACTION, ACTION_PK, action_id)
            if action is None:
                return error_response(f"Action {action_id} not found.")
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, titre, status, responsable, due_date, priorite
                        FROM {TABLE_ACTION} WHERE parent_action_id = %s
                        ORDER BY ordre NULLS LAST, id""",
                    (action_id,),
                )
                sub_actions = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""SELECT id, file_name, uploaded_by, created_at
                        FROM {TABLE_ATTACHMENT} WHERE action_id = %s
                        ORDER BY created_at DESC""",
                    (action_id,),
                )
                attachments = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""SELECT old_status, new_status, comment, created_by, created_at
                        FROM {TABLE_STATUS_COMMENT} WHERE action_id = %s
                        ORDER BY created_at DESC LIMIT 5""",
                    (action_id,),
                )
                status_comments = [dict(r) for r in cur.fetchall()]

        return success_response({
            **action,
            "sub_actions":     sub_actions,
            "attachments":     attachments,
            "status_comments": status_comments,
        })
    except Exception as exc:
        logger.exception("get_action failed")
        return error_response("Failed to get action.", exc)


@mcp.tool()
def update_action_status(
    action_id: int, new_status: str,
    comment: Optional[str] = None, created_by: Optional[str] = None,
    closed_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an action status and record a status comment."""
    if new_status not in VALID_STATUSES:
        return error_response(f"new_status must be one of: {_valid_values_text(VALID_STATUSES)}.")
    if new_status == "done" and not closed_date:
        closed_date = date.today().isoformat()
    try:
        with db_connection("actions") as conn:
            action = get_row(conn, TABLE_ACTION, ACTION_PK, action_id)
            if action is None:
                return error_response(f"Action {action_id} not found.")
            old_status = action["status"]
            upd_payload: Dict[str, Any] = {"status": new_status}
            if closed_date:
                upd_payload["closed_date"] = closed_date
            updated = update_row(conn, TABLE_ACTION, ACTION_PK, action_id, upd_payload)
            with conn.cursor() as cur:
                cur.execute(
                    f"""INSERT INTO {TABLE_STATUS_COMMENT}
                       (action_id, old_status, new_status, comment, created_by)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (action_id, old_status, new_status, comment, created_by),
                )
            _log_event(conn, action_id, "status_changed",
                       old_value=old_status, new_value=new_status,
                       details=comment, created_by=created_by)
        return success_response(updated, f"Status updated: {old_status} \u2192 {new_status}.")
    except Exception as exc:
        logger.exception("update_action_status failed")
        return error_response("Failed to update action status.", exc)


@mcp.tool()
def update_action(
    action_id: int, fields: Optional[Dict[str, Any]] = None,
    titre: Optional[str] = None, description: Optional[str] = None,
    responsable: Optional[str] = None, email_responsable: Optional[str] = None,
    due_date: Optional[str] = None, priorite: Optional[int] = None,
    importance: Optional[str] = None, urgency: Optional[str] = None,
    kpi: Optional[str] = None, estimated_duration_days: Optional[int] = None,
    ordre: Optional[int] = None,
) -> Dict[str, Any]:
    """Update action fields. Use update_action_status to change status."""
    try:
        payload = merge_update_payload({
            "titre": titre, "description": description,
            "responsable": responsable, "email_responsable": email_responsable,
            "due_date": due_date, "priorite": priorite,
            "importance": importance, "urgency": urgency,
            "kpi": kpi, "estimated_duration_days": estimated_duration_days,
            "ordre": ordre,
        }, fields)
        with db_connection("actions") as conn:
            row = update_row(conn, TABLE_ACTION, ACTION_PK, action_id, payload)
            if row is None:
                return error_response(f"Action {action_id} not found.")
            _log_event(conn, action_id, "updated",
                       details=str(list(payload.keys())),
                       created_by=payload.get("email_responsable"))
        return success_response(row, "Action updated.")
    except Exception as exc:
        logger.exception("update_action failed")
        return error_response("Failed to update action.", exc)


@mcp.tool()
def list_actions_by_sujet(
    sujet_id: int, status: Optional[str] = None,
    include_sub_actions: bool = False,
    limit: int = 100, offset: int = 0,
) -> Dict[str, Any]:
    """List actions for a sujet."""
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    if status and status not in VALID_STATUSES:
        return error_response(f"status must be one of: {_valid_values_text(VALID_STATUSES)}.")
    try:
        conditions = ["sujet_id = %s"]
        params: List[Any] = [sujet_id]
        if not include_sub_actions:
            conditions.append("parent_action_id IS NULL")
        if status:
            conditions.append("status = %s")
            params.append(status)
        params.extend([limit, offset])
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, titre, type, status, priorite, priority_index,
                               responsable, email_responsable, due_date,
                               importance, urgency, depth, created_at
                        FROM {TABLE_ACTION}
                        WHERE {" AND ".join(conditions)}
                        ORDER BY priority_index NULLS LAST, ordre NULLS LAST, id
                        LIMIT %s OFFSET %s""",
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_actions_by_sujet failed")
        return error_response("Failed to list actions by sujet.", exc)


@mcp.tool()
def get_actions_by_responsable(
    email_responsable: str, status: Optional[str] = None, limit: int = 50,
) -> Dict[str, Any]:
    """Get actions assigned to a responsible person by email."""
    if not email_responsable or "@" not in email_responsable:
        return error_response("A valid email is required.")
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if status and status not in VALID_STATUSES:
        return error_response(f"status must be one of: {_valid_values_text(VALID_STATUSES)}.")
    try:
        conditions = ["email_responsable ILIKE %s"]
        params: List[Any] = [email_responsable.strip()]
        if status:
            conditions.append("status = %s")
            params.append(status)
        params.append(limit)
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, titre, type, status, priorite,
                               sujet_id, due_date, importance, urgency,
                               depth, parent_action_id, created_at
                        FROM {TABLE_ACTION}
                        WHERE {" AND ".join(conditions)}
                        ORDER BY due_date NULLS LAST, priority_index NULLS LAST
                        LIMIT %s""",
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_actions_by_responsable failed")
        return error_response("Failed to get actions by responsable.", exc)


@mcp.tool()
def get_overdue_actions(
    email_responsable: Optional[str] = None,
    sujet_id: Optional[int] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Get overdue actions (past due_date, not done or cancelled)."""
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        conditions = ["due_date < CURRENT_DATE", "status NOT IN ('done', 'cancelled')"]
        params: List[Any] = []
        if email_responsable:
            conditions.append("email_responsable ILIKE %s")
            params.append(email_responsable.strip())
        if sujet_id:
            conditions.append("sujet_id = %s")
            params.append(sujet_id)
        params.append(limit)
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, titre, type, status, due_date,
                               responsable, email_responsable,
                               sujet_id, priorite, importance
                        FROM {TABLE_ACTION}
                        WHERE {" AND ".join(conditions)}
                        ORDER BY due_date, priority_index NULLS LAST
                        LIMIT %s""",
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_overdue_actions failed")
        return error_response("Failed to get overdue actions.", exc)


@mcp.tool()
def get_action_summary_by_sujet(sujet_id: int) -> Dict[str, Any]:
    """Dashboard summary for a sujet — counts by status with overdue flag."""
    try:
        with db_connection("actions") as conn:
            sujet = get_row(conn, TABLE_SUJET, SUJET_PK, sujet_id)
            if sujet is None:
                return error_response(f"Sujet {sujet_id} not found.")
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT status, COUNT(*) AS count,
                              COUNT(*) FILTER (
                                  WHERE due_date < CURRENT_DATE
                                    AND status NOT IN ('done','cancelled')
                              ) AS overdue
                       FROM {TABLE_ACTION}
                       WHERE sujet_id = %s AND parent_action_id IS NULL
                       GROUP BY status""",
                    (sujet_id,),
                )
                by_status = [dict(r) for r in cur.fetchall()]
                cur.execute(
                    f"""SELECT MIN(due_date) AS next_due
                       FROM {TABLE_ACTION}
                       WHERE sujet_id = %s AND status NOT IN ('done','cancelled')""",
                    (sujet_id,),
                )
                next_due = cur.fetchone()["next_due"]
        return success_response({"sujet": sujet, "by_status": by_status, "next_due": next_due})
    except Exception as exc:
        logger.exception("get_action_summary_by_sujet failed")
        return error_response("Failed to get action summary.", exc)


@mcp.tool()
def get_action_event_log(action_id: int, limit: int = 20) -> Dict[str, Any]:
    """Get the event history of an action."""
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT event_type, old_value, new_value, details,
                              created_by, created_at
                       FROM {TABLE_EVENT_LOG}
                       WHERE action_id = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (action_id, limit),
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_action_event_log failed")
        return error_response("Failed to get action event log.", exc)


@mcp.tool()
def add_action_attachment(
    action_id: int, file_name: str, file_path: str,
    uploaded_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a file attachment linked to an action."""
    if not file_name or not file_path:
        return error_response("file_name and file_path are required.")
    try:
        payload = merge_create_payload(
            {"action_id": action_id, "file_name": file_name,
             "file_path": file_path, "uploaded_by": uploaded_by}
        )
        with db_connection("actions") as conn:
            if get_row(conn, TABLE_ACTION, ACTION_PK, action_id) is None:
                return error_response(f"Action {action_id} not found.")
            row = insert_row(conn, TABLE_ATTACHMENT, payload)
        return success_response(row, "Attachment registered.")
    except Exception as exc:
        logger.exception("add_action_attachment failed")
        return error_response("Failed to add attachment.", exc)


@mcp.tool()
def delete_action(action_id: int) -> Dict[str, Any]:
    """Delete an action and its DB-managed cascade dependents."""
    try:
        with db_connection("actions") as conn:
            row = delete_row(conn, TABLE_ACTION, ACTION_PK, action_id)
        if row is None:
            return error_response(f"Action {action_id} not found.")
        return success_response(row, "Action deleted.")
    except Exception as exc:
        logger.exception("delete_action failed")
        return error_response("Failed to delete action.", exc)


@mcp.tool()
def batch_update_actions_status(
    action_ids: List[int], new_status: str,
    comment: Optional[str] = None, created_by: Optional[str] = None,
    closed_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Update several action statuses in one transaction."""
    if new_status not in VALID_STATUSES:
        return error_response(f"new_status must be one of: {_valid_values_text(VALID_STATUSES)}.")
    if not isinstance(action_ids, list) or not action_ids:
        return error_response("action_ids must be a non-empty array.")
    if len(action_ids) > BATCH_ACTION_STATUS_MAX_IDS:
        return error_response(f"action_ids cannot exceed {BATCH_ACTION_STATUS_MAX_IDS}.")
    if new_status == "done" and not closed_date:
        closed_date = date.today().isoformat()

    seen: set = set()
    unique_ids: List[int] = []
    for aid in action_ids:
        if isinstance(aid, bool) or not isinstance(aid, int) or aid <= 0:
            return error_response("action_ids must contain positive integers only.")
        if aid not in seen:
            seen.add(aid)
            unique_ids.append(aid)
    duplicates_removed = len(action_ids) - len(unique_ids)

    try:
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, status FROM {TABLE_ACTION} WHERE id = ANY(%s) ORDER BY id",
                    (unique_ids,),
                )
                existing = [dict(r) for r in cur.fetchall()]
                existing_ids = {r["id"] for r in existing}
                missing = [i for i in unique_ids if i not in existing_ids]
                if missing:
                    resp = error_response("One or more actions not found.")
                    resp["missing_action_ids"] = missing
                    return resp

                old_status_map = {r["id"]: r["status"] for r in existing}
                if closed_date:
                    cur.execute(
                        f"UPDATE {TABLE_ACTION} SET status=%s, closed_date=%s WHERE id=ANY(%s) RETURNING *",
                        (new_status, closed_date, unique_ids),
                    )
                else:
                    cur.execute(
                        f"UPDATE {TABLE_ACTION} SET status=%s WHERE id=ANY(%s) RETURNING *",
                        (new_status, unique_ids),
                    )
                updated = [dict(r) for r in cur.fetchall()]
                cur.executemany(
                    f"""INSERT INTO {TABLE_STATUS_COMMENT}
                       (action_id, old_status, new_status, comment, created_by)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [(a["id"], old_status_map[a["id"]], new_status, comment, created_by) for a in updated],
                )
            for a in updated:
                _log_event(conn, a["id"], "status_changed",
                           old_value=old_status_map[a["id"]], new_value=new_status,
                           details=comment, created_by=created_by)
        return success_response({
            "updated_actions":   updated,
            "updated_count":     len(updated),
            "duplicates_removed": duplicates_removed,
        }, f"Updated {len(updated)} action(s) to {new_status}.")
    except Exception as exc:
        logger.exception("batch_update_actions_status failed")
        return error_response("Failed to batch update action statuses.", exc)


# ============================================================================
# Cross-DB tools  (people + actions)
# ============================================================================

@mcp.tool()
def get_action_full_context(action_id: int) -> Dict[str, Any]:
    """
    Get an action enriched with responsable and demandeur profiles
    from KPI_DB_Final (people + role + unit).
    """
    try:
        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT a.*, s.titre AS sujet_titre, s.code AS sujet_code
                       FROM {TABLE_ACTION} a
                       JOIN {TABLE_SUJET} s ON s.id = a.sujet_id
                       WHERE a.id = %s""",
                    (action_id,),
                )
                action = cur.fetchone()
        if action is None:
            return error_response(f"Action {action_id} not found.")
        action = dict(action)

        def _enrich_email(email: Optional[str]) -> Optional[Dict[str, Any]]:
            if not email:
                return None
            try:
                with db_connection("people") as conn:
                    rows = _fetch_people_assignments(conn, "WHERE email ILIKE %s", [email], limit=10)
                if not rows:
                    return None
                grouped = _group_assignments_by_person(rows)
                return grouped[0] if grouped else None
            except Exception:
                return None

        return success_response({
            "action":              action,
            "responsable_profile": _enrich_email(action.get("email_responsable")),
            "demandeur_profile":   _enrich_email(action.get("email_demandeur")),
        })
    except Exception as exc:
        logger.exception("get_action_full_context failed")
        return error_response("Failed to get action full context.", exc)


@mcp.tool()
def get_member_action_dashboard(email: str) -> Dict[str, Any]:
    """
    Get a person's full profile from KPI_DB_Final (with role/unit) plus
    their open, overdue, and done-this-month action counts from the action DB.
    """
    if not email or "@" not in email:
        return error_response("A valid email is required.")
    try:
        with db_connection("people") as conn:
            rows = _fetch_people_assignments(conn, "WHERE email ILIKE %s", [email.strip()], limit=20)
        if not rows:
            return error_response(f"No person found with email '{email}'.")
        member = _group_assignments_by_person(rows)[0]

        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id, titre, type, status, due_date,
                              sujet_id, priorite, importance, urgency
                       FROM {TABLE_ACTION}
                       WHERE email_responsable ILIKE %s
                         AND status NOT IN ('done', 'cancelled')
                       ORDER BY due_date NULLS LAST, priority_index NULLS LAST
                       LIMIT 50""",
                    (email.strip(),),
                )
                open_actions = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""SELECT id, titre, due_date, status, priorite
                       FROM {TABLE_ACTION}
                       WHERE email_responsable ILIKE %s
                         AND due_date < CURRENT_DATE
                         AND status NOT IN ('done', 'cancelled')
                       ORDER BY due_date""",
                    (email.strip(),),
                )
                overdue = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"""SELECT COUNT(*) AS done_this_month
                       FROM {TABLE_ACTION}
                       WHERE email_responsable ILIKE %s
                         AND status = 'done'
                         AND closed_date >= date_trunc('month', CURRENT_DATE)""",
                    (email.strip(),),
                )
                done_this_month = cur.fetchone()["done_this_month"]

        return success_response({
            "member":          member,
            "open_actions":    open_actions,
            "overdue_actions": overdue,
            "done_this_month": done_this_month,
            "totals":          {"open": len(open_actions), "overdue": len(overdue)},
        })
    except Exception as exc:
        logger.exception("get_member_action_dashboard failed")
        return error_response("Failed to get member action dashboard.", exc)


@mcp.tool()
def resolve_and_assign_action(
    action_id: int, assignee_email: str, assigned_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reassign an action after verifying the assignee exists in KPI_DB_Final people.
    Uses the person's primary role name as their display title.
    """
    if not assignee_email or "@" not in assignee_email:
        return error_response("A valid assignee email is required.")
    try:
        with db_connection("people") as conn:
            rows = _fetch_people_assignments(conn, "WHERE email ILIKE %s", [assignee_email.strip()], limit=10)
        if not rows:
            return error_response(
                f"No person found with email '{assignee_email}'. "
                "Use search_member before assigning."
            )
        grouped     = _group_assignments_by_person(rows)
        person      = grouped[0]
        display_name = f"{person['first_name']} {person['last_name']}".strip()
        primary_asgmt = next((a for a in person.get("assignments", []) if a.get("is_primary")), None)
        job_title     = primary_asgmt["role_name"] if primary_asgmt else None

        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT responsable, email_responsable FROM {TABLE_ACTION} WHERE id = %s",
                    (action_id,),
                )
                current = cur.fetchone()
                if current is None:
                    return error_response(f"Action {action_id} not found.")

                cur.execute(
                    f"""UPDATE {TABLE_ACTION}
                       SET responsable = %s, email_responsable = %s, updated_at = now()
                       WHERE id = %s RETURNING *""",
                    (display_name, person["email"], action_id),
                )
                updated = dict(cur.fetchone())

                cur.execute(
                    f"""INSERT INTO {TABLE_EVENT_LOG}
                       (action_id, event_type, old_value, new_value, details, created_by)
                       VALUES (%s, 'reassigned', %s, %s, %s, %s)""",
                    (
                        action_id,
                        current["email_responsable"],
                        person["email"],
                        f"Reassigned to {display_name}" + (f" ({job_title})" if job_title else ""),
                        assigned_by,
                    ),
                )
        return success_response(
            {"action": updated, "assignee": {**person, "display_name": display_name, "job_title": job_title}},
            f"Action reassigned to {display_name}.",
        )
    except Exception as exc:
        logger.exception("resolve_and_assign_action failed")
        return error_response("Failed to resolve and assign action.", exc)


@mcp.tool()
def search_actions_with_member_context(
    query: str, status: Optional[str] = None, limit: int = 20,
) -> Dict[str, Any]:
    """
    Search actions and enrich each result with the responsable's full
    profile (role, unit) from KPI_DB_Final.
    """
    if not query or not query.strip():
        return error_response("query is required.")
    if limit < 1 or limit > 200:
        return error_response("limit must be between 1 and 200.")
    if status and status not in VALID_STATUSES:
        return error_response(f"status must be one of: {_valid_values_text(VALID_STATUSES)}.")
    try:
        like = f"%{query.strip()}%"
        conditions = ["(a.titre ILIKE %s OR a.description ILIKE %s OR a.kpi ILIKE %s)"]
        params: List[Any] = [like, like, like]
        if status:
            conditions.append("a.status = %s")
            params.append(status)
        params.append(limit)

        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT a.id, a.titre, a.type, a.status, a.due_date,
                               a.responsable, a.email_responsable,
                               a.priorite, a.importance, a.sujet_id,
                               s.titre AS sujet_titre
                        FROM {TABLE_ACTION} a
                        JOIN {TABLE_SUJET} s ON s.id = a.sujet_id
                        WHERE {" AND ".join(conditions)}
                        ORDER BY a.due_date NULLS LAST, a.priority_index NULLS LAST
                        LIMIT %s""",
                    params,
                )
                actions = [dict(r) for r in cur.fetchall()]

        if not actions:
            return success_response([], count=0, total=0)

        emails = sorted({str(a["email_responsable"]).lower()
                         for a in actions if a.get("email_responsable")})
        profiles: Dict[str, Dict[str, Any]] = {}
        if emails:
            with db_connection("people") as conn:
                rows = _fetch_people_assignments(
                    conn,
                    "WHERE LOWER(email) = ANY(%s)",
                    [emails],
                    limit=len(emails) * 5,
                )
                for p in _group_assignments_by_person(rows):
                    profiles[p["email"].lower()] = p

        for a in actions:
            key = (a.get("email_responsable") or "").lower()
            a["responsable_profile"] = profiles.get(key)

        return success_response(actions, count=len(actions))
    except Exception as exc:
        logger.exception("search_actions_with_member_context failed")
        return error_response("Failed to search actions with member context.", exc)


@mcp.tool()
def get_kpi_action_overview(kpi_name_or_code: str, limit: int = 50) -> Dict[str, Any]:
    """
    Cross-DB overview for the assistant: KPI definitions matching the name or
    code (KPI_DB_Final) plus the action-plan actions whose `kpi` field matches
    the same text — links KPI performance to corrective actions in one call.
    """
    if not kpi_name_or_code or not kpi_name_or_code.strip():
        return error_response("kpi_name_or_code is required.")
    if limit < 1 or limit > 200:
        return error_response("limit must be between 1 and 200.")
    try:
        like = f"%{kpi_name_or_code.strip()}%"

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT kpi_id, kpi_name, kpi_code, unit, frequency,
                           target_value, target_direction, status
                    FROM {TABLE_KPI}
                    WHERE kpi_name ILIKE %s OR kpi_code ILIKE %s
                    ORDER BY kpi_name
                    LIMIT %s
                    """,
                    (like, like, limit),
                )
                kpis = [dict(r) for r in cur.fetchall()]

        with db_connection("actions") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, titre, type, status, due_date,
                           responsable, email_responsable,
                           sujet_id, kpi, priorite
                    FROM {TABLE_ACTION}
                    WHERE kpi ILIKE %s
                    ORDER BY due_date NULLS LAST
                    LIMIT %s
                    """,
                    (like, limit),
                )
                actions = [dict(r) for r in cur.fetchall()]

        return success_response({
            "kpis":    kpis,
            "actions": actions,
            "counts":  {"kpis": len(kpis), "actions": len(actions)},
        })
    except Exception as exc:
        logger.exception("get_kpi_action_overview failed")
        return error_response("Failed to get KPI action overview.", exc)


# ============================================================================
# KPI MONITORING TOOLS (V1)  — statut, historique, relations, liens actions
# Tables : kpi_monitoring_status, kpi_monitoring_history,
#          kpi_relationship, kpi_action_link  +  vue v_kpi_monitoring
# (Constantes définies plus haut : TABLE_KPI_MON_STATUS, etc.)
# ============================================================================

def _classify_status(raw_value, low_limit, high_limit, target_direction):
    """
    Règle de statut V1 :
      Green  : valeur dans [low_limit, high_limit]
      Red    : hors bornes DU MAUVAIS CÔTÉ selon target_direction
      Yellow : hors bornes du bon côté, ou bornes/valeur manquantes
    'Critical' est réservé à la Phase 2 -> jamais renvoyé ici.
    """
    if raw_value is None:
        return "Yellow"
    if low_limit is not None and high_limit is not None and low_limit <= raw_value <= high_limit:
        return "Green"
    direction = (target_direction or "").strip().lower()
    if direction == "up":
        if low_limit is not None and raw_value < low_limit:
            return "Red"
        return "Yellow"
    if direction == "down":
        if high_limit is not None and raw_value > high_limit:
            return "Red"
        return "Yellow"
    return "Yellow"


@mcp.tool()
def recompute_kpi_monitoring(
    kpi_id: Optional[int] = None,
    kpi_target_allocation_id: Optional[int] = None,
    dry_run: bool = False,
    changed_by: str = "agent",
) -> Dict[str, Any]:
    """
    Recalcule l'état courant (Green/Yellow/Red) pour les allocations KPI à partir
    de la DERNIÈRE ligne kpi_result, met à jour kpi_monitoring_status (upsert) et
    journalise chaque CHANGEMENT de statut dans kpi_monitoring_history.

    Périmètre :
      - kpi_target_allocation_id fourni -> cette seule allocation
      - sinon kpi_id fourni            -> toutes les allocations de ce KPI
      - sinon                          -> toutes les allocations actives

    dry_run=True : calcule et renvoie les transitions SANS rien écrire.

    Portée globale (ni kpi_id ni kpi_target_allocation_id fourni) : TOUTES les
    allocations sont traitées, quel que soit leur target_status. Une allocation
    sans kpi_result reçoit le statut 'Yellow' (valeur manquante).
    """
    try:
        # Pas de filtre sur target_status au global : on traite toutes les
        # allocations. Les filtres ci-dessous ne s'appliquent que si l'appelant
        # cible un KPI ou une allocation précise.
        where = ["TRUE"]
        params: List[Any] = []
        if kpi_target_allocation_id is not None:
            where = ["a.kpi_target_allocation_id = %s"]
            params = [kpi_target_allocation_id]
        elif kpi_id is not None:
            where = ["a.kpi_id = %s"]
            params = [kpi_id]
        where_sql = " AND ".join(where)

        transitions: List[Dict[str, Any]] = []
        processed = 0
        changed = 0

        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT a.kpi_id,
                           a.kpi_target_allocation_id,
                           a.role_id,
                           k.target_direction,
                           lr.raw_value,
                           lr.target_value,
                           lr.lower_limit,
                           lr.upper_limit,
                           lr.result_date,
                           lr.kpi_result_id
                    FROM {TABLE_KPI_ALLOCATION} a
                    JOIN {TABLE_KPI} k ON k.kpi_id = a.kpi_id
                    LEFT JOIN LATERAL (
                        SELECT r.raw_value, r.target_value, r.lower_limit, r.upper_limit,
                               r.result_date, r.kpi_result_id
                        FROM {TABLE_KPI_RESULT} r
                        WHERE r.kpi_target_allocation_id = a.kpi_target_allocation_id
                        ORDER BY r.result_date DESC, r.kpi_result_id DESC
                        LIMIT 1
                    ) lr ON TRUE
                    WHERE {where_sql}
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]

                # Classement en mémoire (rapide) + lecture EN MASSE des statuts
                # précédents pour éviter une requête par allocation (N+1).
                computed = []          # (kpi_id, alloc_id, role_id, new_status, raw, target, gap, result_date, result_id, direction, low, high)
                for row in rows:
                    raw = row["raw_value"]
                    low = row["lower_limit"]
                    high = row["upper_limit"]
                    new_status = _classify_status(raw, low, high, row["target_direction"])
                    gap = None
                    if raw is not None and row["target_value"] is not None:
                        gap = float(raw) - float(row["target_value"])
                    computed.append({
                        "kpi_id": row["kpi_id"],
                        "alloc_id": row["kpi_target_allocation_id"],
                        "role_id": row["role_id"],
                        "new_status": new_status,
                        "raw": raw, "target": row["target_value"], "gap": gap,
                        "result_date": row["result_date"], "result_id": row["kpi_result_id"],
                        "direction": row["target_direction"], "low": low, "high": high,
                    })
                processed = len(computed)

                # 1 seule requête : statuts précédents pour toutes les allocations
                alloc_ids = [c["alloc_id"] for c in computed]
                prev_by_alloc = {}
                if alloc_ids:
                    cur.execute(
                        f"""SELECT kpi_target_allocation_id, current_status
                            FROM {TABLE_KPI_MON_STATUS}
                            WHERE kpi_target_allocation_id = ANY(%s)""",
                        (alloc_ids,),
                    )
                    prev_by_alloc = {r["kpi_target_allocation_id"]: r["current_status"]
                                     for r in cur.fetchall()}

                status_rows = []   # pour upsert en masse
                history_rows = []  # pour insert en masse (seulement les changements)
                for c in computed:
                    old_status = prev_by_alloc.get(c["alloc_id"])
                    if old_status != c["new_status"]:
                        changed += 1
                        transitions.append({
                            "kpi_id": c["kpi_id"],
                            "kpi_target_allocation_id": c["alloc_id"],
                            "old_status": old_status,
                            "new_status": c["new_status"],
                        })
                        history_rows.append((
                            c["kpi_id"], c["alloc_id"], old_status, c["new_status"],
                            "recompute_kpi_monitoring",
                            Json({"raw_value": json_safe(c["raw"]),
                                  "lower_limit": json_safe(c["low"]),
                                  "upper_limit": json_safe(c["high"]),
                                  "target_direction": c["direction"]}),
                            changed_by,
                        ))
                    status_rows.append((
                        c["kpi_id"], c["alloc_id"], c["role_id"], c["new_status"],
                        c["raw"], c["target"], c["gap"], c["result_date"], c["result_id"],
                    ))

                if not dry_run and status_rows:
                    # 1 seul upsert en masse (execute_values)
                    execute_values(
                        cur,
                        f"""
                        INSERT INTO {TABLE_KPI_MON_STATUS}
                          (kpi_id, kpi_target_allocation_id, owner_role_id,
                           current_status, latest_value, latest_target, performance_gap,
                           latest_result_date, latest_kpi_result_id, calculation_date)
                        VALUES %s
                        ON CONFLICT (kpi_id, kpi_target_allocation_id) DO UPDATE SET
                           owner_role_id        = EXCLUDED.owner_role_id,
                           current_status       = EXCLUDED.current_status,
                           latest_value         = EXCLUDED.latest_value,
                           latest_target        = EXCLUDED.latest_target,
                           performance_gap      = EXCLUDED.performance_gap,
                           latest_result_date   = EXCLUDED.latest_result_date,
                           latest_kpi_result_id = EXCLUDED.latest_kpi_result_id,
                           calculation_date     = now()
                        """,
                        status_rows,
                        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
                        page_size=500,
                    )
                    # 1 seul insert d'historique en masse (uniquement les changements)
                    if history_rows:
                        execute_values(
                            cur,
                            f"""
                            INSERT INTO {TABLE_KPI_MON_HISTORY}
                              (kpi_id, kpi_target_allocation_id, old_status, new_status,
                               change_reason, details, changed_by)
                            VALUES %s
                            """,
                            history_rows,
                            page_size=500,
                        )

        return success_response(
            {"transitions": transitions},
            message=("Dry-run : aucune écriture." if dry_run
                     else f"{processed} allocation(s) recalculée(s), {changed} changement(s)."),
            processed=processed, changed=changed, dry_run=dry_run,
        )
    except Exception as exc:
        logger.exception("recompute_kpi_monitoring failed")
        return error_response("Failed to recompute KPI monitoring.", exc)


@mcp.tool()
def get_kpi_monitoring(
    kpi_id: Optional[int] = None,
    current_status: Optional[str] = None,
    plant_name: Optional[str] = None,
    role_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Lit l'état courant des KPI via v_kpi_monitoring.
    Filtres : kpi_id, current_status (Green/Yellow/Red/Critical),
    plant_name (ILIKE), role_name (ILIKE).
    """
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    if offset < 0:
        return error_response("offset must be \u2265 0.")
    if current_status and current_status not in VALID_MON_STATUS:
        return error_response(f"current_status must be one of: {_valid_values_text(VALID_MON_STATUS)}.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if kpi_id is not None:
            conditions.append("kpi_id = %s"); params.append(kpi_id)
        if current_status:
            conditions.append("current_status = %s"); params.append(current_status)
        if plant_name:
            conditions.append("plant_name ILIKE %s"); params.append(f"%{plant_name}%")
        if role_name:
            conditions.append("role_name ILIKE %s"); params.append(f"%{role_name}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.extend([limit, offset])
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {VIEW_KPI_MONITORING}
                    {where}
                    ORDER BY CASE current_status
                                WHEN 'Critical' THEN 0 WHEN 'Red' THEN 1
                                WHEN 'Yellow' THEN 2 ELSE 3 END,
                             kpi_name, plant_name NULLS LAST
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_kpi_monitoring failed")
        return error_response("Failed to get KPI monitoring.", exc)


@mcp.tool()
def get_kpi_monitoring_history(
    kpi_id: Optional[int] = None,
    kpi_target_allocation_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Historique des changements de statut/escalade (kpi_monitoring_history).
    Filtres : kpi_id, kpi_target_allocation_id, date_from/date_to (ISO).
    Ordonné du plus récent au plus ancien.
    """
    if limit < 1 or limit > 1000:
        return error_response("limit must be between 1 and 1000.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if kpi_id is not None:
            conditions.append("h.kpi_id = %s"); params.append(kpi_id)
        if kpi_target_allocation_id is not None:
            conditions.append("h.kpi_target_allocation_id = %s"); params.append(kpi_target_allocation_id)
        if date_from:
            conditions.append("h.change_date >= %s"); params.append(date_from)
        if date_to:
            conditions.append("h.change_date <= %s"); params.append(date_to)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT h.history_id, h.kpi_id, k.kpi_name,
                           h.kpi_target_allocation_id, h.change_date,
                           h.old_status, h.new_status,
                           h.old_escalation_level, h.new_escalation_level,
                           h.change_reason, h.details, h.changed_by
                    FROM {TABLE_KPI_MON_HISTORY} h
                    JOIN {TABLE_KPI} k ON k.kpi_id = h.kpi_id
                    {where}
                    ORDER BY h.change_date DESC, h.history_id DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("get_kpi_monitoring_history failed")
        return error_response("Failed to get KPI monitoring history.", exc)


@mcp.tool()
def manage_kpi_relationship(
    action: str,
    source_kpi_id: Optional[int] = None,
    target_kpi_id: Optional[int] = None,
    relationship_type: Optional[str] = None,
    expected_lag_periods: Optional[int] = None,
    confidence_score: Optional[float] = None,
    validated: Optional[bool] = None,
    relationship_id: Optional[int] = None,
    notes: Optional[str] = None,
    only_validated: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Gère les relations de causalité entre KPI (kpi_relationship).
      action='create'   : crée une relation (validated=false par défaut).
      action='validate' : passe validated=true (relationship_id requis).
      action='list'     : liste les relations (filtre only_validated).
    relationship_type : leading | driver | correlation.
    """
    act = (action or "").strip().lower()
    if act not in {"create", "validate", "list"}:
        return error_response("action must be one of: create, validate, list.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                if act == "create":
                    if source_kpi_id is None or target_kpi_id is None or not relationship_type:
                        return error_response("source_kpi_id, target_kpi_id and relationship_type are required.")
                    if relationship_type not in VALID_RELATION_TYPES:
                        return error_response(f"relationship_type must be one of: {_valid_values_text(VALID_RELATION_TYPES)}.")
                    if source_kpi_id == target_kpi_id:
                        return error_response("source_kpi_id and target_kpi_id must differ.")
                    cur.execute(
                        f"""
                        INSERT INTO {TABLE_KPI_RELATION}
                          (source_kpi_id, target_kpi_id, relationship_type,
                           expected_lag_periods, confidence_score,
                           validated, notes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        RETURNING *
                        """,
                        (source_kpi_id, target_kpi_id, relationship_type,
                         expected_lag_periods, confidence_score,
                         bool(validated) if validated is not None else False, notes),
                    )
                    return success_response(dict(cur.fetchone()), "Relationship created.")

                if act == "validate":
                    if relationship_id is None:
                        return error_response("relationship_id is required to validate.")
                    cur.execute(
                        f"UPDATE {TABLE_KPI_RELATION} SET validated=TRUE WHERE relationship_id=%s RETURNING *",
                        (relationship_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return error_response(f"Relationship {relationship_id} not found.")
                    return success_response(dict(row), "Relationship validated.")

                # list
                conditions: List[str] = []
                params: List[Any] = []
                if source_kpi_id is not None:
                    conditions.append("rel.source_kpi_id = %s"); params.append(source_kpi_id)
                if target_kpi_id is not None:
                    conditions.append("rel.target_kpi_id = %s"); params.append(target_kpi_id)
                if only_validated:
                    conditions.append("rel.validated = TRUE")
                where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
                params.append(limit)
                cur.execute(
                    f"""
                    SELECT rel.*, ks.kpi_name AS source_kpi_name, kt.kpi_name AS target_kpi_name
                    FROM {TABLE_KPI_RELATION} rel
                    JOIN {TABLE_KPI} ks ON ks.kpi_id = rel.source_kpi_id
                    JOIN {TABLE_KPI} kt ON kt.kpi_id = rel.target_kpi_id
                    {where}
                    ORDER BY rel.source_kpi_id, rel.target_kpi_id
                    LIMIT %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
                return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("manage_kpi_relationship failed")
        return error_response("Failed to manage KPI relationship.", exc)


@mcp.tool()
def link_action_to_kpi(
    action_id: int,
    kpi_id: int,
    kpi_target_allocation_id: Optional[int] = None,
    relationship_type: Optional[str] = None,
    expected_impact: Optional[str] = None,
    verify_action: bool = True,
) -> Dict[str, Any]:
    """
    Relie une action (base 'actions' / ACTION_PLAN_DATABASE_URL) à un KPI
    (base 'people' / KPI_DB_Final) dans kpi_action_link.

    IMPORTANT : action_id est une référence LOGIQUE inter-bases — il n'y a pas
    de FK SQL. Si verify_action=True (défaut), l'existence de l'action est
    vérifiée applicativement dans la base 'actions' AVANT la création du lien.
    relationship_type : root_cause | symptom | prevention.
    """
    if relationship_type and relationship_type not in VALID_ACTION_LINK_TYPES:
        return error_response(f"relationship_type must be one of: {_valid_values_text(VALID_ACTION_LINK_TYPES)}.")
    try:
        if verify_action:
            with db_connection("actions") as aconn:
                with aconn.cursor() as acur:
                    acur.execute(f"SELECT id FROM {TABLE_ACTION} WHERE id = %s", (action_id,))
                    if acur.fetchone() is None:
                        return error_response(
                            f"Action {action_id} introuvable dans la base 'actions'. "
                            "Lien non créé (passez verify_action=False pour forcer)."
                        )
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {TABLE_KPI_ACTION_LINK}
                      (action_id, kpi_id, kpi_target_allocation_id,
                       relationship_type, expected_impact)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (action_id, kpi_id, kpi_target_allocation_id) DO UPDATE SET
                       relationship_type = EXCLUDED.relationship_type,
                       expected_impact   = EXCLUDED.expected_impact
                    RETURNING *
                    """,
                    (action_id, kpi_id, kpi_target_allocation_id,
                     relationship_type, expected_impact),
                )
                row = dict(cur.fetchone())
        return success_response(
            row,
            "Action liée au KPI (référence logique inter-bases, sans FK).",
        )
    except Exception as exc:
        logger.exception("link_action_to_kpi failed")
        return error_response("Failed to link action to KPI.", exc)


@mcp.tool()
def list_kpi_action_links(
    kpi_id: Optional[int] = None,
    action_id: Optional[int] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """Liste les liens action <-> KPI (kpi_action_link). Filtres : kpi_id, action_id."""
    if limit < 1 or limit > 500:
        return error_response("limit must be between 1 and 500.")
    try:
        conditions: List[str] = []
        params: List[Any] = []
        if kpi_id is not None:
            conditions.append("l.kpi_id = %s"); params.append(kpi_id)
        if action_id is not None:
            conditions.append("l.action_id = %s"); params.append(action_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT l.*, k.kpi_name, k.kpi_code
                    FROM {TABLE_KPI_ACTION_LINK} l
                    JOIN {TABLE_KPI} k ON k.kpi_id = l.kpi_id
                    {where}
                    ORDER BY l.kpi_id, l.action_id
                    LIMIT %s
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
        return success_response(rows, count=len(rows))
    except Exception as exc:
        logger.exception("list_kpi_action_links failed")
        return error_response("Failed to list KPI action links.", exc)

# ════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Agent write tool: full-schema upsert + auto history logging
# ════════════════════════════════════════════════════════════════════════════

VALID_MON_STATUS_V1 = {
    "Green", "Yellow", "Red", "Critical", "Blocking", "Configuration Error",
}
VALID_PRIORITY_LEVELS = {"Low", "Medium", "High", "Very High", "Critical"}
VALID_PERFORMANCE_TRENDS = {
    "solid_improvement", "probable_improvement", "stable",
    "probable_deterioration", "confirmed_deterioration", "insufficient_data",
}
VALID_AGENT_ACTION_STATUS = {
    "no_action", "planned_not_started", "late", "in_progress",
    "effective", "ineffective", "action_present",
}
SIGNIFICANT_PRIORITY_DELTA = 10.0


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


@mcp.tool()
def upsert_kpi_monitoring_v1(
    kpi_id: int,
    kpi_target_allocation_id: int,
    current_status: str,
    priority_score: float,
    priority_level: str,
    escalation_level: int,
    business_status: Optional[str] = None,
    owner_people_id: Optional[int] = None,
    owner_role_id: Optional[int] = None,
    performance_score: Optional[float] = None,
    performance_gap: Optional[float] = None,
    performance_trend: Optional[str] = None,
    escalation_target_people_id: Optional[int] = None,
    escalation_target_role_id: Optional[int] = None,
    escalation_reason: Optional[str] = None,
    spc_status: Optional[str] = None,
    spc_signal_count: int = 0,
    periods_degraded: int = 0,
    periods_without_improvement: int = 0,
    recurrence_count: int = 0,
    action_status: str = "no_action",
    open_action_count: int = 0,
    late_action_count: int = 0,
    ineffective_action_count: int = 0,
    root_cause_category: Optional[str] = None,
    root_cause_confidence: Optional[float] = None,
    concentration_type: Optional[str] = None,
    concentration_value: Optional[str] = None,
    concentration_score: Optional[float] = None,
    customer_risk: Optional[float] = None,
    delivery_risk: Optional[float] = None,
    quality_risk: Optional[float] = None,
    cash_risk: Optional[float] = None,
    ebitda_risk: Optional[float] = None,
    analysis_details: Optional[Dict[str, Any]] = None,
    changed_by: str = "kpi_monitoring_agent_v1",
) -> Dict[str, Any]:
    """
    KPI Monitoring Agent V1 write tool.

    Upserts ONE full analysis row into kpi_monitoring_status
    (key: kpi_id + kpi_target_allocation_id) and, in the SAME transaction,
    automatically inserts a kpi_monitoring_history row when a SIGNIFICANT
    change occurred versus the previous state:
      - current_status changed
      - priority_level changed
      - |priority_score delta| >= 10
      - escalation_level changed
      - action_status changed
      - root_cause_category changed
      - blocking condition appeared/disappeared (status in Blocking /
        Configuration Error)

    last_escalation_date is stamped only when escalation_level INCREASES.
    Insignificant changes update the status row but write NO history.
    Returns {status_row, history_logged, change_reasons}.
    """
    # ── Validation (agent-facing: strict enums, clear errors) ────────────────
    if current_status not in VALID_MON_STATUS_V1:
        return error_response(f"current_status must be one of: {_valid_values_text(VALID_MON_STATUS_V1)}.")
    if priority_level not in VALID_PRIORITY_LEVELS:
        return error_response(f"priority_level must be one of: {_valid_values_text(VALID_PRIORITY_LEVELS)}.")
    if not (0 <= float(priority_score) <= 100):
        return error_response("priority_score must be between 0 and 100.")
    if escalation_level < 0:
        return error_response("escalation_level must be >= 0.")
    if performance_trend is not None and performance_trend not in VALID_PERFORMANCE_TRENDS:
        return error_response(f"performance_trend must be one of: {_valid_values_text(VALID_PERFORMANCE_TRENDS)}.")
    if action_status not in VALID_AGENT_ACTION_STATUS:
        return error_response(f"action_status must be one of: {_valid_values_text(VALID_AGENT_ACTION_STATUS)}.")
    if root_cause_confidence is not None and not (0 <= float(root_cause_confidence) <= 1):
        return error_response("root_cause_confidence must be between 0 and 1.")
    if analysis_details is not None and not isinstance(analysis_details, dict):
        return error_response("analysis_details must be a JSON object.")

    blocking_statuses = {"Blocking", "Configuration Error"}

    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                # Referential sanity: the allocation must exist and belong to the KPI.
                cur.execute(
                    f"""SELECT 1 FROM {TABLE_KPI_ALLOCATION}
                        WHERE kpi_target_allocation_id = %s AND kpi_id = %s""",
                    (kpi_target_allocation_id, kpi_id),
                )
                if cur.fetchone() is None:
                    return error_response(
                        f"Allocation {kpi_target_allocation_id} not found for KPI {kpi_id}."
                    )

                # Previous state (locked) for significance comparison +
                # escalation-increase detection.
                cur.execute(
                    f"""SELECT current_status, priority_score, priority_level,
                               escalation_level, action_status, root_cause_category,
                               last_escalation_date
                        FROM {TABLE_KPI_MON_STATUS}
                        WHERE kpi_id = %s AND kpi_target_allocation_id = %s
                        FOR UPDATE""",
                    (kpi_id, kpi_target_allocation_id),
                )
                prev = cur.fetchone()
                prev = dict(prev) if prev else None

                # ── Significance rules (SQL_WRITE_NOTES.md) ──────────────────
                reasons: List[str] = []
                if prev is None:
                    reasons.append("initial_analysis")
                else:
                    if prev["current_status"] != current_status:
                        reasons.append("status_changed")
                    if prev["priority_level"] != priority_level:
                        reasons.append("priority_level_changed")
                    old_ps = _num(prev["priority_score"])
                    if old_ps is not None and abs(old_ps - float(priority_score)) >= SIGNIFICANT_PRIORITY_DELTA:
                        reasons.append("priority_score_delta>=10")
                    if int(prev["escalation_level"] or 0) != int(escalation_level):
                        reasons.append("escalation_level_changed")
                    if (prev["action_status"] or None) != action_status:
                        reasons.append("action_status_changed")
                    if (prev["root_cause_category"] or None) != (root_cause_category or None):
                        reasons.append("root_cause_changed")
                    was_blocking = prev["current_status"] in blocking_statuses
                    is_blocking = current_status in blocking_statuses
                    if was_blocking != is_blocking:
                        reasons.append("blocking_appeared" if is_blocking else "blocking_resolved")

                escalation_increased = (
                    int(escalation_level) > int((prev or {}).get("escalation_level") or 0)
                )

                # ── Upsert status row ────────────────────────────────────────
                cur.execute(
                    f"""
                    INSERT INTO {TABLE_KPI_MON_STATUS} (
                        kpi_id, kpi_target_allocation_id,
                        owner_people_id, owner_role_id, calculation_date,
                        current_status, business_status,
                        performance_score, performance_gap, performance_trend,
                        priority_score, priority_level,
                        escalation_level, escalation_target_people_id,
                        escalation_target_role_id, escalation_reason,
                        last_escalation_date,
                        spc_status, spc_signal_count,
                        periods_degraded, periods_without_improvement, recurrence_count,
                        action_status, open_action_count, late_action_count,
                        ineffective_action_count,
                        root_cause_category, root_cause_confidence,
                        concentration_type, concentration_value, concentration_score,
                        customer_risk, delivery_risk, quality_risk, cash_risk, ebitda_risk,
                        analysis_details, created_at, updated_at
                    ) VALUES (
                        %s,%s, %s,%s, now(), %s,%s, %s,%s,%s, %s,%s,
                        %s,%s,%s,%s,
                        CASE WHEN %s THEN now() ELSE NULL END,
                        %s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s, %s,%s,%s,
                        %s,%s,%s,%s,%s, %s, now(), now()
                    )
                    ON CONFLICT (kpi_id, kpi_target_allocation_id) DO UPDATE SET
                        owner_people_id             = EXCLUDED.owner_people_id,
                        owner_role_id               = EXCLUDED.owner_role_id,
                        calculation_date            = now(),
                        current_status              = EXCLUDED.current_status,
                        business_status             = EXCLUDED.business_status,
                        performance_score           = EXCLUDED.performance_score,
                        performance_gap             = EXCLUDED.performance_gap,
                        performance_trend           = EXCLUDED.performance_trend,
                        priority_score              = EXCLUDED.priority_score,
                        priority_level              = EXCLUDED.priority_level,
                        escalation_level            = EXCLUDED.escalation_level,
                        escalation_target_people_id = EXCLUDED.escalation_target_people_id,
                        escalation_target_role_id   = EXCLUDED.escalation_target_role_id,
                        escalation_reason           = EXCLUDED.escalation_reason,
                        last_escalation_date        = CASE
                            WHEN %s THEN now()
                            ELSE {TABLE_KPI_MON_STATUS}.last_escalation_date
                        END,
                        spc_status                  = EXCLUDED.spc_status,
                        spc_signal_count            = EXCLUDED.spc_signal_count,
                        periods_degraded            = EXCLUDED.periods_degraded,
                        periods_without_improvement = EXCLUDED.periods_without_improvement,
                        recurrence_count            = EXCLUDED.recurrence_count,
                        action_status               = EXCLUDED.action_status,
                        open_action_count           = EXCLUDED.open_action_count,
                        late_action_count           = EXCLUDED.late_action_count,
                        ineffective_action_count    = EXCLUDED.ineffective_action_count,
                        root_cause_category         = EXCLUDED.root_cause_category,
                        root_cause_confidence       = EXCLUDED.root_cause_confidence,
                        concentration_type          = EXCLUDED.concentration_type,
                        concentration_value         = EXCLUDED.concentration_value,
                        concentration_score         = EXCLUDED.concentration_score,
                        customer_risk               = EXCLUDED.customer_risk,
                        delivery_risk               = EXCLUDED.delivery_risk,
                        quality_risk                = EXCLUDED.quality_risk,
                        cash_risk                   = EXCLUDED.cash_risk,
                        ebitda_risk                 = EXCLUDED.ebitda_risk,
                        analysis_details            = EXCLUDED.analysis_details,
                        updated_at                  = now()
                    RETURNING *
                    """,
                    (
                        kpi_id, kpi_target_allocation_id,
                        owner_people_id, owner_role_id,
                        current_status, business_status,
                        performance_score, performance_gap, performance_trend,
                        priority_score, priority_level,
                        escalation_level, escalation_target_people_id,
                        escalation_target_role_id, escalation_reason,
                        escalation_increased,               # INSERT last_escalation_date
                        spc_status, spc_signal_count,
                        periods_degraded, periods_without_improvement, recurrence_count,
                        action_status, open_action_count, late_action_count,
                        ineffective_action_count,
                        root_cause_category, root_cause_confidence,
                        concentration_type, concentration_value, concentration_score,
                        customer_risk, delivery_risk, quality_risk, cash_risk, ebitda_risk,
                        Json(analysis_details) if analysis_details is not None else None,
                        escalation_increased,               # UPDATE last_escalation_date
                    ),
                )
                status_row = dict(cur.fetchone())

                # ── History row only on significant change ───────────────────
                history_row = None
                if reasons:
                    cur.execute(
                        f"""
                        INSERT INTO {TABLE_KPI_MON_HISTORY} (
                            kpi_id, kpi_target_allocation_id, change_date,
                            old_status, new_status,
                            old_priority_score, new_priority_score,
                            old_priority_level, new_priority_level,
                            old_escalation_level, new_escalation_level,
                            old_action_status, new_action_status,
                            change_reason, details, changed_by
                        ) VALUES (%s,%s, now(), %s,%s, %s,%s, %s,%s, %s,%s,
                                  %s,%s, %s,%s,%s)
                        RETURNING *
                        """,
                        (
                            kpi_id, kpi_target_allocation_id,
                            (prev or {}).get("current_status"), current_status,
                            _num((prev or {}).get("priority_score")), float(priority_score),
                            (prev or {}).get("priority_level"), priority_level,
                            (prev or {}).get("escalation_level"), escalation_level,
                            (prev or {}).get("action_status"), action_status,
                            "; ".join(reasons),
                            Json({
                                "escalation_reason": escalation_reason,
                                "business_status": business_status,
                                "comment": (analysis_details or {}).get("comment"),
                            }),
                            changed_by,
                        ),
                    )
                    history_row = dict(cur.fetchone())

        return success_response(
            {
                "status_row": status_row,
                "history_logged": history_row is not None,
                "history_row": history_row,
                "change_reasons": reasons,
                "escalation_increased": escalation_increased,
            },
            "Monitoring status upserted."
            + (" History logged." if history_row else " No significant change — no history row."),
        )
    except Exception as exc:
        logger.exception("upsert_kpi_monitoring_v1 failed")
        return error_response("Failed to upsert KPI monitoring V1 status.", exc)


@mcp.tool()
def get_kpi_analysis_bundle(
    kpi_target_allocation_id: int,
    history_periods: int = 24,
) -> Dict[str, Any]:
    """
    ONE call that gathers everything the KPI Monitoring Agent V1 needs to
    analyze a single allocation: KPI attributes (with knowledge base),
    the allocation + resolved responsibles, up to `history_periods` results
    (oldest→newest), the current monitoring status, and the responsible's
    escalation chain. Reduces 5-6 round-trips to 1.
    """
    if history_periods < 6 or history_periods > 200:
        return error_response("history_periods must be between 6 and 200.")
    try:
        with db_connection("people") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT a.*, k.kpi_name, k.kpi_code, k.target_direction,
                               k.importance, k.frequency, k.unit AS kpi_unit,
                               k.low_limit, k.high_limit, k.kpi_knowledge_base
                        FROM {TABLE_KPI_ALLOCATION} a
                        JOIN {TABLE_KPI} k ON k.kpi_id = a.kpi_id
                        WHERE a.kpi_target_allocation_id = %s""",
                    (kpi_target_allocation_id,),
                )
                alloc = cur.fetchone()
                if alloc is None:
                    return error_response(f"Allocation {kpi_target_allocation_id} not found.")
                alloc = dict(alloc)
                kid = alloc["kpi_id"]

                cur.execute(
                    f"""SELECT result_date, week, period_type, raw_value,
                               target_value, lower_limit, upper_limit, result_status
                        FROM {TABLE_KPI_RESULT}
                        WHERE kpi_target_allocation_id = %s
                        ORDER BY result_date DESC, kpi_result_id DESC
                        LIMIT %s""",
                    (kpi_target_allocation_id, history_periods),
                )
                results = [dict(r) for r in cur.fetchall()][::-1]  # oldest → newest

                cur.execute(
                    f"""SELECT * FROM {TABLE_KPI_MON_STATUS}
                        WHERE kpi_id = %s AND kpi_target_allocation_id = %s""",
                    (kid, kpi_target_allocation_id),
                )
                mon = cur.fetchone()
                monitoring_status = dict(mon) if mon else None

                # Responsibles: active holders of the allocated role, unit-scoped.
                cur.execute(
                    f"""SELECT va.people_id, va.first_name, va.last_name, va.email,
                               va.is_primary, va.work_unit_name
                        FROM {VIEW_PEOPLE_ASSIGNMENTS} va
                        WHERE va.role_id = %s
                          AND va.assignment_status = 'ACTIVE'
                          AND (%s::int IS NULL
                               OR va.role_unit_id = %s OR va.work_unit_id = %s)
                        ORDER BY va.is_primary DESC, va.last_name""",
                    (alloc["role_id"], alloc["unit_id"], alloc["unit_id"], alloc["unit_id"]),
                )
                responsibles = [dict(r) for r in cur.fetchall()]

        # Escalation chain for the first responsible (agent can request others).
        chain = None
        if responsibles:
            chain_resp = get_escalation_target(responsibles[0]["people_id"], 0)
            if chain_resp.get("success"):
                chain = chain_resp["data"].get("chain")

        return success_response({
            "allocation": alloc,
            "results": results,
            "results_count": len(results),
            "monitoring_status": monitoring_status,
            "responsibles": responsibles,
            "responsible_ambiguity": len(responsibles) > 1,
            "escalation_chain": chain,
        })
    except Exception as exc:
        logger.exception("get_kpi_analysis_bundle failed")
        return error_response("Failed to build KPI analysis bundle.", exc)
from dataclasses import asdict
from kpi_monitoring_engine_v1 import KPIContext, KPIResult, analyze_kpi_allocation


@mcp.tool()
def analyze_kpi_allocation_tool(
    kpi_target_allocation_id: int,
    history_periods: int = 24,
) -> Dict[str, Any]:
    """
    Calcule de façon déterministe (kpi_monitoring_engine_v1) le statut, la
    priorité, la tendance, le SPC et l'escalade pour une allocation KPI.
    Ne fait AUCUNE écriture. Le JSON retourné doit être passé tel quel à
    upsert_kpi_monitoring_v1 (mêmes clés que agent_output_schema.json).
    """
    bundle_resp = get_kpi_analysis_bundle(kpi_target_allocation_id, history_periods)
    if not bundle_resp.get("success"):
        return bundle_resp
    bundle = bundle_resp["data"]
    alloc = bundle["allocation"]
    results = [r for r in bundle["results"] if r.get("raw_value") is not None]
    if not results:
        return error_response("Aucune valeur raw_value disponible — analyse impossible.")

    responsibles = bundle.get("responsibles") or []
    owner_people_id = responsibles[0]["people_id"] if responsibles else None
    mon = bundle.get("monitoring_status") or {}
    chain = bundle.get("escalation_chain") or []

    try:
        ctx = KPIContext(
            kpi_id=alloc["kpi_id"],
            kpi_target_allocation_id=kpi_target_allocation_id,
            kpi_name=alloc["kpi_name"],
            target_direction=alloc["target_direction"],
            importance=alloc.get("importance"),
            urgency=alloc.get("urgency"),
            owner_people_id=owner_people_id,
            owner_role_id=alloc.get("role_id"),
            current_escalation_level=mon.get("escalation_level", 0),
            actions=[],
            relationships=[],
            knowledge_base=alloc.get("kpi_knowledge_base") or {},
        )
        kpi_results = [
            KPIResult(
                result_date=str(r["result_date"]),
                raw_value=float(r["raw_value"]),
                target_value=float(r["target_value"]) if r.get("target_value") is not None else None,
                lower_limit=float(r["lower_limit"]) if r.get("lower_limit") is not None else None,
                upper_limit=float(r["upper_limit"]) if r.get("upper_limit") is not None else None,
            )
            for r in results
        ]
        analysis = analyze_kpi_allocation(ctx, kpi_results)
        out = asdict(analysis)
        out["owner_people_id"] = owner_people_id
        out["owner_role_id"] = alloc.get("role_id")

        target_lvl = analysis.escalation_level
        if chain and target_lvl < len(chain):
            out["escalation_target_people_id"] = chain[target_lvl].get("people_id")
        else:
            out["escalation_target_people_id"] = None
        out["escalation_target_role_id"] = None

        return success_response(out)
    except Exception as exc:
        logger.exception("analyze_kpi_allocation_tool failed")
        return error_response("Failed to analyze KPI allocation.", exc)
# ============================================================================
# DDL tool — création de NOUVELLES tables uniquement (sûr, déclaratif, gardé)
# ============================================================================
#
# Sécurité (important car cet MCP est destiné au CEO) :
#   - Ne peut QUE créer une table neuve. Jamais ALTER / DROP / OR REPLACE.
#   - Refuse si la table existe déjà -> aucune table existante n'est touchée.
#   - Nom obligatoirement préfixé par 'ext_' -> isole les tables créées ici
#     et empêche toute collision avec people, kpi, unit, role, customer, etc.
#   - Schéma DÉCLARATIF (colonnes typées), jamais de SQL brut -> pas d'injection.
#   - Désactivé par défaut : nécessite ENABLE_DDL=1 côté serveur.
# ============================================================================

DDL_ALLOWED_TYPES = {
    "integer", "bigint", "smallint", "serial", "bigserial",
    "numeric", "real", "double precision",
    "text", "varchar", "boolean",
    "date", "timestamp", "timestamptz",
    "jsonb", "uuid",
}
DDL_REQUIRED_PREFIX = "ext_"
DDL_ALLOWED_DATABASES = {"people", "actions"}
DDL_MAX_COLUMNS = 40
DDL_SAFE_DEFAULTS = {"now()", "current_date", "current_timestamp", "true", "false", "null"}

_DDL_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _ddl_validate_identifier(name: str, kind: str) -> Optional[str]:
    if not name or not _DDL_IDENT_RE.match(name):
        return (f"{kind} '{name}' invalide : minuscules, chiffres et '_' uniquement, "
                "doit commencer par une lettre.")
    if len(name) > 63:
        return f"{kind} '{name}' trop long (max 63 caractères)."
    return None


@mcp.tool()
def create_new_table(
    database: str,
    table_name: str,
    columns: List[Dict[str, Any]],
    primary_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Crée une NOUVELLE table dans 'people' (KPI_DB_Final) ou 'actions'.

    SÉCURITÉ : cet outil ne peut QUE créer une table neuve. Il ne peut jamais
    modifier ni supprimer une table existante. Le nom doit commencer par 'ext_'.
    L'outil est désactivé sauf si ENABLE_DDL=1 est défini côté serveur.

    columns : liste d'objets, par ex.
        [{"name": "id", "type": "bigserial", "nullable": false},
         {"name": "machine_name", "type": "text", "nullable": false},
         {"name": "purchase_date", "type": "date"},
         {"name": "initial_price", "type": "numeric"},
         {"name": "created_at", "type": "timestamptz", "default": "now()"}]
    Types autorisés : integer, bigint, smallint, serial, bigserial, numeric,
    real, double precision, text, varchar, boolean, date, timestamp,
    timestamptz, jsonb, uuid.
    primary_key (optionnel) : nom d'une des colonnes déclarées.
    """
    # 1) Garde d'environnement — désactivé par défaut.
    if os.getenv("ENABLE_DDL", "0") != "1":
        return error_response(
            "Création de table désactivée. Définir ENABLE_DDL=1 côté serveur pour l'activer."
        )

    # 2) Base cible valide ('people' / 'actions', alias 'kpi' accepte).
    db = resolve_db_selector(database)
    if db not in DDL_ALLOWED_DATABASES:
        return error_response(f"database must be one of: {_valid_values_text(DDL_ALLOWED_DATABASES)}.")
    # 3) Nom de table : identifiant valide + préfixe obligatoire.
    err = _ddl_validate_identifier(table_name, "table_name")
    if err:
        return error_response(err)
    if not table_name.startswith(DDL_REQUIRED_PREFIX):
        return error_response(f"Le nom de table doit commencer par '{DDL_REQUIRED_PREFIX}'.")

    # 4) Colonnes : présence, nombre, unicité, type whitelisté.
    if not isinstance(columns, list) or not columns:
        return error_response("columns doit être une liste non vide.")
    if len(columns) > DDL_MAX_COLUMNS:
        return error_response(f"Trop de colonnes (max {DDL_MAX_COLUMNS}).")

    seen: set = set()
    col_specs: List[Tuple[str, str, bool, Any]] = []
    for c in columns:
        if not isinstance(c, dict):
            return error_response("Chaque colonne doit être un objet JSON.")
        cname = str(c.get("name", "")).strip().lower()
        ctype = str(c.get("type", "")).strip().lower()
        cerr = _ddl_validate_identifier(cname, "column name")
        if cerr:
            return error_response(cerr)
        if cname in seen:
            return error_response(f"Colonne en double : '{cname}'.")
        seen.add(cname)
        if ctype not in DDL_ALLOWED_TYPES:
            return error_response(
                f"Type '{ctype}' non autorisé pour '{cname}'. "
                f"Types autorisés : {_valid_values_text(DDL_ALLOWED_TYPES)}."
            )
        nullable = bool(c.get("nullable", True))
        default = c.get("default")
        if default is not None and not isinstance(default, (str, int, float, bool)):
            return error_response(f"default invalide pour '{cname}' (string/number/bool uniquement).")
        col_specs.append((cname, ctype, nullable, default))

    # 5) PK doit référencer une colonne déclarée.
    if primary_key is not None and primary_key.lower() not in seen:
        return error_response(f"primary_key '{primary_key}' n'est pas une colonne déclarée.")

    try:
        with db_connection(db) as conn:
            # 6) Refus si la table existe déjà — garantit qu'aucune table
            #    existante n'est jamais touchée.
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = ANY(current_schemas(true))
                      AND table_name = %s
                    """,
                    (table_name,),
                )
                if cur.fetchone() is not None:
                    return error_response(
                        f"La table '{table_name}' existe déjà. "
                        "Création annulée (aucune modification effectuée)."
                    )

            # 7) Construction du DDL avec quoting sûr (psycopg2.sql).
            col_defs = []
            for cname, ctype, nullable, default in col_specs:
                parts = [sql.Identifier(cname), sql.SQL(ctype)]
                if not nullable:
                    parts.append(sql.SQL("NOT NULL"))
                if default is not None:
                    if isinstance(default, str) and default.strip().lower() in DDL_SAFE_DEFAULTS:
                        parts.append(sql.SQL("DEFAULT ") + sql.SQL(default))
                    else:
                        parts.append(sql.SQL("DEFAULT ") + sql.Literal(default))
                col_defs.append(sql.SQL(" ").join(parts))

            if primary_key is not None:
                col_defs.append(
                    sql.SQL("PRIMARY KEY (") + sql.Identifier(primary_key.lower()) + sql.SQL(")")
                )

            q = sql.SQL("CREATE TABLE {t} ({cols})").format(
                t=sql.Identifier(table_name),
                cols=sql.SQL(", ").join(col_defs),
            )
            with conn.cursor() as cur:
                cur.execute(q)

        logger.info("create_new_table created %s in %s", table_name, db)
        return success_response(
            {"database": db,
             "table_name": table_name,
             "columns": [c[0] for c in col_specs],
             "primary_key": (primary_key.lower() if primary_key else None)},
            f"Table '{table_name}' créée.",
        )
    except Exception as exc:
        logger.exception("create_new_table failed")
        return error_response("Échec de la création de la table.", exc)

# ============================================================================
# Direct SQL tool
# ============================================================================

def _strip_sql_comments_and_literals(query: str) -> str:
    result: List[str] = []
    idx    = 0
    length = len(query)
    while idx < length:
        ch  = query[idx]
        nxt = query[idx + 1] if idx + 1 < length else ""
        if ch == "-" and nxt == "-":
            result.extend("  "); idx += 2
            while idx < length and query[idx] not in "\r\n":
                result.append(" "); idx += 1
            continue
        if ch == "/" and nxt == "*":
            result.extend("  "); idx += 2
            while idx < length:
                if query[idx] == "*" and idx + 1 < length and query[idx + 1] == "/":
                    result.extend("  "); idx += 2; break
                result.append("\n" if query[idx] in "\r\n" else " "); idx += 1
            continue
        if ch == "'":
            result.append(" "); idx += 1
            while idx < length:
                if query[idx] == "'" and idx + 1 < length and query[idx + 1] == "'":
                    result.extend("  "); idx += 2; continue
                if query[idx] == "'":
                    result.append(" "); idx += 1; break
                result.append("\n" if query[idx] in "\r\n" else " "); idx += 1
            continue
        if ch == '"':
            result.append(" "); idx += 1
            while idx < length:
                if query[idx] == '"' and idx + 1 < length and query[idx + 1] == '"':
                    result.extend("  "); idx += 2; continue
                if query[idx] == '"':
                    result.append(" "); idx += 1; break
                result.append("\n" if query[idx] in "\r\n" else " "); idx += 1
            continue
        if ch == "$":
            tag_match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", query[idx:])
            if tag_match:
                tag = tag_match.group(0)
                result.extend(" " * len(tag)); idx += len(tag)
                end_idx = query.find(tag, idx)
                if end_idx == -1:
                    while idx < length:
                        result.append("\n" if query[idx] in "\r\n" else " "); idx += 1
                    break
                while idx < end_idx:
                    result.append("\n" if query[idx] in "\r\n" else " "); idx += 1
                result.extend(" " * len(tag)); idx += len(tag)
                continue
        result.append(ch); idx += 1
    return "".join(result)


def _validate_readonly_query(
    database: str, query: str, max_rows: int, timeout_ms: int,
) -> Optional[Dict[str, Any]]:
    if resolve_db_selector(database) not in DB_ENV_BY_NAME:
        return error_response(f"database must be one of: {_valid_values_text(DB_ENV_BY_NAME.keys())}.")
    if not query.strip():
        return error_response("query is required.")
    if max_rows < 1 or max_rows > READONLY_QUERY_MAX_ROWS_LIMIT:
        return error_response(f"max_rows must be between 1 and {READONLY_QUERY_MAX_ROWS_LIMIT}.")
    if timeout_ms < 100 or timeout_ms > 60000:
        return error_response("timeout_ms must be between 100 and 60000.")

    sanitized = _strip_sql_comments_and_literals(query).strip().rstrip(";").rstrip()
    while sanitized.endswith(";"):
        sanitized = sanitized[:-1].rstrip()
    if not sanitized:
        return error_response("query is required.")
    if ";" in sanitized:
        return error_response("Only one SQL statement is allowed.")

    m = re.match(r"^\s*([A-Za-z_]\w*)\b", sanitized)
    if not m or m.group(1).lower() not in READONLY_QUERY_ALLOWED_START:
        return error_response("Only SELECT / WITH queries are allowed.")

    forbidden = r"\b(" + "|".join(sorted(READONLY_QUERY_FORBIDDEN_KEYWORDS)) + r")\b"
    fm = re.search(forbidden, sanitized, re.IGNORECASE)
    if fm:
        return error_response(f"Forbidden keyword: {fm.group(1).upper()}.")

    if re.search(r"\bFOR\s+(UPDATE|NO\s+KEY\s+UPDATE|SHARE|KEY\s+SHARE)\b", sanitized, re.IGNORECASE):
        return error_response("SELECT locking clauses are not allowed.")
    return None


@mcp.tool()
def execute_readonly_query(
    database: str, query: str,
    params: Optional[List[Any]] = None,
    max_rows: int = 500, timeout_ms: int = 15000,
) -> Dict[str, Any]:
    """
    Execute one read-only SELECT/WITH query on 'people', 'actions', 'rfq', 'apqp', or 'suppliers'.
    The 'people' selector targets KPI_DB_Final; 'kpi' is accepted as a legacy
    alias of 'people'. The 'actions', 'rfq', 'apqp', and 'suppliers' selectors
    target their matching DATABASE_URL environment variables. Result is capped at max_rows.
    """
    err = _validate_readonly_query(database, query, max_rows, timeout_ms)
    if err is not None:
        return err

    db = resolve_db_selector(database)
    qp = [] if params is None else params
    if not isinstance(qp, (list, tuple)):
        return error_response("params must be a JSON array when provided.")

    try:
        with db_connection(db) as conn:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
                cur.execute(query, list(qp)) if qp else cur.execute(query)
                if cur.description is None:
                    return error_response("Query returned no rows descriptor.")
                columns = [c.name for c in cur.description]
                rows    = [dict(r) for r in cur.fetchmany(max_rows + 1)]

        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]

        return success_response(
            rows, database=db, columns=columns,
            row_count=len(rows), max_rows=max_rows, truncated=truncated,
        )
    except Exception as exc:
        logger.exception("execute_readonly_query failed db=%s", db)
        return error_response("Failed to execute read-only query.", exc)


# ============================================================================
# Diagnostic tool
# ============================================================================

def _summarize_tool_check(tool_name: str, response: Dict[str, Any]) -> Dict[str, Any]:
    s: Dict[str, Any] = {"tool": tool_name, "success": bool(response.get("success"))}
    for key in ("count", "total", "message", "error", "details"):
        if key in response:
            s[key] = response[key]
    return json_safe(s)


def _run_check(checks: List[Dict[str, Any]], tool_name: str, func, *args, **kwargs) -> Dict[str, Any]:
    try:
        resp = func(*args, **kwargs)
    except Exception as exc:
        logger.exception("Diagnostic check failed: %s", tool_name)
        resp = error_response("Unhandled exception during diagnostic.", exc)
    checks.append(_summarize_tool_check(tool_name, resp))
    return resp


def _first_data_row(resp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = resp.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return None


@mcp.tool()
def test_all_readonly_tools(sample_query: str = "a", limit: int = 1) -> Dict[str, Any]:
    """
    Run a safe read-only diagnostic across registered tools.
    Only available when the ENABLE_DIAGNOSTICS environment variable is set to '1'.
    """
    if os.getenv("ENABLE_DIAGNOSTICS", "0") != "1":
        return error_response(
            "Diagnostic tool is disabled. "
            "Set ENABLE_DIAGNOSTICS=1 in your environment to enable it."
        )
    if limit < 1 or limit > 10:
        return error_response("limit must be between 1 and 10.")
    if not sample_query or not sample_query.strip():
        return error_response("sample_query is required.")

    checks: List[Dict[str, Any]] = []

    skipped = [
        {"tool": t, "reason": r} for t, r in [
            ("create_customer",             "write"),
            ("update_customer",             "write"),
            ("delete_customer",             "delete"),
            ("create_sujet",                "write"),
            ("create_action",               "write"),
            ("update_action",               "write"),
            ("update_action_status",        "write"),
            ("batch_update_actions_status", "write"),
            ("add_action_attachment",       "write"),
            ("delete_action",               "delete"),
            ("resolve_and_assign_action",   "write"),
            ("escalate_training_result",    "write"),
            ("log_corrective_action_escalation", "write"),
            ("recompute_kpi_monitoring",    "write"),
            ("manage_kpi_relationship",     "write"),
            ("link_action_to_kpi",          "write"),
            ("create_kpi",                  "write"),
            ("create_kpi_allocation",       "write"),
            ("create_kpi_with_allocations", "write"),
            ("update_kpi",                  "write"),
            ("update_kpi_allocation",       "write"),
            ("create_role",                 "write"),
            ("update_role",                 "write"),
            ("deactivate_role",             "write"),
            ("delete_role",                 "delete"),
            ("create_person",               "write"),
            ("update_person",               "write"),
            ("offboard_person",             "write"),
            ("assign_role_to_person",       "write"),
            ("change_person_role",          "write"),
            ("transfer_person_to_unit",     "write"),
            ("update_role_assignment",      "write"),
            ("end_role_assignment",         "write"),
        ]
    ]

    env_status = {
        db: {"env_var": env, "present": bool(os.getenv(env))}
        for db, env in DB_ENV_BY_NAME.items()
    }

    _run_check(checks, "execute_readonly_query[people]",  execute_readonly_query, "people",  "SELECT 1 AS ok", max_rows=1)
    _run_check(checks, "execute_readonly_query[actions]", execute_readonly_query, "actions", "SELECT 1 AS ok", max_rows=1)

    # Customer tools (KPI_DB_Final)
    customers_resp = _run_check(checks, "list_customers",   list_customers,   limit=limit)
    _run_check(checks, "search_customers", search_customers, sample_query, limit=limit)
    customer_row = _first_data_row(customers_resp)
    cust_id = (customer_row or {}).get("customer_id")
    if cust_id:
        _run_check(checks, "get_customer", get_customer, cust_id)
    else:
        checks.append({"tool": "get_customer", "success": None, "skipped": "No sample customer."})

    members_resp = _run_check(checks, "list_members",  list_members,  limit=limit)
    search_resp  = _run_check(checks, "search_member", search_member, sample_query, limit=limit)

    first_member = _first_data_row(search_resp) or _first_data_row(members_resp)
    pid   = (first_member or {}).get("people_id")
    email = (first_member or {}).get("email")

    if pid:
        _run_check(checks, "get_member",            get_member,            pid)
        _run_check(checks, "get_direct_reports",    get_direct_reports,    pid)
        _run_check(checks, "get_org_path",          get_org_path,          pid)
        _run_check(checks, "get_escalation_target", get_escalation_target, pid)
        _run_check(
            checks,
            "get_person_career_history",
            hr_tools.get_person_career_history,
            people_id=pid,
        )
    else:
        for t in (
            "get_member", "get_direct_reports", "get_org_path",
            "get_escalation_target", "get_person_career_history",
        ):
            checks.append({"tool": t, "success": None, "skipped": "No sample person."})

    if email:
        _run_check(checks, "find_member_by_email",        find_member_by_email,        email)
        _run_check(checks, "get_member_action_dashboard", get_member_action_dashboard, email)
    else:
        for t in ("find_member_by_email", "get_member_action_dashboard"):
            checks.append({"tool": t, "success": None, "skipped": "No sample email."})

    _run_check(checks, "list_roles",     list_roles,     limit=limit)
    _run_check(checks, "get_role_tree",  hr_tools.get_role_tree, max_depth=1)
    _run_check(checks, "list_kpi_units", list_kpi_units, limit=limit)
    _run_check(checks, "list_zones",     list_zones,     limit=limit)
    _run_check(checks, "list_markets",   list_markets,   limit=limit)

    # KPI assistant tools
    kpis_resp = _run_check(checks, "list_kpis", list_kpis, limit=limit)
    kpi_row   = _first_data_row(kpis_resp)
    kid       = (kpi_row or {}).get("kpi_id")
    if kid:
        _run_check(checks, "get_kpi",              get_kpi,              kid)
        _run_check(checks, "get_kpi_full_context", get_kpi_full_context, kpi_id=kid, limit=limit)
        _run_check(checks, "list_kpi_allocations", list_kpi_allocations, kpi_id=kid, limit=limit)
        _run_check(checks, "get_kpi_results",      get_kpi_results,      kpi_id=kid, limit=limit)
        _run_check(checks, "get_kpi_responsibles", get_kpi_responsibles, kid)
        _run_check(checks, "get_kpi_export",       get_kpi_export,       kid, results_limit=limit)
        # KPI monitoring (V1) — read-only checks
        _run_check(checks, "get_kpi_monitoring",         get_kpi_monitoring,         kpi_id=kid, limit=limit)
        _run_check(checks, "get_kpi_monitoring_history", get_kpi_monitoring_history, kpi_id=kid, limit=limit)
        _run_check(checks, "list_kpi_action_links",      list_kpi_action_links,      kpi_id=kid, limit=limit)
    else:
        for t in ("get_kpi", "get_kpi_full_context", "list_kpi_allocations",
                  "get_kpi_results", "get_kpi_responsibles", "get_kpi_export",
                  "get_kpi_monitoring", "get_kpi_monitoring_history", "list_kpi_action_links"):
            checks.append({"tool": t, "success": None, "skipped": "No sample KPI."})

    # KPI monitoring relationship listing (no KPI needed)
    _run_check(checks, "manage_kpi_relationship[list]", manage_kpi_relationship, "list", limit=limit)

    _run_check(checks, "get_training_dashboard",      get_training_dashboard,      limit=limit)
    _run_check(checks, "get_overdue_training_tests",  get_overdue_training_tests,  limit=limit)

    sujets_resp = _run_check(checks, "list_sujets",          list_sujets,          limit=limit)
    _run_check(checks, "get_overdue_actions", get_overdue_actions, limit=limit)

    sujet_row = _first_data_row(sujets_resp)
    sujet_id  = (sujet_row or {}).get("id")
    if sujet_id:
        _run_check(checks, "get_sujet",               get_sujet,               sujet_id)
        _run_check(checks, "get_action_summary_by_sujet", get_action_summary_by_sujet, sujet_id)
        actions_resp = _run_check(checks, "list_actions_by_sujet", list_actions_by_sujet, sujet_id, limit=limit)
        action_row = _first_data_row(actions_resp)
        aid = (action_row or {}).get("id")
        if aid:
            _run_check(checks, "get_action",              get_action,              aid)
            _run_check(checks, "get_action_event_log",    get_action_event_log,    aid, limit=limit)
            _run_check(checks, "get_action_full_context", get_action_full_context, aid)
        else:
            for t in ("get_action", "get_action_event_log", "get_action_full_context"):
                checks.append({"tool": t, "success": None, "skipped": "No sample action."})
    else:
        for t in ("get_sujet", "get_action_summary_by_sujet", "list_actions_by_sujet",
                  "get_action", "get_action_event_log", "get_action_full_context"):
            checks.append({"tool": t, "success": None, "skipped": "No sample sujet."})

    passed        = sum(1 for c in checks if c.get("success") is True)
    failed        = sum(1 for c in checks if c.get("success") is False)
    skipped_count = sum(1 for c in checks if c.get("success") is None)

    return success_response({
        "summary": {
            "registered_tools":    len(mcp._tool_manager._tools),
            "read_only_checks":    len(checks),
            "passed":              passed,
            "failed":              failed,
            "skipped":             skipped_count,
            "write_tools_skipped": len(skipped),
        },
        "environment":         env_status,
        "checks":              checks,
        "skipped_write_tools": skipped,
    }, "Read-only MCP diagnostic completed.")


# ============================================================================
# ASGI middleware — GET /mcp intercept (fixes 406 for non-SSE clients)
# ============================================================================

class _GetMcpInterceptMiddleware:
    __slots__ = ("_app", "_mcp_path")

    def __init__(self, app, mcp_path: str = "/mcp") -> None:
        self._app     = app
        self._mcp_path = mcp_path

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == self._mcp_path
            # Only intercept when there is no query string so we don't swallow
            # legitimate MCP GET requests that carry parameters.
            and not scope.get("query_string", b"")
        ):
            from starlette.responses import JSONResponse as _JR
            resp = _JR(
                {"jsonrpc": "2.0",
                 "result":  {"status": "ok", "message": "MCP server ready. POST to /mcp."},
                 "id":      None},
                status_code=200,
            )
            await resp(scope, receive, send)
            return
        await self._app(scope, receive, send)


async def _run_patched_http_async() -> None:
    import uvicorn
    app     = mcp.streamable_http_app()
    patched = _GetMcpInterceptMiddleware(app, mcp_path="/mcp")
    config  = uvicorn.Config(patched, host=MCP_HOST, port=MCP_PORT, log_level="info")
    await uvicorn.Server(config).serve()


# Register optional HR tools before the existing profile filter runs.
import hr_tools

# ════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Tool profile filter (paste just ABOVE `if __name__ == "__main__":`)
#
# Deploy a second instance (or the same code with a different env) using:
#     MCP_TOOL_PROFILE=kpi_agent
# and only the 18 tools below are exposed to the ChatGPT agent.
# Default (unset / "full") keeps all 101 tools — nothing changes for the
# existing CEO/assistant deployment.
# ════════════════════════════════════════════════════════════════════════════
 
KPI_AGENT_TOOL_ALLOWLIST = {
    # Reads — KPI & context
    "list_kpis",
    "get_kpi",
    "get_kpi_full_context",
    "list_kpi_allocations",
    "get_kpi_results",
    "get_kpi_responsibles",
    "get_kpi_analysis_bundle",
    # Reads — hierarchy / escalation
    "get_org_path",
    "get_escalation_target",
    # Reads — actions (evaluate, never create)
    "search_actions_with_member_context",
    "get_actions_by_responsable",
    "get_overdue_actions",
    "list_kpi_action_links",
    # Reads — monitoring state
    "get_kpi_monitoring",
    "get_kpi_monitoring_history",
    "manage_kpi_relationship",   # list/create relations (root-cause hypotheses)
    "analyze_kpi_allocation_tool",
    # Writes — the ONLY authorized writes in V1
    "upsert_kpi_monitoring_v1",
}
 
RFQ_APQP_AGENT_TOOL_ALLOWLIST = {
    # Structured RFQ CRUD
    "list_rfqs",
    "get_rfq",
    "create_rfq",
    "update_rfq",
    "delete_rfq",
    # Structured APQP project CRUD
    "list_apqp_projects",
    "get_apqp_project",
    "create_apqp_project",
    "update_apqp_project",
    "delete_apqp_project",
    # Safe exploration/reporting helpers for RFQ/APQP databases
    "list_external_database_tables",
    "describe_external_database_table",
    "list_external_database_rows",
    "get_external_database_row",
    "execute_readonly_query",
}

SUPPLIER_AGENT_TOOL_ALLOWLIST = {
    # Generic supplier schema/table readers
    "list_supplier_database_tables",
    "describe_supplier_database_table",
    "list_supplier_database_rows",
    "get_supplier_database_row",
    "query_suppliers_readonly",
    # Structured supplier and purchasing read tools
    "list_supplier_groups",
    "search_supplier_units",
    "list_supplier_relations",
    "get_supplier_unit_360",
    "list_evaluation_cycles",
    "get_relation_evaluation",
    "get_supplier_status_history",
    "list_opportunities",
    "get_opportunity",
    "get_financial_line_monthly",
}

_TOOL_PROFILE_ALLOWLISTS = {
    "kpi_agent": KPI_AGENT_TOOL_ALLOWLIST,
    "kpi-agent": KPI_AGENT_TOOL_ALLOWLIST,
    "kpi_monitoring_agent": KPI_AGENT_TOOL_ALLOWLIST,
    "rfq_apqp_agent": RFQ_APQP_AGENT_TOOL_ALLOWLIST,
    "rfq-apqp-agent": RFQ_APQP_AGENT_TOOL_ALLOWLIST,
    "commercial_agent": RFQ_APQP_AGENT_TOOL_ALLOWLIST,
    "supplier_agent": SUPPLIER_AGENT_TOOL_ALLOWLIST,
    "supplier-agent": SUPPLIER_AGENT_TOOL_ALLOWLIST,
    "suppliers_agent": SUPPLIER_AGENT_TOOL_ALLOWLIST,
}

_tool_profile = os.getenv("MCP_TOOL_PROFILE", "full").strip().lower()
if _tool_profile in _TOOL_PROFILE_ALLOWLISTS:
    _allowed_tools = _TOOL_PROFILE_ALLOWLISTS[_tool_profile]
    _registry = mcp._tool_manager._tools
    _removed = [name for name in list(_registry.keys()) if name not in _allowed_tools]
    for _name in _removed:
        _registry.pop(_name, None)
    logger.info(
        "MCP_TOOL_PROFILE=%s -> %d tools exposed, %d hidden (%s...)",
        _tool_profile, len(_registry), len(_removed), ", ".join(sorted(_removed)[:5]),
    )
# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":
    import anyio

    logger.info("=" * 48)
    logger.info("Starting Enterprise Central MCP")
    logger.info("Python    = %s", sys.version)
    logger.info("TRANSPORT = %s", MCP_TRANSPORT)
    logger.info("HOST      = %s", MCP_HOST)
    logger.info("PORT      = %s", MCP_PORT)
    logger.info("=" * 48)

    if MCP_TRANSPORT == "streamable-http":
        anyio.run(_run_patched_http_async)
    else:
        mcp.run(transport=MCP_TRANSPORT)
