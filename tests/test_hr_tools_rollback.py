import contextlib
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
os.environ["MCP_TOOL_PROFILE"] = "full"

import server
import hr_tools


@unittest.skipUnless(
    os.getenv("PEOPLE_DATABASE_URL") and os.getenv("HR_INTEGRATION_TESTS") == "1",
    "integration tests against a populated people directory (fixtures use existing role ids); "
    "set PEOPLE_DATABASE_URL and HR_INTEGRATION_TESTS=1 -- every write is rolled back",
)
class HRToolsRollbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ["PEOPLE_DATABASE_URL"]
        cls.conn = psycopg2.connect(
            url, cursor_factory=RealDictCursor, sslmode=os.getenv("PGSSLMODE", "require"), connect_timeout=15
        )
        cls.conn.autocommit = False
        cls.original_db_connection = hr_tools.db_connection

        @contextlib.contextmanager
        def fake_db_connection(database="people"):
            assert server.resolve_db_selector(database) == "people"
            yield cls.conn

        hr_tools.db_connection = fake_db_connection
        cls.assert_patch_targeted_hr_module()
        cls.initial_counts = cls.counts(cls.conn)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "conn", None) is not None:
            cls.conn.rollback()
            cls.conn.close()
        hr_tools.db_connection = cls.original_db_connection

    @classmethod
    def assert_patch_targeted_hr_module(cls):
        assert hr_tools.db_connection is not server.db_connection

    @staticmethod
    def counts(conn):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  (SELECT count(*) FROM people) AS people,
                  (SELECT count(*) FROM role) AS role,
                  (SELECT count(*) FROM people_role_assignment) AS people_role_assignment
                """
            )
            return dict(cur.fetchone())

    def fetchone(self, query, params=()):
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def fetchall(self, query, params=()):
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def assert_success(self, response):
        self.assertTrue(response.get("success"), response)
        return response["data"]

    def assert_error_contains(self, response, text):
        self.assertFalse(response.get("success"), response)
        self.assertIn(text, response.get("error", ""))

    def tearDown(self):
        self.conn.rollback()

    def role_exists(self, role_id):
        row = self.fetchone("SELECT 1 AS found FROM role WHERE role_id = %s", (role_id,))
        return row is not None

    def create_delete_test_role(self, **kwargs):
        suffix = uuid4().hex[:10]
        params = {
            "role_name": f"Codex Delete Role {suffix}",
            "parent_role_id": 21,
            "role_level": "Professional",
            "grade": "G4",
            "scope": "Usine",
        }
        params.update(kwargs)
        return self.assert_success(hr_tools.create_role(**params))["role"]

    def sample_people_id(self):
        row = self.fetchone("SELECT people_id FROM people ORDER BY people_id LIMIT 1")
        self.assertIsNotNone(row)
        return row["people_id"]

    def add_assignment_for_role(self, role_id, assignment_status="ACTIVE"):
        end_date = date.today() if assignment_status == "ENDED" else None
        return self.fetchone(
            """
            INSERT INTO people_role_assignment
                (people_id, role_id, assignment_status, start_date, end_date,
                 is_primary, allocation_percentage)
            VALUES (%s, %s, %s, CURRENT_DATE, %s, TRUE, 100)
            RETURNING assignment_id
            """,
            (self.sample_people_id(), role_id, assignment_status, end_date),
        )

    def create_test_kpi(self, owner_role_id=None):
        suffix = uuid4().hex[:10]
        return self.fetchone(
            """
            INSERT INTO kpi (kpi_name, kpi_explanation, unit, status, owner_role_id)
            VALUES (%s, %s, %s, 'Active', %s)
            RETURNING kpi_id
            """,
            (
                f"Codex Delete Role KPI {suffix}",
                "Rollback test KPI for delete_role dependency checks.",
                "%",
                owner_role_id,
            ),
        )

    def test_delete_role_not_found(self):
        response = hr_tools.delete_role(-987654321, confirm=True)
        self.assert_error_contains(response, "introuvable")

    def test_delete_role_unused_dry_run_does_not_delete(self):
        role = self.create_delete_test_role()

        data = self.assert_success(hr_tools.delete_role(role["role_id"], confirm=False))

        self.assertTrue(data["can_delete"])
        self.assertEqual(data["dependencies"]["assignments"]["count"], 0)
        self.assertTrue(self.role_exists(role["role_id"]))

    def test_delete_role_unused_confirm_true_deletes(self):
        role = self.create_delete_test_role()

        data = self.assert_success(hr_tools.delete_role(role["role_id"], confirm=True))

        self.assertEqual(data["deleted_role"]["role_id"], role["role_id"])
        self.assertFalse(self.role_exists(role["role_id"]))

    def test_delete_role_refuses_active_assignment(self):
        role = self.create_delete_test_role()
        assignment = self.add_assignment_for_role(role["role_id"], "ACTIVE")

        response = hr_tools.delete_role(role["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        self.assertEqual(response["data"]["dependencies"]["assignments"]["count"], 1)
        self.assertTrue(self.role_exists(role["role_id"]))
        self.assertIsNotNone(
            self.fetchone(
                "SELECT 1 FROM people_role_assignment WHERE assignment_id = %s",
                (assignment["assignment_id"],),
            )
        )

    def test_delete_role_refuses_ended_historical_assignment(self):
        role = self.create_delete_test_role()
        self.add_assignment_for_role(role["role_id"], "ENDED")

        response = hr_tools.delete_role(role["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        deps = response["data"]["dependencies"]
        self.assertEqual(deps["assignments"]["count"], 1)
        self.assertEqual(deps["assignments"]["rows"][0]["assignment_status"], "ENDED")
        self.assertTrue(self.role_exists(role["role_id"]))

    def test_delete_role_refuses_child_role(self):
        parent = self.create_delete_test_role()
        self.create_delete_test_role(parent_role_id=parent["role_id"])

        response = hr_tools.delete_role(parent["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        self.assertEqual(response["data"]["dependencies"]["child_roles"]["count"], 1)
        self.assertTrue(self.role_exists(parent["role_id"]))

    def test_delete_role_refuses_functional_report_reference(self):
        target = self.create_delete_test_role()
        self.create_delete_test_role(functional_report_role_id=target["role_id"])

        response = hr_tools.delete_role(target["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        self.assertEqual(
            response["data"]["dependencies"]["functional_role_references"]["count"],
            1,
        )
        self.assertTrue(self.role_exists(target["role_id"]))

    def test_delete_role_refuses_kpi_target_allocation(self):
        role = self.create_delete_test_role()
        kpi = self.create_test_kpi()
        self.fetchone(
            """
            INSERT INTO kpi_target_allocation (kpi_id, role_id, target_status)
            VALUES (%s, %s, 'Active')
            RETURNING kpi_target_allocation_id
            """,
            (kpi["kpi_id"], role["role_id"]),
        )

        response = hr_tools.delete_role(role["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        self.assertEqual(response["data"]["dependencies"]["kpi_target_allocations"]["count"], 1)
        self.assertTrue(self.role_exists(role["role_id"]))

    def test_delete_role_refuses_kpi_owner_role(self):
        role = self.create_delete_test_role()
        self.create_test_kpi(owner_role_id=role["role_id"])

        response = hr_tools.delete_role(role["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        self.assertEqual(response["data"]["dependencies"]["owned_kpis"]["count"], 1)
        self.assertTrue(self.role_exists(role["role_id"]))

    def test_delete_role_generic_fk_safety_catches_unknown_reference(self):
        role = self.create_delete_test_role()
        table_name = f"codex_delete_role_fk_{uuid4().hex[:8]}"
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE {table_name} (
                    id bigserial PRIMARY KEY,
                    role_id bigint REFERENCES role(role_id)
                )
                """
            )
            cur.execute(f"INSERT INTO {table_name} (role_id) VALUES (%s)", (role["role_id"],))

        response = hr_tools.delete_role(role["role_id"], confirm=True)

        self.assert_error_contains(response, "référencé")
        fk_refs = response["data"]["dependencies"]["foreign_key_references"]
        self.assertTrue(any(ref["table"].endswith(f".{table_name}") for ref in fk_refs), fk_refs)
        self.assertTrue(self.role_exists(role["role_id"]))

    def test_delete_role_confirm_true_with_dependency_deletes_nothing(self):
        role = self.create_delete_test_role()
        assignment = self.add_assignment_for_role(role["role_id"], "ACTIVE")

        response = hr_tools.delete_role(role["role_id"], confirm=True)

        self.assertFalse(response["success"], response)
        self.assertTrue(self.role_exists(role["role_id"]))
        self.assertIsNotNone(
            self.fetchone(
                "SELECT 1 FROM people_role_assignment WHERE assignment_id = %s",
                (assignment["assignment_id"],),
            )
        )

    def test_delete_role_success_does_not_modify_other_core_rows(self):
        role = self.create_delete_test_role()
        before = {
            "people": self.fetchone("SELECT count(*) AS n FROM people")["n"],
            "role": self.fetchone("SELECT count(*) AS n FROM role")["n"],
            "assignments": self.fetchone("SELECT count(*) AS n FROM people_role_assignment")["n"],
            "kpis": self.fetchone("SELECT count(*) AS n FROM kpi")["n"],
            "allocations": self.fetchone("SELECT count(*) AS n FROM kpi_target_allocation")["n"],
        }

        self.assert_success(hr_tools.delete_role(role["role_id"], confirm=True))

        after = {
            "people": self.fetchone("SELECT count(*) AS n FROM people")["n"],
            "role": self.fetchone("SELECT count(*) AS n FROM role")["n"],
            "assignments": self.fetchone("SELECT count(*) AS n FROM people_role_assignment")["n"],
            "kpis": self.fetchone("SELECT count(*) AS n FROM kpi")["n"],
            "allocations": self.fetchone("SELECT count(*) AS n FROM kpi_target_allocation")["n"],
        }
        self.assertEqual(after["role"], before["role"] - 1)
        for key in ("people", "assignments", "kpis", "allocations"):
            self.assertEqual(after[key], before[key])

    def active_primary_count(self, people_id):
        row = self.fetchone(
            """
            SELECT count(*) AS n
            FROM people_role_assignment
            WHERE people_id = %s
              AND upper(assignment_status) = 'ACTIVE'
              AND is_primary IS TRUE
            """,
            (people_id,),
        )
        return row["n"]

    def active_manager_assignment(self, role_id, unit_id):
        return self.fetchone(
            """
            SELECT assignment_id, people_id
            FROM people_role_assignment
            WHERE role_id = %s
              AND unit_id = %s
              AND upper(assignment_status) = 'ACTIVE'
            ORDER BY is_primary DESC NULLS LAST, assignment_id
            LIMIT 1
            """,
            (role_id, unit_id),
        )

    def test_hr_tools_write_rollback_scenarios(self):
        suffix = uuid4().hex[:10]
        test_date = date.today().isoformat()
        role_name = f"Codex HR Rollback {suffix}"

        parent_role = self.fetchone("SELECT role_family FROM role WHERE role_id = 21")
        self.assertEqual(parent_role["role_family"], "VP Operation")

        created_role_data = self.assert_success(
            hr_tools.create_role(
                role_name=role_name,
                parent_role_id=21,
                role_level="Professional",
                grade="G4",
                scope="Usine",
            )
        )
        created_role = created_role_data["role"]
        created_role_id = created_role["role_id"]
        self.assertEqual(created_role["normalized_role_name"], role_name.lower())
        self.assertEqual(created_role["role_slug"], role_name.lower().replace(" ", "_"))
        self.assertEqual(created_role["role_family"], "VP Operation")

        duplicate_role = hr_tools.create_role(role_name="HR Manager")
        self.assert_error_contains(duplicate_role, "role_id=")

        child_role_data = self.assert_success(
            hr_tools.create_role(
                role_name=f"{role_name} Child",
                parent_role_id=created_role_id,
                role_level="Professional",
            )
        )
        child_role_id = child_role_data["role"]["role_id"]
        cycle_response = hr_tools.update_role(created_role_id, parent_role_id=child_role_id)
        self.assert_error_contains(cycle_response, "Cycle")

        plant_manager = self.active_manager_assignment(role_id=17, unit_id=20)
        self.assertIsNotNone(plant_manager)
        person_data = self.assert_success(
            hr_tools.create_person(
                first_name="Codex",
                name=f"Rollback{suffix}",
                work_at_unit_id=20,
                paid_by_unit_id=20,
                email=f"codex.rollback.{suffix}@example.invalid",
                role_id=21,
                assignment_unit_id=20,
                assignment_type="PERMANENT",
            )
        )
        people_id = person_data["person"]["people_id"]
        first_assignment = person_data["assignment"]
        self.assertEqual(first_assignment["assignment_status"], "ACTIVE")
        self.assertTrue(first_assignment["is_primary"])
        self.assertEqual(first_assignment["reports_to"], plant_manager["assignment_id"])

        refused_primary = hr_tools.assign_role_to_person(
            people_id=people_id, role_id=22, unit_id=20, is_primary=True
        )
        self.assert_error_contains(refused_primary, "principale ACTIVE")

        assigned_data = self.assert_success(
            hr_tools.assign_role_to_person(
                people_id=people_id,
                role_id=22,
                unit_id=20,
                is_primary=True,
                demote_existing_primary=True,
            )
        )
        role22_assignment_id = assigned_data["assignment"]["assignment_id"]
        self.assertEqual(self.active_primary_count(people_id), 1)

        changed_data = self.assert_success(
            hr_tools.change_person_role(
                people_id=people_id,
                new_role_id=23,
                assignment_id=role22_assignment_id,
                effective_date=test_date,
            )
        )
        previous = changed_data["previous_assignment"]
        current_assignment = changed_data["new_assignment"]
        self.assertEqual(previous["assignment_status"], "ENDED")
        self.assertEqual(previous["end_date"], test_date)
        self.assertTrue(previous["is_primary"])
        self.assertEqual(current_assignment["start_date"], test_date)
        self.assertTrue(current_assignment["is_primary"])

        subordinate_data = self.assert_success(
            hr_tools.create_person(
                first_name="CodexSub",
                name=f"Rollback{suffix}",
                work_at_unit_id=20,
                paid_by_unit_id=20,
                email=f"codex.sub.{suffix}@example.invalid",
                role_id=22,
                assignment_unit_id=20,
                reports_to=current_assignment["assignment_id"],
                auto_resolve_reports_to=False,
            )
        )
        subordinate_assignment_id = subordinate_data["assignment"]["assignment_id"]

        changed_with_reports = self.assert_success(
            hr_tools.change_person_role(
                people_id=people_id,
                new_role_id=24,
                assignment_id=current_assignment["assignment_id"],
                effective_date=test_date,
                reassign_direct_reports=True,
            )
        )
        current_assignment = changed_with_reports["new_assignment"]
        self.assertIn(
            subordinate_assignment_id,
            changed_with_reports["direct_reports_followed_assignment_ids"],
        )
        subordinate_after = self.fetchone(
            "SELECT reports_to FROM people_role_assignment WHERE assignment_id = %s",
            (subordinate_assignment_id,),
        )
        self.assertEqual(subordinate_after["reports_to"], current_assignment["assignment_id"])

        secondary_data = self.assert_success(
            hr_tools.assign_role_to_person(
                people_id=people_id,
                role_id=25,
                unit_id=20,
                is_primary=False,
            )
        )
        secondary_assignment_id = secondary_data["assignment"]["assignment_id"]
        end_refused = hr_tools.end_role_assignment(
            current_assignment["assignment_id"], end_date=test_date
        )
        self.assert_error_contains(end_refused, "affectation PRINCIPALE")
        self.assertIn("remaining_active_assignments", end_refused)

        ended_data = self.assert_success(
            hr_tools.end_role_assignment(
                current_assignment["assignment_id"],
                end_date=test_date,
                promote_assignment_id_to_primary=secondary_assignment_id,
            )
        )
        self.assertEqual(ended_data["closed_assignment"]["assignment_status"], "ENDED")
        promoted = self.fetchone(
            "SELECT is_primary FROM people_role_assignment WHERE assignment_id = %s",
            (secondary_assignment_id,),
        )
        self.assertTrue(promoted["is_primary"])

        offboard_data = self.assert_success(
            hr_tools.create_person(
                first_name="CodexOff",
                name=f"Rollback{suffix}",
                work_at_unit_id=21,
                paid_by_unit_id=21,
                email=f"codex.off.{suffix}@example.invalid",
                role_id=21,
                assignment_unit_id=21,
            )
        )
        offboard_people_id = offboard_data["person"]["people_id"]
        offboarded = self.assert_success(
            hr_tools.offboard_person(
                offboard_people_id,
                departure_date=test_date,
                reason="rollback test",
            )
        )
        self.assertEqual(offboarded["person"]["status"], "Inactive")
        self.assertIn("rollback test", offboarded["person"]["comments"])
        active_after_offboard = self.fetchone(
            """
            SELECT count(*) AS n
            FROM people_role_assignment
            WHERE people_id = %s AND upper(assignment_status) = 'ACTIVE'
            """,
            (offboard_people_id,),
        )
        self.assertEqual(active_after_offboard["n"], 0)

        transfer_data = self.assert_success(
            hr_tools.create_person(
                first_name="CodexMove",
                name=f"Rollback{suffix}",
                work_at_unit_id=20,
                paid_by_unit_id=20,
                email=f"codex.move.{suffix}@example.invalid",
                role_id=21,
                assignment_unit_id=20,
            )
        )
        transfer_people_id = transfer_data["person"]["people_id"]
        transferred = self.assert_success(
            hr_tools.transfer_person_to_unit(
                transfer_people_id,
                new_unit_id=21,
                effective_date=test_date,
            )
        )
        self.assertEqual(transferred["new_assignment"]["unit_id"], 21)
        self.assertEqual(transferred["person"]["work_at_unit_id"], 21)
        self.assertEqual(transferred["paid_by_unit_id_unchanged"], 20)

        history = self.assert_success(hr_tools.get_person_career_history(people_id=117))
        self.assertEqual(history["history_count"], 3)
        starts = [row["start_date"] for row in history["history"]]
        self.assertEqual(starts, sorted(starts))
        first_history = history["history"][0]
        self.assertEqual(first_history["assignment_status"], "ENDED")
        self.assertEqual(first_history["duration_days"], 32)

        kam_zone = self.fetchone(
            """
            SELECT zone_id, count(*) AS null_customers
            FROM customer
            WHERE zone_id IS NOT NULL AND main_kam_people_id IS NULL
            GROUP BY zone_id
            ORDER BY count(*) DESC, zone_id
            LIMIT 1
            """
        )
        self.assertIsNotNone(kam_zone)
        null_customers_before = kam_zone["null_customers"]
        kam_data = self.assert_success(
            hr_tools.create_person(
                first_name="CodexKam",
                name=f"Rollback{suffix}",
                work_at_unit_id=2,
                paid_by_unit_id=2,
                email=f"codex.kam.{suffix}@example.invalid",
                role_id=16,
                assignment_unit_id=2,
                zone_id=kam_zone["zone_id"],
                auto_resolve_reports_to=False,
            )
        )
        kam_people_id = kam_data["person"]["people_id"]
        kam_assignment_id = kam_data["assignment"]["assignment_id"]
        self.assert_success(hr_tools.update_role_assignment(kam_assignment_id, is_primary=True))
        customer_trigger = self.fetchone(
            """
            SELECT count(*) AS assigned_customers
            FROM customer
            WHERE zone_id = %s AND main_kam_people_id = %s
            """,
            (kam_zone["zone_id"], kam_people_id),
        )
        trigger_status = self.fetchone(
            """
            SELECT tgenabled
            FROM pg_trigger
            WHERE tgrelid = 'people_role_assignment'::regclass
              AND tgname = 'trg_sync_customer_kam'
            """
        )
        if trigger_status["tgenabled"] == "D":
            self.assertEqual(customer_trigger["assigned_customers"], 0)
        else:
            self.assertGreater(customer_trigger["assigned_customers"], 0)

        counts_before_rollback = self.counts(self.conn)
        self.assertGreater(counts_before_rollback["people"], self.initial_counts["people"])
        self.assertGreater(counts_before_rollback["role"], self.initial_counts["role"])
        self.assertGreater(
            counts_before_rollback["people_role_assignment"],
            self.initial_counts["people_role_assignment"],
        )

        self.conn.rollback()

        with psycopg2.connect(
            os.environ["PEOPLE_DATABASE_URL"],
            cursor_factory=RealDictCursor,
            sslmode="require",
            connect_timeout=15,
        ) as fresh:
            self.assertEqual(self.counts(fresh), self.initial_counts)
            with fresh.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*) AS null_customers
                    FROM customer
                    WHERE zone_id = %s AND main_kam_people_id IS NULL
                    """,
                    (kam_zone["zone_id"],),
                )
                restored_customers = cur.fetchone()
            self.assertEqual(restored_customers["null_customers"], null_customers_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
