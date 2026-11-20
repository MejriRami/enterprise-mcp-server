# Enterprise MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives AI assistants **controlled** access to a manufacturing group's internal systems: people directory and org chart, roles, KPIs and their monitoring, action plans, customers, RFQs, APQP projects and suppliers. It exposes **115 typed tools** over five PostgreSQL databases.

**▶ [Demo video, about 2 min](https://mejrirami.github.io/demos/#mcp)**

> **Résumé (FR)** : serveur MCP qui expose 115 outils typés aux assistants IA (annuaire et organigramme, rôles, KPI, plans d'action, clients, RFQ, APQP, fournisseurs) avec des garde-fous : requêtes SQL en lecture seule, profils d'outils par agent, création de table désactivée par défaut. Développé en entreprise ; ce dépôt est anonymisé et la démo utilise une entreprise fictive.

![tests](https://github.com/MejriRami/enterprise-mcp-server/actions/workflows/tests.yml/badge.svg)

---

## Why

Several AI assistants in the company (a CEO assistant, a KPI-analysis agent, HR and quality copilots) needed to answer questions such as *"who does this engineer report to?"*, *"which corrective actions are overdue on this KPI?"* or *"create an action for the plant manager"*. Instead of giving each assistant raw database access, they all go through one MCP server with typed, documented tools and server-side rules.

## Tools (115)

| Area | Examples |
|---|---|
| People & org chart | `search_member`, `get_org_path`, `get_direct_reports`, `list_members_by_role`, `get_escalation_target` |
| HR lifecycle (14 tools) | `create_person`, `assign_role_to_person`, `transfer_person_to_unit`, `offboard_person`, `get_person_career_history` |
| Roles | `create_role`, `get_role_tree`, `update_role`, `deactivate_role` |
| KPIs & monitoring | `list_kpis`, `get_kpi_full_context`, `get_kpi_results`, `recompute_kpi_monitoring`, `get_kpi_analysis_bundle` |
| Action plans | `create_sujet`, `create_action`, `get_overdue_actions`, `resolve_and_assign_action`, `link_action_to_kpi` |
| Customers, RFQ, APQP | `search_customers`, `create_rfq`, `list_apqp_projects` |
| Suppliers (read-only) | `search_supplier_units`, `get_supplier_unit_360`, `query_suppliers_readonly` |
| Controlled SQL | `execute_readonly_query` |

Every tool has a typed signature and a description written for the model: when to use it, what it returns, and what it will refuse.

## Guardrails

- **Read-only SQL tool.** Only one `SELECT`/`WITH` statement; 30+ forbidden keywords (`DELETE`, `INSERT`, `INTO`, `SET`, `COPY`, …) checked after stripping comments and string literals; no `FOR UPDATE` locking; row (≤ 5000) and time (≤ 60 s) limits; database allow-list.
- **Tool profiles (least privilege).** `MCP_TOOL_PROFILE=kpi_agent` exposes only the 18 tools a KPI-analysis agent needs (it can read and evaluate actions, never create them); the default profile keeps the full catalogue for the main assistant.
- **No schema changes by default.** `create_new_table` can only create new `ext_` tables and is disabled unless `ENABLE_DDL=1`.
- **Business validation in the tools, not in the prompt.** For example, `create_role` refuses an unknown parent role and returns data-quality notes; HR write tools are transactional, and the integration suite checks that every failed write rolls back.
- **Secrets only from the environment.** Each database has its own URL (`PEOPLE_DATABASE_URL`, `ACTION_PLAN_DATABASE_URL`, …), with SSL required by default.

These are covered by offline tests in [`tests/test_guardrails.py`](tests/test_guardrails.py).

## Architecture

```
 AI assistants (Claude, ChatGPT agents, OpenAI Agents SDK, internal copilots)
            │  MCP: streamable HTTP (/mcp), SSE or stdio
            ▼
 ┌──────────────────────────── Enterprise MCP Server ───────────────────────────┐
 │ 115 typed tools · tool profiles · read-only SQL validator · DDL switch         │
 └──────┬─────────────┬────────────┬────────────┬─────────────┬──────────────────┘
        ▼             ▼            ▼            ▼             ▼
   people / KPI   action plans    RFQ         APQP        suppliers (read-only)
                    PostgreSQL databases (one URL each)
```

## Demo with a fictional company

The demo uses **Northwind Components**, an invented group with plants in Tunis, Monterrey and Suzhou. `demo/seed_demo.py` creates its roles, people, reporting lines and corrective actions **through the MCP tools themselves**, so the data passes the same validation as real use.

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

createdb people_demo && psql -d people_demo -f demo/schema/people.sql
createdb actions_demo && psql -d actions_demo -f demo/schema/actions.sql

export PEOPLE_DATABASE_URL=postgresql://user:pass@localhost:5432/people_demo
export ACTION_PLAN_DATABASE_URL=postgresql://user:pass@localhost:5432/actions_demo
export PGSSLMODE=disable                                     # local only
python -m demo.seed_demo                                     # roles: 6, people: 9, actions: 4

python server.py                                             # http://127.0.0.1:8000/mcp

pip install -r requirements-demo.txt                         # optional: agent web chat
OPENAI_API_KEY=sk-... python -m demo.web_chat                # http://127.0.0.1:8020 (shows every tool call)
# or: npx @modelcontextprotocol/inspector, or connect Claude Desktop
```

Questions to try from an assistant:
- *"Who does Ines Gharbi report to?"* → `search_member` + `get_org_path`: Quality Engineer → Group Quality Director → CEO.
- *"Which corrective actions are overdue?"* → `get_overdue_actions`: the PFMEA update owned by Ines.
- Force the agent to send `DELETE FROM people …` to the SQL tool → **the server** refuses: *Only SELECT / WITH queries are allowed*. The guardrail does not depend on the model behaving.

## Tests

```bash
pytest -q          # guardrail tests run offline
```

`tests/test_hr_tools_rollback.py` is an integration suite for the HR write tools against a populated people directory (every write is rolled back). Enable it with `PEOPLE_DATABASE_URL=… HR_INTEGRATION_TESTS=1`.

## Stack

Python · MCP SDK (FastMCP) · PostgreSQL / psycopg2 · pytest · GitHub Actions

---

*Built at my current employer; published with the company anonymized and with fictional demo data only.*
