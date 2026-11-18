-- Structure only (no data) of the people / KPI directory database used by the MCP server.
--
-- PostgreSQL database dump
--

-- Dumped from database version 17.5
-- Dumped by pg_dump version 17.5

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: stg; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA stg;


--
-- Name: sync; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA sync;


--
-- Name: audit_kpi_changes(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.audit_kpi_changes() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.kpi_audit (
            kpi_id,
            operation,
            changed_at,
            changed_by,
            application_name,
            client_address,
            transaction_id,
            old_data,
            new_data
        )
        VALUES (
            NEW.kpi_id,
            TG_OP,
            CURRENT_TIMESTAMP,
            CURRENT_USER,
            current_setting('application_name', true),
            inet_client_addr(),
            txid_current(),
            NULL,
            to_jsonb(NEW)
        );

        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO public.kpi_audit (
            kpi_id,
            operation,
            changed_at,
            changed_by,
            application_name,
            client_address,
            transaction_id,
            old_data,
            new_data
        )
        VALUES (
            NEW.kpi_id,
            TG_OP,
            CURRENT_TIMESTAMP,
            CURRENT_USER,
            current_setting('application_name', true),
            inet_client_addr(),
            txid_current(),
            to_jsonb(OLD),
            to_jsonb(NEW)
        );

        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO public.kpi_audit (
            kpi_id,
            operation,
            changed_at,
            changed_by,
            application_name,
            client_address,
            transaction_id,
            old_data,
            new_data
        )
        VALUES (
            OLD.kpi_id,
            TG_OP,
            CURRENT_TIMESTAMP,
            CURRENT_USER,
            current_setting('application_name', true),
            inet_client_addr(),
            txid_current(),
            to_jsonb(OLD),
            NULL
        );

        RETURN OLD;
    END IF;

    RETURN NULL;
END;
$$;


--
-- Name: audit_kpi_target_allocation_changes(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.audit_kpi_target_allocation_changes() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN

    IF TG_OP = 'INSERT' THEN

        INSERT INTO public.kpi_target_allocation_audit (
            kpi_target_allocation_id,
            operation,
            changed_at,
            changed_by,
            application_name,
            client_address,
            transaction_id,
            old_data,
            new_data
        )
        VALUES (
            NEW.kpi_target_allocation_id,
            TG_OP,
            CURRENT_TIMESTAMP,
            CURRENT_USER,
            current_setting('application_name', true),
            inet_client_addr(),
            txid_current(),
            NULL,
            to_jsonb(NEW)
        );

        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN

        INSERT INTO public.kpi_target_allocation_audit (
            kpi_target_allocation_id,
            operation,
            changed_at,
            changed_by,
            application_name,
            client_address,
            transaction_id,
            old_data,
            new_data
        )
        VALUES (
            NEW.kpi_target_allocation_id,
            TG_OP,
            CURRENT_TIMESTAMP,
            CURRENT_USER,
            current_setting('application_name', true),
            inet_client_addr(),
            txid_current(),
            to_jsonb(OLD),
            to_jsonb(NEW)
        );

        RETURN NEW;

    ELSIF TG_OP = 'DELETE' THEN

        INSERT INTO public.kpi_target_allocation_audit (
            kpi_target_allocation_id,
            operation,
            changed_at,
            changed_by,
            application_name,
            client_address,
            transaction_id,
            old_data,
            new_data
        )
        VALUES (
            OLD.kpi_target_allocation_id,
            TG_OP,
            CURRENT_TIMESTAMP,
            CURRENT_USER,
            current_setting('application_name', true),
            inet_client_addr(),
            txid_current(),
            to_jsonb(OLD),
            NULL
        );

        RETURN OLD;

    END IF;

    RETURN NULL;

END;
$$;


--
-- Name: customer_document_attachment_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.customer_document_attachment_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE parent_status text;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Archive attachments instead of deleting them'; END IF;
 SELECT status INTO parent_status FROM public.customer_dossier_revision WHERE revision_id=NEW.revision_id FOR UPDATE;
 IF parent_status IS DISTINCT FROM 'draft' THEN RAISE EXCEPTION 'Only draft revision files can change'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'pending' OR NEW.blob_version_id IS NOT NULL OR NEW.checksum_sha256 IS NOT NULL OR NEW.uploaded_at IS NOT NULL
  THEN RAISE EXCEPTION 'New files must be pending'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['status','blob_version_id','checksum_sha256','uploaded_at'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','blob_version_id','checksum_sha256','uploaded_at'])
  THEN RAISE EXCEPTION 'File identity and upload metadata are immutable'; END IF;
  IF NOT ((OLD.status='pending' AND NEW.status IN('ready','failed','archived'))
       OR (OLD.status IN('ready','failed') AND NEW.status='archived'))
  THEN RAISE EXCEPTION 'Invalid file state transition'; END IF;
  IF OLD.status<>'pending' OR NEW.status<>'ready' THEN
   IF (NEW.blob_version_id,NEW.checksum_sha256,NEW.uploaded_at)
       IS DISTINCT FROM (OLD.blob_version_id,OLD.checksum_sha256,OLD.uploaded_at)
   THEN RAISE EXCEPTION 'Confirmed file version is immutable'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;


--
-- Name: customer_document_audit(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.customer_document_audit() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE old_j jsonb; new_j jsonb;
BEGIN
 IF TG_OP<>'INSERT' THEN old_j:=to_jsonb(OLD); END IF;
 IF TG_OP<>'DELETE' THEN new_j:=to_jsonb(NEW); END IF;
 INSERT INTO public.customer_document_event(customer_id,entity_table,entity_id,operation,actor,old_data,new_data)
 VALUES(COALESCE(new_j->>'customer_id',old_j->>'customer_id')::integer,TG_TABLE_NAME,
  COALESCE(new_j->>TG_ARGV[0],old_j->>TG_ARGV[0]),TG_OP,
  COALESCE(NULLIF(current_setting('app.customer_document_actor',true),''),current_user),old_j,new_j);
 RETURN NULL;
END $$;


--
-- Name: customer_document_bump_revision(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.customer_document_bump_revision() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
 UPDATE public.customer_dossier_revision SET updated_by=COALESCE(NULLIF(current_setting('app.customer_document_actor',true),''),current_user)
 WHERE revision_id=NEW.revision_id;
 RETURN NULL;
END $$;


--
-- Name: customer_dossier_revision_guard(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.customer_dossier_revision_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Archive revisions instead of deleting them'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' THEN RAISE EXCEPTION 'A new revision must be draft'; END IF;
  RETURN NEW;
 END IF;
 IF (NEW.dossier_id,NEW.customer_id,NEW.revision_no,NEW.request_key,NEW.request_hash,NEW.created_by,NEW.created_at)
    IS DISTINCT FROM (OLD.dossier_id,OLD.customer_id,OLD.revision_no,OLD.request_key,OLD.request_hash,OLD.created_by,OLD.created_at)
 THEN RAISE EXCEPTION 'Revision identity is immutable'; END IF;
 IF OLD.status IN('validated','archived') THEN
  IF OLD.status='archived' OR NEW.status NOT IN('validated','archived')
   OR (to_jsonb(NEW)-ARRAY['status','updated_at','updated_by','version'])
      IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','updated_at','updated_by','version'])
  THEN RAISE EXCEPTION 'Validated revisions are immutable; create a new revision'; END IF;
 END IF;
 IF NEW.status='validated' AND OLD.status='draft' THEN
  IF (SELECT count(*) FROM public.customer_document_attachment WHERE revision_id=OLD.revision_id AND status='ready' AND document_type IN('dossier_json','dossier_pdf'))<>2
   OR EXISTS(SELECT 1 FROM public.customer_document_attachment WHERE revision_id=OLD.revision_id AND status IN('pending','failed'))
  THEN RAISE EXCEPTION 'Confirm JSON/PDF and resolve pending/failed files before validation'; END IF;
  IF NEW.validated_by IS NULL OR COALESCE(btrim(NEW.validation_note),'')='' THEN RAISE EXCEPTION 'User validation is required'; END IF;
  NEW.validated_at:=clock_timestamp();
 END IF;
 NEW.updated_at:=clock_timestamp(); NEW.version:=OLD.version+1;
 RETURN NEW;
END $$;


--
-- Name: customer_visit_audit(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.customer_visit_audit() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    old_j jsonb;
    new_j jsonb;
    entity text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_j := to_jsonb(OLD);
    END IF;

    IF TG_OP <> 'DELETE' THEN
        new_j := to_jsonb(NEW);
    END IF;

    entity := COALESCE(
        new_j ->> TG_ARGV[0],
        old_j ->> TG_ARGV[0]
    );

    INSERT INTO public.customer_visit_event (
        entity_table,
        entity_id,
        operation,
        actor,
        old_data,
        new_data
    )
    VALUES (
        TG_TABLE_NAME,
        entity,
        TG_OP,
        COALESCE(
            NULLIF(
                current_setting(
                    'app.commercial_actor',
                    true
                ),
                ''
            ),
            current_user
        ),
        old_j,
        new_j
    );

    RETURN NULL;
END;
$$;


--
-- Name: customer_visit_report_before_update(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.customer_visit_report_before_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Préserver les informations de création et de reprise.
    NEW.created_by := OLD.created_by;
    NEW.created_at := OLD.created_at;
    NEW.request_key := OLD.request_key;
    NEW.request_hash := OLD.request_hash;

    NEW.updated_at := clock_timestamp();
    NEW.version := OLD.version + 1;

    RETURN NEW;
END;
$$;


--
-- Name: enqueue_all_kpi_knowledge_refresh(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_all_kpi_knowledge_refresh(p_reason text DEFAULT NULL::text) RETURNS void
    LANGUAGE plpgsql
    AS $$
        BEGIN
          INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
          SELECT
            k.kpi_id,
            COALESCE(NULLIF(BTRIM(COALESCE(p_reason, '')), ''), 'source_change'),
            NOW()
          FROM public.kpi k
          ON CONFLICT (kpi_id) DO UPDATE
          SET
            reason = EXCLUDED.reason,
            requested_at = EXCLUDED.requested_at;
        END;
        $$;


--
-- Name: enqueue_kpi_knowledge_refresh_for_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
          v_reason TEXT := LOWER(TG_TABLE_NAME) || '_' || LOWER(TG_OP);
          v_role_id INTEGER;
          v_people_id INTEGER;
          v_unit_id INTEGER;
          v_zone_id INTEGER;
          v_unit_type_id INTEGER;
          v_product_id INTEGER;
          v_product_line_id INTEGER;
          v_market_id INTEGER;
          v_customer_id INTEGER;
        BEGIN
          IF TG_TABLE_NAME = 'kpi' THEN
            IF TG_OP = 'INSERT' THEN
              PERFORM public.enqueue_kpi_knowledge_refresh_target(NEW.kpi_id, v_reason);
              RETURN NEW;
            END IF;

            IF TG_OP = 'UPDATE' THEN
              IF
                (to_jsonb(NEW) - 'kpi_knowledge_base' - 'kpi_knowledge_updated_date')
                IS DISTINCT FROM
                (to_jsonb(OLD) - 'kpi_knowledge_base' - 'kpi_knowledge_updated_date')
              THEN
                PERFORM public.enqueue_kpi_knowledge_refresh_target(NEW.kpi_id, v_reason);

                INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
                SELECT DISTINCT
                  ref.kpi_id,
                  'reference_kpi_updated',
                  NOW()
                FROM public.kpi ref
                WHERE ref.reference_kpi_id = NEW.kpi_id
                  AND ref.kpi_id <> NEW.kpi_id
                ON CONFLICT (kpi_id) DO UPDATE
                SET
                  reason = EXCLUDED.reason,
                  requested_at = EXCLUDED.requested_at;
              END IF;

              RETURN NEW;
            END IF;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'subject_kpi' THEN
            PERFORM public.enqueue_kpi_knowledge_refresh_target(COALESCE(NEW.kpi_id, OLD.kpi_id), v_reason);

            IF TG_OP = 'UPDATE' AND NEW.kpi_id IS DISTINCT FROM OLD.kpi_id THEN
              PERFORM public.enqueue_kpi_knowledge_refresh_target(OLD.kpi_id, 'subject_kpi_relinked');
            END IF;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'subject' THEN
            PERFORM public.enqueue_all_kpi_knowledge_refresh(v_reason);
            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'kpi_target_allocation' THEN
            PERFORM public.enqueue_kpi_knowledge_refresh_target(COALESCE(NEW.kpi_id, OLD.kpi_id), v_reason);

            IF TG_OP = 'UPDATE' AND NEW.kpi_id IS DISTINCT FROM OLD.kpi_id THEN
              PERFORM public.enqueue_kpi_knowledge_refresh_target(OLD.kpi_id, 'kpi_target_allocation_relinked');
            END IF;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'kpi_result' THEN
            PERFORM public.enqueue_kpi_knowledge_refresh_target(COALESCE(NEW.kpi_id, OLD.kpi_id), v_reason);

            IF TG_OP = 'UPDATE' AND NEW.kpi_id IS DISTINCT FROM OLD.kpi_id THEN
              PERFORM public.enqueue_kpi_knowledge_refresh_target(OLD.kpi_id, 'kpi_result_relinked');
            END IF;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'role' THEN
            v_role_id := COALESCE(NEW.role_id, OLD.role_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              affected.kpi_id,
              v_reason,
              NOW()
            FROM (
              SELECT k.kpi_id
              FROM public.kpi k
              WHERE k.owner_role_id = v_role_id
              UNION
              SELECT a.kpi_id
              FROM public.kpi_target_allocation a
              WHERE a.role_id = v_role_id
            ) affected
            WHERE affected.kpi_id IS NOT NULL
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'people' THEN
            v_people_id := COALESCE(NEW.people_id, OLD.people_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              affected.kpi_id,
              v_reason,
              NOW()
            FROM (
              SELECT k.kpi_id
              FROM public.kpi k
              WHERE k.created_by_people_id = v_people_id
              UNION
              SELECT a.kpi_id
              FROM public.kpi_target_allocation a
              WHERE a.set_by_people_id = v_people_id
                 OR a.created_by_people_id = v_people_id
                 OR a.approved_by_people_id = v_people_id
              UNION
              SELECT r.kpi_id
              FROM public.kpi_result r
              WHERE r.recorded_by_people_id = v_people_id
            ) affected
            WHERE affected.kpi_id IS NOT NULL
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'unit' THEN
            v_unit_id := COALESCE(NEW.unit_id, OLD.unit_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.plant_id = v_unit_id
               OR a.unit_id = v_unit_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'zone' THEN
            v_zone_id := COALESCE(NEW.zone_id, OLD.zone_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.zone_id = v_zone_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'unit_type' THEN
            v_unit_type_id := COALESCE(NEW.unit_type_id, OLD.unit_type_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.unit_type_id = v_unit_type_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'product' THEN
            v_product_id := COALESCE(NEW.product_id, OLD.product_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.product_id = v_product_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'product_line' THEN
            v_product_line_id := COALESCE(NEW.product_line_id, OLD.product_line_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.product_line_id = v_product_line_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'market' THEN
            v_market_id := COALESCE(NEW.market_id, OLD.market_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.market_id = v_market_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          IF TG_TABLE_NAME = 'customer' THEN
            v_customer_id := COALESCE(NEW.customer_id, OLD.customer_id);

            INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
            SELECT DISTINCT
              a.kpi_id,
              v_reason,
              NOW()
            FROM public.kpi_target_allocation a
            WHERE a.customer_id = v_customer_id
            ON CONFLICT (kpi_id) DO UPDATE
            SET
              reason = EXCLUDED.reason,
              requested_at = EXCLUDED.requested_at;

            RETURN COALESCE(NEW, OLD);
          END IF;

          RETURN COALESCE(NEW, OLD);
        END;
        $$;


--
-- Name: enqueue_kpi_knowledge_refresh_target(integer, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enqueue_kpi_knowledge_refresh_target(p_kpi_id integer, p_reason text DEFAULT NULL::text) RETURNS void
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF p_kpi_id IS NULL THEN
            RETURN;
          END IF;

          INSERT INTO public.kpi_knowledge_refresh_queue (kpi_id, reason, requested_at)
          VALUES (
            p_kpi_id,
            COALESCE(NULLIF(BTRIM(COALESCE(p_reason, '')), ''), 'source_change'),
            NOW()
          )
          ON CONFLICT (kpi_id) DO UPDATE
          SET
            reason = EXCLUDED.reason,
            requested_at = EXCLUDED.requested_at;
        END;
        $$;


--
-- Name: export_database_to_json(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.export_database_to_json() RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
    result JSONB := '{}'::JSONB;
    table_data JSONB;
BEGIN

    FOR r IN
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        AND table_type = 'BASE TABLE'
        ORDER BY table_name
    LOOP

        EXECUTE format(
            'SELECT COALESCE(jsonb_agg(t), ''[]''::jsonb)
             FROM (SELECT * FROM %I.%I) t',
            'public',
            r.table_name
        )
        INTO table_data;

        result := result || jsonb_build_object(r.table_name, table_data);

    END LOOP;

    RETURN result;
END;
$$;


--
-- Name: ext_group_key(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.ext_group_key(p_name text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE
    AS $$
DECLARE
    s     text;
    prev  text;
    suffixes text := '(group|czech|dalian|india|shanghai|changshu|wenling|mexico|brazil|germany|korea|usa|china|export|domestic|enterprises|electric|elec|motor|motors|technology|technologies|co|ltd|inc|corp|corporation|sas|gmbh|automobile|automotive|electricappliance|intelligent)';
BEGIN
    -- minuscules + tout caractere non alphanumerique -> espace
    s := regexp_replace(lower(coalesce(p_name, '')), '[^a-z0-9]+', ' ', 'g');
    -- pad pour que chaque mot soit entoure d'espaces
    s := ' ' || s || ' ';
    -- retirer les suffixes geo/business, en boucle (multi-suffixes successifs)
    LOOP
        prev := s;
        s := regexp_replace(s, ' ' || suffixes || ' ', ' ', 'g');
        EXIT WHEN s = prev;
    END LOOP;
    s := trim(s);
    RETURN NULLIF(s, '');
END;
$$;


--
-- Name: f_escalade(integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.f_escalade(p_people_id integer) RETURNS TABLE(niveau integer, personne text, role_name character varying, email character varying)
    LANGUAGE sql STABLE
    AS $$
WITH RECURSIVE chaine AS (
    SELECT assignment_id, people_id, reports_to, 0 AS niveau
    FROM people_role_assignment
    WHERE people_id = p_people_id AND is_primary AND assignment_status='ACTIVE'
    UNION ALL
    SELECT m.assignment_id, m.people_id, m.reports_to, c.niveau+1
    FROM people_role_assignment m JOIN chaine c ON m.assignment_id = c.reports_to
    WHERE m.assignment_status='ACTIVE'
)
SELECT c.niveau,
       (p.first_name || ' ' || p.name)::text,
       r.role_name,
       p.email
FROM chaine c
JOIN people p ON p.people_id = c.people_id
JOIN people_role_assignment a ON a.assignment_id = c.assignment_id
LEFT JOIN role r ON r.role_id = a.role_id
ORDER BY c.niveau;
$$;


--
-- Name: fn_check_kpi_result_kpi_id(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_check_kpi_result_kpi_id() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.kpi_target_allocation
    WHERE kpi_target_allocation_id = NEW.kpi_target_allocation_id
      AND kpi_id = NEW.kpi_id
  ) THEN
    RAISE EXCEPTION
      'kpi_result.kpi_id=% ne correspond pas au kpi_id de l''allocation id=%',
      NEW.kpi_id, NEW.kpi_target_allocation_id;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: fn_kpi_calc_limits(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_kpi_calc_limits() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  normalized_target numeric;
  normalized_up numeric;
  normalized_low numeric;
  up_delta numeric;
  low_delta numeric;
BEGIN
  IF LOWER(TRIM(COALESCE(NEW.tolerance_type, ''))) = 'relative' THEN
    normalized_target := NEW.target_value;

    normalized_up := REPLACE(REPLACE(BTRIM(COALESCE(NEW.up_tolerance, '')), '%', ''), ',', '.')::numeric;
    normalized_low := REPLACE(REPLACE(BTRIM(COALESCE(NEW.low_tolerance, '')), '%', ''), ',', '.')::numeric;

    up_delta := CASE
      WHEN POSITION('%' IN COALESCE(NEW.up_tolerance, '')) > 0 OR ABS(normalized_up) > 1
      THEN ABS(normalized_up) / 100
      ELSE ABS(normalized_up)
    END;

    low_delta := CASE
      WHEN POSITION('%' IN COALESCE(NEW.low_tolerance, '')) > 0 OR ABS(normalized_low) > 1
      THEN ABS(normalized_low) / 100
      ELSE ABS(normalized_low)
    END;

    NEW.high_limit := normalized_target * (1 + up_delta);
    NEW.low_limit := normalized_target * (1 - low_delta);
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: fn_log_unit_factory_cost_column_changes(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_log_unit_factory_cost_column_changes() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_old_json JSONB;
    v_new_json JSONB;
BEGIN
    -- Logger only for Factory units
    -- Factory = unit_type_id 4
    IF NEW.unit_type_id <> 4 THEN
        RETURN NEW;
    END IF;

    v_old_json := jsonb_build_object(
        'zone_id', OLD.zone_id,
        'direct_labor_cost_per_hour', OLD.direct_labor_cost_per_hour,
        'base_variable_overhead_cost_per_hour', OLD.base_variable_overhead_cost_per_hour,
        'number_of_shifts', OLD.number_of_shifts,
        'hours_per_shift', OLD.hours_per_shift,
        'open_hours_per_year', OLD.open_hours_per_year,
        'foh_dc_percent', OLD.foh_dc_percent,
        'fee_dc_percent', OLD.fee_dc_percent,
        'company_tax_percent', OLD.company_tax_percent,
        'y_minus_1_sales', OLD.y_minus_1_sales,
        'shift_1_premium_percent', OLD.shift_1_premium_percent,
        'shift_2_premium_percent', OLD.shift_2_premium_percent,
        'shift_3_premium_percent', OLD.shift_3_premium_percent,
        'fully_loaded_hourly_cost', OLD.fully_loaded_hourly_cost,
        'factory_cost_comment', OLD.factory_cost_comment
    );

    v_new_json := jsonb_build_object(
        'zone_id', NEW.zone_id,
        'direct_labor_cost_per_hour', NEW.direct_labor_cost_per_hour,
        'base_variable_overhead_cost_per_hour', NEW.base_variable_overhead_cost_per_hour,
        'number_of_shifts', NEW.number_of_shifts,
        'hours_per_shift', NEW.hours_per_shift,
        'open_hours_per_year', NEW.open_hours_per_year,
        'foh_dc_percent', NEW.foh_dc_percent,
        'fee_dc_percent', NEW.fee_dc_percent,
        'company_tax_percent', NEW.company_tax_percent,
        'y_minus_1_sales', NEW.y_minus_1_sales,
        'shift_1_premium_percent', NEW.shift_1_premium_percent,
        'shift_2_premium_percent', NEW.shift_2_premium_percent,
        'shift_3_premium_percent', NEW.shift_3_premium_percent,
        'fully_loaded_hourly_cost', NEW.fully_loaded_hourly_cost,
        'factory_cost_comment', NEW.factory_cost_comment
    );

    IF NEW.zone_id IS DISTINCT FROM OLD.zone_id THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'zone_id',
            OLD.zone_id::TEXT, NEW.zone_id::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.direct_labor_cost_per_hour IS DISTINCT FROM OLD.direct_labor_cost_per_hour THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'direct_labor_cost_per_hour',
            OLD.direct_labor_cost_per_hour::TEXT, NEW.direct_labor_cost_per_hour::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.base_variable_overhead_cost_per_hour IS DISTINCT FROM OLD.base_variable_overhead_cost_per_hour THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'base_variable_overhead_cost_per_hour',
            OLD.base_variable_overhead_cost_per_hour::TEXT, NEW.base_variable_overhead_cost_per_hour::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.number_of_shifts IS DISTINCT FROM OLD.number_of_shifts THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'number_of_shifts',
            OLD.number_of_shifts::TEXT, NEW.number_of_shifts::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.hours_per_shift IS DISTINCT FROM OLD.hours_per_shift THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'hours_per_shift',
            OLD.hours_per_shift::TEXT, NEW.hours_per_shift::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.open_hours_per_year IS DISTINCT FROM OLD.open_hours_per_year THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'open_hours_per_year',
            OLD.open_hours_per_year::TEXT, NEW.open_hours_per_year::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.foh_dc_percent IS DISTINCT FROM OLD.foh_dc_percent THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'foh_dc_percent',
            OLD.foh_dc_percent::TEXT, NEW.foh_dc_percent::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.fee_dc_percent IS DISTINCT FROM OLD.fee_dc_percent THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'fee_dc_percent',
            OLD.fee_dc_percent::TEXT, NEW.fee_dc_percent::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.company_tax_percent IS DISTINCT FROM OLD.company_tax_percent THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'company_tax_percent',
            OLD.company_tax_percent::TEXT, NEW.company_tax_percent::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.y_minus_1_sales IS DISTINCT FROM OLD.y_minus_1_sales THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'y_minus_1_sales',
            OLD.y_minus_1_sales::TEXT, NEW.y_minus_1_sales::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.shift_1_premium_percent IS DISTINCT FROM OLD.shift_1_premium_percent THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'shift_1_premium_percent',
            OLD.shift_1_premium_percent::TEXT, NEW.shift_1_premium_percent::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.shift_2_premium_percent IS DISTINCT FROM OLD.shift_2_premium_percent THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'shift_2_premium_percent',
            OLD.shift_2_premium_percent::TEXT, NEW.shift_2_premium_percent::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.shift_3_premium_percent IS DISTINCT FROM OLD.shift_3_premium_percent THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'shift_3_premium_percent',
            OLD.shift_3_premium_percent::TEXT, NEW.shift_3_premium_percent::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    IF NEW.fully_loaded_hourly_cost IS DISTINCT FROM OLD.fully_loaded_hourly_cost THEN
        INSERT INTO public.unit_factory_cost_change_log (
            unit_id, unit_name, column_name, old_value, new_value,
            old_row_data, new_row_data, change_comment
        )
        VALUES (
            NEW.unit_id, NEW.unit_name, 'fully_loaded_hourly_cost',
            OLD.fully_loaded_hourly_cost::TEXT, NEW.fully_loaded_hourly_cost::TEXT,
            v_old_json, v_new_json, NEW.factory_cost_comment
        );
    END IF;

    NEW.factory_cost_updated_at := NOW();

    RETURN NEW;
END;
$$;


--
-- Name: fn_manage_customer_code(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_manage_customer_code() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
BEGIN
    -- Sales Management creates the code; supplied values are ignored.
    IF TG_OP = 'INSERT'
       AND NEW.insert_type = 'SALES_MANAGER'
       AND NEW.insert_source = 'CUSTOMER_APPLICATION'
    THEN
        NEW.customer_code :=
            'CUS-' ||
            LPAD(
                nextval('public.customer_code_seq')::text,
                8,
                '0'
            );
    END IF;

    -- A standardized code is permanent.
    IF TG_OP = 'UPDATE'
       AND OLD.customer_code ~ '^CUS-[0-9]{8}$'
       AND NEW.customer_code IS DISTINCT FROM OLD.customer_code
    THEN
        RAISE EXCEPTION
            'Customer code % is permanent and cannot be modified.',
            OLD.customer_code;
    END IF;

    RETURN NEW;
END;
$_$;


--
-- Name: fn_notify_kam_visits_changed(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_notify_kam_visits_changed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_kpi_id integer;
    v_allocation_id integer;
    v_week text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_kpi_id := OLD.kpi_id;
        v_allocation_id :=
            OLD.kpi_target_allocation_id;
        v_week := OLD.week;
    ELSE
        v_kpi_id := NEW.kpi_id;
        v_allocation_id :=
            NEW.kpi_target_allocation_id;
        v_week := NEW.week;
    END IF;

    IF v_kpi_id = 655
       OR (TG_OP = 'UPDATE'
           AND OLD.kpi_id = 655)
    THEN
        PERFORM pg_notify(
            'kam_visits_changed',
            json_build_object(
                'operation', TG_OP,
                'allocation_id', v_allocation_id,
                'week', v_week,
                'kpi_id', 655
            )::text
        );
    END IF;

    RETURN NULL;
END;
$$;


--
-- Name: fn_sync_customer_main_contacts(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_sync_customer_main_contacts() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE v_role_name TEXT;
BEGIN
  SELECT role_name INTO v_role_name
  FROM public.role
  WHERE role_id = COALESCE(NEW.role_id, OLD.role_id);

  IF v_role_name IN ('KAM','Zone Manager','Large Accounts Director') THEN
    IF TG_OP IN ('INSERT','UPDATE') AND NEW.is_primary = TRUE THEN
      UPDATE public.customer
      SET main_kam_people_id = NEW.people_id
      WHERE zone_id = NEW.zone_id
        AND main_kam_people_id IS NULL;
    ELSIF TG_OP = 'DELETE' THEN
      UPDATE public.customer
      SET main_kam_people_id = NULL
      WHERE main_kam_people_id = OLD.people_id;
    END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$;


--
-- Name: fn_sync_target_value_to_kpi_result(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fn_sync_target_value_to_kpi_result() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN

  -- ① Guard: only proceed if target_value actually changed
  IF NEW.target_value IS NOT DISTINCT FROM OLD.target_value THEN
    RETURN NEW;
  END IF;

  -- ③ Propagate new target_value to all matching kpi_result rows
  --    trg_kpi_result_calc_limits will fire automatically on this UPDATE (step ④)
  UPDATE public.kpi_result
  SET    target_value = NEW.target_value
  WHERE  kpi_target_allocation_id = NEW.kpi_target_allocation_id
    AND  kpi_id                   = NEW.kpi_id;

  RETURN NEW;

END;
$$;


--
-- Name: notify_role_assignment_change(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_role_assignment_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF NEW.is_primary IS TRUE AND NEW.assignment_status = 'ACTIVE' THEN
    PERFORM pg_notify(
      'kpi_role_change',
      json_build_object('people_id', NEW.people_id, 'role_id', NEW.role_id)::text
    );
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: trg_kpi_calc_limits(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_kpi_calc_limits() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE
  normalized_target numeric;
  normalized_up     numeric;
  normalized_low    numeric;
  up_delta          numeric;
  low_delta         numeric;
BEGIN
  -- Skip if limits are already set (only compute on first assignment)
  IF TG_OP = 'UPDATE' AND (OLD.high_limit IS NOT NULL OR OLD.low_limit IS NOT NULL) THEN
    RETURN NEW;
  END IF;

  IF LOWER(TRIM(COALESCE(NEW.tolerance_type, ''))) = 'relative' THEN

    normalized_target := NEW.target_value;

    normalized_up := CASE
      WHEN NULLIF(BTRIM(COALESCE(NEW.up_tolerance, '')), '') IS NULL THEN NULL
      WHEN REPLACE(REPLACE(BTRIM(NEW.up_tolerance), '%', ''), ',', '.')
             ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
        THEN REPLACE(REPLACE(BTRIM(NEW.up_tolerance), '%', ''), ',', '.')::numeric
      ELSE NULL
    END;

    normalized_low := CASE
      WHEN NULLIF(BTRIM(COALESCE(NEW.low_tolerance, '')), '') IS NULL THEN NULL
      WHEN REPLACE(REPLACE(BTRIM(NEW.low_tolerance), '%', ''), ',', '.')
             ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
        THEN REPLACE(REPLACE(BTRIM(NEW.low_tolerance), '%', ''), ',', '.')::numeric
      ELSE NULL
    END;

    IF normalized_target IS NULL OR normalized_up IS NULL OR normalized_low IS NULL THEN
      NEW.high_limit := NULL;
      NEW.low_limit  := NULL;
      RETURN NEW;
    END IF;

    up_delta := CASE
      WHEN POSITION('%' IN COALESCE(NEW.up_tolerance, '')) > 0
        OR ABS(normalized_up) > 1
        THEN ABS(normalized_up) / 100
      ELSE ABS(normalized_up)
    END;

    low_delta := CASE
      WHEN POSITION('%' IN COALESCE(NEW.low_tolerance, '')) > 0
        OR ABS(normalized_low) > 1
        THEN ABS(normalized_low) / 100
      ELSE ABS(normalized_low)
    END;

    NEW.high_limit := normalized_target * (1 + up_delta);
    NEW.low_limit  := normalized_target * (1 - low_delta);

  END IF;

  RETURN NEW;
END;
$_$;


--
-- Name: trg_kpi_result_calc_limits(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_kpi_result_calc_limits() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE
  v_tolerance_type  varchar;
  v_up_tolerance    varchar;
  v_low_tolerance   varchar;
  normalized_target numeric;
  normalized_up     numeric;
  normalized_low    numeric;
  up_delta          numeric;
  low_delta         numeric;
BEGIN
  -- Skip if target_value is NULL
  IF NEW.target_value IS NULL THEN
    RETURN NEW;
  END IF;

  -- Fetch tolerance config from parent kpi
  SELECT tolerance_type, up_tolerance, low_tolerance
    INTO v_tolerance_type, v_up_tolerance, v_low_tolerance
    FROM public.kpi
   WHERE kpi_id = NEW.kpi_id;

  IF LOWER(TRIM(COALESCE(v_tolerance_type, ''))) = 'relative' THEN

    normalized_target := NEW.target_value;

    normalized_up := CASE
      WHEN NULLIF(BTRIM(COALESCE(v_up_tolerance, '')), '') IS NULL THEN NULL
      WHEN REPLACE(REPLACE(BTRIM(v_up_tolerance), '%', ''), ',', '.')
             ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
        THEN ABS(REPLACE(REPLACE(BTRIM(v_up_tolerance), '%', ''), ',', '.')::numeric)
      ELSE NULL
    END;

    normalized_low := CASE
      WHEN NULLIF(BTRIM(COALESCE(v_low_tolerance, '')), '') IS NULL THEN NULL
      WHEN REPLACE(REPLACE(BTRIM(v_low_tolerance), '%', ''), ',', '.')
             ~ '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)$'
        THEN ABS(REPLACE(REPLACE(BTRIM(v_low_tolerance), '%', ''), ',', '.')::numeric)
      ELSE NULL
    END;

    IF normalized_up IS NULL OR normalized_low IS NULL THEN
      NEW.upper_limit := NULL;
      NEW.lower_limit := NULL;
      RETURN NEW;
    END IF;

    up_delta := CASE
      WHEN POSITION('%' IN COALESCE(v_up_tolerance, '')) > 0 OR normalized_up > 1
        THEN normalized_up / 100
      ELSE normalized_up
    END;

    low_delta := CASE
      WHEN POSITION('%' IN COALESCE(v_low_tolerance, '')) > 0 OR normalized_low > 1
        THEN normalized_low / 100
      ELSE normalized_low
    END;

    NEW.upper_limit := normalized_target * (1 + up_delta);
    NEW.lower_limit := normalized_target * (1 - low_delta);

  END IF;
  RETURN NEW;
END;
$_$;


--
-- Name: f_fill_people(text, text); Type: FUNCTION; Schema: stg; Owner: -
--

CREATE FUNCTION stg.f_fill_people(col text, typ text DEFAULT 'text'::text) RETURNS integer
    LANGUAGE plpgsql
    AS $_$
DECLARE n int;
BEGIN
    EXECUTE format($q$
        WITH upd AS (
            UPDATE people p SET %1$I = e.val::%3$s
            FROM (SELECT matched_people_id AS pid,
                         CASE %2$L
                           WHEN 'email'          THEN email_clean
                           WHEN 'gender'         THEN gender
                           WHEN 'hr_employee_id' THEN matricule
                           WHEN 'hr_site'        THEN src_sheet
                           WHEN 'hire_date'      THEN hire_date::text
                           WHEN 'date_of_birth'  THEN date_of_birth::text
                         END AS val
                  FROM stg.employees
                  WHERE matched_people_id IS NOT NULL AND NOT excluded
                    AND match_method <> 'NEW') e
            WHERE p.people_id = e.pid AND p.%1$I IS NULL AND e.val IS NOT NULL
              AND e.pid NOT IN (SELECT people_id FROM stg.kpi_people_inserted)
              AND (%2$L <> 'email' OR NOT EXISTS
                   (SELECT 1 FROM people x WHERE lower(x.email)=lower(e.val)))
              AND (%2$L <> 'hr_employee_id' OR NOT EXISTS
                   (SELECT 1 FROM people x WHERE x.hr_employee_id = e.val
                      AND x.work_at_unit_id = p.work_at_unit_id
                      AND x.people_id <> p.people_id))
            RETURNING p.people_id, e.val)
        INSERT INTO stg.kpi_field_fills (tbl, pk, col, filled_value)
        SELECT 'people', people_id, %2$L, val FROM upd
        ON CONFLICT DO NOTHING$q$, col, col, typ);
    GET DIAGNOSTICS n = ROW_COUNT; RETURN n;
END $_$;


--
-- Name: f_norm(text); Type: FUNCTION; Schema: stg; Owner: -
--

CREATE FUNCTION stg.f_norm(t text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
  SELECT NULLIF(trim(regexp_replace(lower(translate(coalesce(t,''),
    'ÀÁÂÃÄÅàáâãäåÈÉÊËèéêëÌÍÎÏìíîïÒÓÔÕÖòóôõöÙÚÛÜùúûüÇçÑñŸÿ­ ',
    'AAAAAAaaaaaaEEEEeeeeIIIIiiiiOOOOOoooooUUUUuuuuCcNnYy  ')),
    '\s+', ' ', 'g')), '')
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: corrective_action_escalation_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.corrective_action_escalation_log (
    escalation_id bigint NOT NULL,
    corrective_action_id bigint NOT NULL,
    escalation_stage text NOT NULL,
    sent_to_email text,
    sent_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: corrective_action_escalation_log_escalation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.corrective_action_escalation_log_escalation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: corrective_action_escalation_log_escalation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.corrective_action_escalation_log_escalation_id_seq OWNED BY public.corrective_action_escalation_log.escalation_id;


--
-- Name: customer; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer (
    customer_id integer NOT NULL,
    customer_name character varying(255) NOT NULL,
    customer_code character varying(100),
    parent_customer_id integer,
    customer_type character varying(100) NOT NULL,
    headquarter_country character varying(100),
    country character varying(100),
    city character varying(100),
    zone_id integer,
    market_id integer,
    main_sales_people_id integer,
    main_kam_people_id integer,
    customer_status character varying(50) NOT NULL,
    strategic_level character varying(50),
    customer_scope text,
    customer_files_link text,
    comments text,
    tmp_global_name text,
    insert_type character varying(30) DEFAULT 'MANUAL'::character varying NOT NULL,
    insert_source character varying(100),
    source_reference character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by character varying(255),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by character varying(255),
    record_version integer DEFAULT 1 NOT NULL,
    is_validated boolean DEFAULT false NOT NULL,
    validated_at timestamp with time zone,
    validated_by character varying(255),
    validation_status character varying(30) DEFAULT 'TO_REVIEW'::character varying NOT NULL,
    validation_comment text,
    is_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by character varying(255),
    CONSTRAINT chk_customer_status CHECK (((customer_status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Prospect'::character varying)::text, ('Dormant'::character varying)::text, ('Lost'::character varying)::text, ('Blocked'::character varying)::text, ('Archived'::character varying)::text]))),
    CONSTRAINT chk_customer_type CHECK (((customer_type)::text = ANY (ARRAY[('Customer Group'::character varying)::text, ('Customer Legal Entity'::character varying)::text, ('Manufacturing Site'::character varying)::text, ('Purchasing Center'::character varying)::text, ('Design Center'::character varying)::text, ('R&D Center'::character varying)::text, ('Distribution Center'::character varying)::text, ('Sales Office'::character varying)::text, ('Aftermarket Unit'::character varying)::text]))),
    CONSTRAINT chk_strategic_level CHECK (((strategic_level)::text = ANY (ARRAY[('Strategic'::character varying)::text, ('Key Account'::character varying)::text, ('Standard'::character varying)::text, ('Opportunistic'::character varying)::text, ('Low Priority'::character varying)::text])))
);


--
-- Name: customer_code_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.customer_code_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: customer_customer_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer ALTER COLUMN customer_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_customer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_delivery_unit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_delivery_unit (
    customer_delivery_id integer NOT NULL,
    customer_id integer NOT NULL,
    unit_id integer NOT NULL,
    product_id integer,
    delivery_status character varying(50) DEFAULT 'Active'::character varying NOT NULL,
    start_date date,
    end_date date,
    comments text
);


--
-- Name: customer_delivery_unit_customer_delivery_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_delivery_unit ALTER COLUMN customer_delivery_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_delivery_unit_customer_delivery_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_document_access; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_document_access (
    customer_id integer NOT NULL,
    principal_id text NOT NULL,
    can_read boolean DEFAULT false NOT NULL,
    can_write boolean DEFAULT false NOT NULL,
    can_validate boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    granted_by text NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT customer_document_access_check CHECK (((NOT can_write) OR can_read)),
    CONSTRAINT customer_document_access_check1 CHECK (((NOT can_validate) OR can_read)),
    CONSTRAINT customer_document_access_principal_id_check CHECK ((btrim(principal_id) <> ''::text))
);


--
-- Name: TABLE customer_document_access; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.customer_document_access IS 'Verified token principal ACL; names supplied by tools are never authorization identities. Managed out of band by a database administrator.';


--
-- Name: customer_document_attachment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_document_attachment (
    attachment_id bigint NOT NULL,
    revision_id bigint NOT NULL,
    customer_id integer NOT NULL,
    document_type text NOT NULL,
    original_filename text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL,
    blob_container text NOT NULL,
    blob_name text NOT NULL,
    blob_version_id text,
    checksum_sha256 text,
    caption text,
    status text DEFAULT 'pending'::text NOT NULL,
    request_key text NOT NULL,
    request_hash text NOT NULL,
    uploaded_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    uploaded_at timestamp with time zone,
    CONSTRAINT customer_document_attachment_blob_container_check CHECK ((btrim(blob_container) <> ''::text)),
    CONSTRAINT customer_document_attachment_blob_name_check CHECK ((btrim(blob_name) <> ''::text)),
    CONSTRAINT customer_document_attachment_check CHECK (((status <> 'ready'::text) OR ((blob_version_id IS NOT NULL) AND (checksum_sha256 IS NOT NULL) AND (uploaded_at IS NOT NULL)))),
    CONSTRAINT customer_document_attachment_check1 CHECK ((((document_type = 'dossier_json'::text) AND (content_type = 'application/json'::text)) OR ((document_type = 'dossier_pdf'::text) AND (content_type = 'application/pdf'::text)) OR ((document_type = 'image'::text) AND (content_type = ANY (ARRAY['image/jpeg'::text, 'image/png'::text]))))),
    CONSTRAINT customer_document_attachment_checksum_sha256_check CHECK ((checksum_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT customer_document_attachment_document_type_check CHECK ((document_type = ANY (ARRAY['dossier_json'::text, 'dossier_pdf'::text, 'image'::text]))),
    CONSTRAINT customer_document_attachment_original_filename_check CHECK ((btrim(original_filename) <> ''::text)),
    CONSTRAINT customer_document_attachment_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT customer_document_attachment_request_key_check CHECK (((length(request_key) >= 1) AND (length(request_key) <= 100))),
    CONSTRAINT customer_document_attachment_size_bytes_check CHECK (((size_bytes >= 1) AND (size_bytes <= 20971520))),
    CONSTRAINT customer_document_attachment_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'ready'::text, 'failed'::text, 'archived'::text])))
);


--
-- Name: customer_document_attachment_attachment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_document_attachment ALTER COLUMN attachment_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_document_attachment_attachment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_document_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_document_event (
    event_id bigint NOT NULL,
    customer_id integer NOT NULL,
    entity_table text NOT NULL,
    entity_id text NOT NULL,
    operation text NOT NULL,
    actor text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    old_data jsonb,
    new_data jsonb
);


--
-- Name: customer_document_event_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_document_event ALTER COLUMN event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_document_event_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_dossier; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_dossier (
    dossier_id bigint NOT NULL,
    customer_id integer NOT NULL,
    dossier_type text DEFAULT 'customer_account_brief'::text NOT NULL,
    next_revision_no integer DEFAULT 1 NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT customer_dossier_dossier_type_check CHECK ((btrim(dossier_type) <> ''::text)),
    CONSTRAINT customer_dossier_next_revision_no_check CHECK ((next_revision_no > 0))
);


--
-- Name: TABLE customer_dossier; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.customer_dossier IS 'Permanent client dossier; each validated revision has its own JSON, PDF and images in private Azure blobs.';


--
-- Name: customer_dossier_dossier_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_dossier ALTER COLUMN dossier_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_dossier_dossier_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_dossier_revision; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_dossier_revision (
    revision_id bigint NOT NULL,
    dossier_id bigint NOT NULL,
    customer_id integer NOT NULL,
    revision_no integer NOT NULL,
    research_date date NOT NULL,
    revision_note text,
    status text DEFAULT 'draft'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    request_key text NOT NULL,
    request_hash text NOT NULL,
    created_by text NOT NULL,
    updated_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    validated_by text,
    validated_at timestamp with time zone,
    validation_note text,
    CONSTRAINT customer_dossier_revision_check CHECK (((status <> 'validated'::text) OR ((validated_by IS NOT NULL) AND (validated_at IS NOT NULL) AND (validation_note IS NOT NULL) AND (btrim(validation_note) <> ''::text)))),
    CONSTRAINT customer_dossier_revision_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT customer_dossier_revision_request_key_check CHECK (((length(request_key) >= 1) AND (length(request_key) <= 100))),
    CONSTRAINT customer_dossier_revision_revision_no_check CHECK ((revision_no > 0)),
    CONSTRAINT customer_dossier_revision_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'validated'::text, 'archived'::text]))),
    CONSTRAINT customer_dossier_revision_version_check CHECK ((version > 0))
);


--
-- Name: customer_dossier_revision_revision_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_dossier_revision ALTER COLUMN revision_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_dossier_revision_revision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_file; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_file (
    customer_file_id integer NOT NULL,
    customer_id integer NOT NULL,
    file_name character varying(255) NOT NULL,
    file_type character varying(100),
    file_url text NOT NULL,
    uploaded_at timestamp with time zone DEFAULT now(),
    uploaded_by_people_id integer
);


--
-- Name: customer_file_customer_file_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_file ALTER COLUMN customer_file_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_file_customer_file_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_master_api_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_master_api_audit (
    audit_id bigint NOT NULL,
    customer_id integer NOT NULL,
    operation character varying(20) NOT NULL,
    actor text NOT NULL,
    old_values jsonb DEFAULT '{}'::jsonb NOT NULL,
    new_values jsonb DEFAULT '{}'::jsonb NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT customer_master_api_audit_operation_check CHECK (((operation)::text = ANY (ARRAY[('CREATE'::character varying)::text, ('UPDATE'::character varying)::text])))
);


--
-- Name: customer_master_api_audit_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_master_api_audit ALTER COLUMN audit_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_master_api_audit_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_project; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_project (
    customer_project_id integer NOT NULL,
    customer_id integer NOT NULL,
    project_id integer NOT NULL,
    customer_role_in_project character varying(100) NOT NULL,
    comments text
);


--
-- Name: customer_project_customer_project_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_project ALTER COLUMN customer_project_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_project_customer_project_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_rfq_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_rfq_link (
    rfq_id text NOT NULL,
    customer_id integer NOT NULL,
    verified_by text NOT NULL,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    verification_note text NOT NULL,
    CONSTRAINT customer_rfq_link_rfq_id_check CHECK ((btrim(rfq_id) <> ''::text)),
    CONSTRAINT customer_rfq_link_verification_note_check CHECK ((btrim(verification_note) <> ''::text)),
    CONSTRAINT customer_rfq_link_verified_by_check CHECK ((btrim(verified_by) <> ''::text))
);


--
-- Name: TABLE customer_rfq_link; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.customer_rfq_link IS 'Correspondance vérifiée entre un client et une RFQ.
La RFQ est située dans rfq_db ; son existence est contrôlée par le MCP.';


--
-- Name: customer_visit_attachment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_visit_attachment (
    attachment_id bigint NOT NULL,
    visit_report_id bigint NOT NULL,
    original_filename text NOT NULL,
    blob_container text NOT NULL,
    blob_name text NOT NULL,
    blob_version_id text,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL,
    checksum_sha256 text,
    caption text,
    status text DEFAULT 'pending'::text NOT NULL,
    uploaded_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    uploaded_at timestamp with time zone,
    request_key text NOT NULL,
    request_hash text NOT NULL,
    CONSTRAINT customer_visit_attachment_blob_container_check CHECK ((btrim(blob_container) <> ''::text)),
    CONSTRAINT customer_visit_attachment_blob_name_check CHECK ((btrim(blob_name) <> ''::text)),
    CONSTRAINT customer_visit_attachment_checksum_sha256_check CHECK ((checksum_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT customer_visit_attachment_original_filename_check CHECK ((btrim(original_filename) <> ''::text)),
    CONSTRAINT customer_visit_attachment_ready CHECK (((status <> 'ready'::text) OR ((uploaded_at IS NOT NULL) AND (checksum_sha256 IS NOT NULL) AND (blob_version_id IS NOT NULL)))),
    CONSTRAINT customer_visit_attachment_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT customer_visit_attachment_request_key_check CHECK (((length(request_key) >= 1) AND (length(request_key) <= 100))),
    CONSTRAINT customer_visit_attachment_size_bytes_check CHECK (((size_bytes >= 1) AND (size_bytes <= 20971520))),
    CONSTRAINT customer_visit_attachment_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'ready'::text, 'failed'::text, 'archived'::text])))
);


--
-- Name: customer_visit_attachment_attachment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_visit_attachment ALTER COLUMN attachment_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_visit_attachment_attachment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_visit_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_visit_event (
    event_id bigint NOT NULL,
    entity_table text NOT NULL,
    entity_id text NOT NULL,
    operation text NOT NULL,
    actor text NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    old_data jsonb,
    new_data jsonb
);


--
-- Name: customer_visit_event_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_visit_event ALTER COLUMN event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_visit_event_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_visit_report; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_visit_report (
    visit_report_id bigint NOT NULL,
    customer_id integer,
    rfq_id text,
    title text NOT NULL,
    visit_date date NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    report_data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by text NOT NULL,
    updated_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    request_key text NOT NULL,
    request_hash text NOT NULL,
    CONSTRAINT customer_visit_report_created_by_check CHECK ((btrim(created_by) <> ''::text)),
    CONSTRAINT customer_visit_report_report_data_check CHECK ((jsonb_typeof(report_data) = 'object'::text)),
    CONSTRAINT customer_visit_report_request_hash_check CHECK ((request_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT customer_visit_report_request_key_check CHECK (((length(request_key) >= 1) AND (length(request_key) <= 100))),
    CONSTRAINT customer_visit_report_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'finalized'::text, 'archived'::text]))),
    CONSTRAINT customer_visit_report_title_check CHECK ((btrim(title) <> ''::text)),
    CONSTRAINT customer_visit_report_updated_by_check CHECK ((btrim(updated_by) <> ''::text)),
    CONSTRAINT customer_visit_report_version_check CHECK ((version > 0)),
    CONSTRAINT customer_visit_requires_link CHECK (((customer_id IS NOT NULL) OR (rfq_id IS NOT NULL)))
);


--
-- Name: COLUMN customer_visit_report.report_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.customer_visit_report.report_data IS 'Contenu JSON du rapport : schema_version, participants, objectifs,
résumé, besoins client, décisions et prochaines actions.';


--
-- Name: customer_visit_report_visit_report_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_visit_report ALTER COLUMN visit_report_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.customer_visit_report_visit_report_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ext_agent_artifact; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_artifact (
    agent_artifact_id bigint NOT NULL,
    agent_project_id bigint NOT NULL,
    agent_build_step_id bigint,
    step_number integer,
    artifact_category character varying NOT NULL,
    artifact_type character varying NOT NULL,
    artifact_name character varying NOT NULL,
    artifact_code character varying,
    artifact_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    artifact_status character varying DEFAULT '''Draft'''::character varying NOT NULL,
    description text,
    mime_type character varying,
    file_name character varying,
    file_extension character varying,
    file_location text,
    content_json jsonb,
    content_text text,
    schema_json jsonb,
    metadata_json jsonb,
    dependencies_json jsonb,
    usage_rules_json jsonb,
    validation_rules_json jsonb,
    security_classification character varying,
    is_required boolean DEFAULT false NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    sequence_number integer,
    created_by_people_id bigint,
    approved_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_at timestamp with time zone,
    comments text
);


--
-- Name: ext_agent_artifact_agent_artifact_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_artifact_agent_artifact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_artifact_agent_artifact_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_artifact_agent_artifact_id_seq OWNED BY public.ext_agent_artifact.agent_artifact_id;


--
-- Name: ext_agent_build_project; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_build_project (
    agent_project_id bigint NOT NULL,
    agent_name character varying NOT NULL,
    agent_code character varying,
    factory_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    agent_version character varying DEFAULT '''0.1'''::character varying NOT NULL,
    build_status character varying DEFAULT '''Draft'''::character varying NOT NULL,
    current_step_number integer DEFAULT 10 NOT NULL,
    completed_step_numbers jsonb,
    short_description text,
    business_problem_or_opportunity text,
    business_purpose text,
    expected_outcome text,
    operating_model jsonb,
    sponsor jsonb,
    stakeholders jsonb,
    impact_when_working_well jsonb,
    impact_when_not_working_well jsonb,
    frequency_of_operation jsonb,
    boundaries jsonb,
    facts_confirmed jsonb,
    assumptions jsonb,
    contradictions_resolved jsonb,
    open_points jsonb,
    risks_identified jsonb,
    step_10_status character varying DEFAULT '''Not Started'''::character varying NOT NULL,
    step_10_output jsonb,
    step_10_user_validated boolean DEFAULT false NOT NULL,
    step_10_validation_date timestamp with time zone,
    next_step_allowed boolean DEFAULT false NOT NULL,
    next_step_number integer DEFAULT 20,
    full_agent_definition jsonb,
    created_by_people_id bigint,
    approved_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_at timestamp with time zone,
    comments text
);


--
-- Name: ext_agent_build_project_agent_project_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_build_project_agent_project_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_build_project_agent_project_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_build_project_agent_project_id_seq OWNED BY public.ext_agent_build_project.agent_project_id;


--
-- Name: ext_agent_build_step; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_build_step (
    agent_build_step_id bigint NOT NULL,
    agent_project_id bigint NOT NULL,
    factory_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    step_number integer NOT NULL,
    step_code character varying NOT NULL,
    step_title character varying NOT NULL,
    step_status character varying DEFAULT '''Not Started'''::character varying NOT NULL,
    step_schema_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    prompt_file_name character varying,
    prompt_file_location text,
    output_file_name character varying,
    output_file_location text,
    step_output_json jsonb,
    validation_json jsonb,
    open_points_json jsonb,
    user_validated boolean DEFAULT false NOT NULL,
    validated_by_people_id bigint,
    validated_at timestamp with time zone,
    sequence_mandatory boolean DEFAULT true NOT NULL,
    next_step_number integer,
    created_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    comments text
);


--
-- Name: ext_agent_build_step_agent_build_step_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_build_step_agent_build_step_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_build_step_agent_build_step_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_build_step_agent_build_step_id_seq OWNED BY public.ext_agent_build_step.agent_build_step_id;


--
-- Name: ext_agent_build_step_v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_build_step_v2 (
    agent_build_step_id bigint NOT NULL,
    agent_project_id bigint NOT NULL,
    factory_prompt_id bigint,
    factory_version character varying NOT NULL,
    step_number integer NOT NULL,
    step_code character varying NOT NULL,
    step_title character varying NOT NULL,
    step_status character varying DEFAULT '''Not Started'''::character varying NOT NULL,
    schema_version character varying NOT NULL,
    step_output_json jsonb NOT NULL,
    open_points_json jsonb,
    validation_json jsonb,
    user_validated boolean DEFAULT false NOT NULL,
    validated_by_people_id bigint,
    validated_at timestamp with time zone,
    next_step_number integer,
    next_step_allowed boolean DEFAULT false NOT NULL,
    prompt_artifact_id bigint,
    schema_artifact_id bigint,
    output_artifact_id bigint,
    created_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    comments text
);


--
-- Name: ext_agent_build_step_v2_agent_build_step_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_build_step_v2_agent_build_step_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_build_step_v2_agent_build_step_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_build_step_v2_agent_build_step_id_seq OWNED BY public.ext_agent_build_step_v2.agent_build_step_id;


--
-- Name: ext_agent_build_step_v3; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_build_step_v3 (
    agent_step_revision_id bigint NOT NULL,
    agent_step_key uuid NOT NULL,
    agent_project_id bigint NOT NULL,
    factory_prompt_key uuid NOT NULL,
    factory_prompt_revision_id bigint NOT NULL,
    step_number integer NOT NULL,
    step_code character varying NOT NULL,
    revision_number integer NOT NULL,
    version_label character varying NOT NULL,
    previous_revision_id bigint,
    revision_status character varying DEFAULT '''Draft'''::character varying NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    step_output_json jsonb NOT NULL,
    open_points_json jsonb,
    validation_json jsonb,
    user_validated boolean DEFAULT false NOT NULL,
    validated_by_people_id bigint,
    validated_at timestamp with time zone,
    next_step_number integer,
    next_step_allowed boolean DEFAULT false NOT NULL,
    prompt_artifact_id bigint,
    schema_artifact_id bigint,
    output_artifact_id bigint,
    change_type character varying DEFAULT '''Initial'''::character varying NOT NULL,
    change_summary text NOT NULL,
    change_reason text,
    changed_fields_json jsonb,
    content_hash character varying,
    created_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    superseded_at timestamp with time zone,
    comments text
);


--
-- Name: ext_agent_build_step_v3_agent_step_revision_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_build_step_v3_agent_step_revision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_build_step_v3_agent_step_revision_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_build_step_v3_agent_step_revision_id_seq OWNED BY public.ext_agent_build_step_v3.agent_step_revision_id;


--
-- Name: ext_agent_definition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_definition (
    agent_id bigint NOT NULL,
    agent_name character varying NOT NULL,
    agent_code character varying,
    version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    status character varying DEFAULT '''Draft'''::character varying NOT NULL,
    short_description text NOT NULL,
    business_purpose text NOT NULL,
    business_problem text,
    expected_business_value text,
    pilot_mode character varying DEFAULT '''Autonomous'''::character varying NOT NULL,
    pilot_role_id bigint,
    business_owner_role_id bigint,
    sponsor_role_id bigint,
    stakeholders jsonb,
    data_sources jsonb,
    functional_scope jsonb,
    boundaries jsonb,
    distribution_rules jsonb,
    escalation_rules jsonb,
    schedule_configuration jsonb,
    failure_consequences jsonb,
    controls_and_validation jsonb,
    performance_kpis jsonb,
    technical_package jsonb,
    prompt_definition text,
    created_by_people_id bigint,
    approved_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_at timestamp with time zone,
    comments text
);


--
-- Name: ext_agent_definition_agent_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_definition_agent_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_definition_agent_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_definition_agent_id_seq OWNED BY public.ext_agent_definition.agent_id;


--
-- Name: ext_agent_factory_prompt; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_factory_prompt (
    factory_prompt_step_id bigint NOT NULL,
    factory_prompt_name character varying NOT NULL,
    factory_prompt_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    step_number integer NOT NULL,
    step_code character varying,
    step_title character varying NOT NULL,
    step_purpose text NOT NULL,
    prompt_instruction text NOT NULL,
    questions_to_ask jsonb,
    required_inputs jsonb,
    expected_outputs jsonb,
    completion_criteria jsonb,
    validation_rules jsonb,
    dependencies jsonb,
    next_step_number integer,
    sequence_mandatory boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by_people_id bigint,
    approved_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    comments text
);


--
-- Name: ext_agent_factory_prompt_factory_prompt_step_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_factory_prompt_factory_prompt_step_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_factory_prompt_factory_prompt_step_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_factory_prompt_factory_prompt_step_id_seq OWNED BY public.ext_agent_factory_prompt.factory_prompt_step_id;


--
-- Name: ext_agent_factory_prompt_v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_factory_prompt_v2 (
    factory_prompt_id bigint NOT NULL,
    factory_name character varying NOT NULL,
    factory_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    step_number integer NOT NULL,
    step_code character varying NOT NULL,
    step_title character varying NOT NULL,
    step_objective text NOT NULL,
    adaptive_prompt_text text NOT NULL,
    required_outputs_json jsonb NOT NULL,
    output_schema_json jsonb NOT NULL,
    validation_rules_json jsonb,
    completion_rule text,
    next_step_number integer,
    sequence_mandatory boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by_people_id bigint,
    approved_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_at timestamp with time zone,
    comments text
);


--
-- Name: ext_agent_factory_prompt_v2_factory_prompt_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_factory_prompt_v2_factory_prompt_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_factory_prompt_v2_factory_prompt_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_factory_prompt_v2_factory_prompt_id_seq OWNED BY public.ext_agent_factory_prompt_v2.factory_prompt_id;


--
-- Name: ext_agent_factory_prompt_v3; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_factory_prompt_v3 (
    factory_prompt_revision_id bigint NOT NULL,
    factory_prompt_key uuid NOT NULL,
    factory_name character varying NOT NULL,
    step_number integer NOT NULL,
    step_code character varying NOT NULL,
    step_title character varying NOT NULL,
    revision_number integer NOT NULL,
    version_label character varying NOT NULL,
    previous_revision_id bigint,
    revision_status character varying DEFAULT '''Draft'''::character varying NOT NULL,
    is_current boolean DEFAULT true NOT NULL,
    effective_from timestamp with time zone,
    effective_to timestamp with time zone,
    step_objective text NOT NULL,
    adaptive_prompt_text text NOT NULL,
    required_outputs_json jsonb NOT NULL,
    output_schema_json jsonb NOT NULL,
    validation_rules_json jsonb,
    completion_rule text NOT NULL,
    next_step_number integer,
    sequence_mandatory boolean DEFAULT true NOT NULL,
    change_type character varying DEFAULT '''Initial'''::character varying NOT NULL,
    change_summary text NOT NULL,
    change_reason text,
    changed_fields_json jsonb,
    content_hash character varying,
    created_by_people_id bigint,
    approved_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    approved_at timestamp with time zone,
    superseded_at timestamp with time zone,
    comments text
);


--
-- Name: ext_agent_factory_prompt_v3_factory_prompt_revision_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_factory_prompt_v3_factory_prompt_revision_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_factory_prompt_v3_factory_prompt_revision_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_factory_prompt_v3_factory_prompt_revision_id_seq OWNED BY public.ext_agent_factory_prompt_v3.factory_prompt_revision_id;


--
-- Name: ext_agent_factory_question; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_factory_question (
    question_id bigint NOT NULL,
    factory_name character varying DEFAULT '''Agent factory prompt'''::character varying NOT NULL,
    factory_version character varying DEFAULT '''1.0'''::character varying NOT NULL,
    step_number integer NOT NULL,
    field_name character varying NOT NULL,
    question_text text NOT NULL,
    question_purpose text,
    expected_answer_type character varying DEFAULT '''text'''::character varying NOT NULL,
    answer_structure jsonb,
    follow_up_rules jsonb,
    validation_rules jsonb,
    knowledge_guidance text,
    mandatory boolean DEFAULT true NOT NULL,
    sequence_mandatory boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_by_people_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    comments text
);


--
-- Name: ext_agent_factory_question_question_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_factory_question_question_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_factory_question_question_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_factory_question_question_id_seq OWNED BY public.ext_agent_factory_question.question_id;


--
-- Name: ext_customer_master_csv; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_customer_master_csv (
    customer_code character varying(100) NOT NULL,
    csv_customer_name text,
    csv_global_name text,
    csv_kam_name text,
    csv_kam_people_id integer,
    csv_regional_mngr text,
    csv_site text,
    csv_site_unit_id integer,
    country text,
    revenue_2024 numeric,
    revenue_2025 numeric,
    loaded_at timestamp with time zone DEFAULT now()
);


--
-- Name: ext_group_merge_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_group_merge_map (
    source_group_id integer NOT NULL,
    target_group_id integer NOT NULL,
    group_key text,
    source_name text,
    target_name text,
    kam_conflict boolean DEFAULT false
);


--
-- Name: ext_kam_name_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_kam_name_map (
    csv_kam_name text NOT NULL,
    people_id integer NOT NULL
);


--
-- Name: ext_site_unit_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_site_unit_map (
    csv_site text NOT NULL,
    unit_id integer
);


--
-- Name: ext_tabledetest; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_tabledetest (
    id bigint NOT NULL,
    machine_name text NOT NULL,
    purchase_date date,
    initial_price numeric,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: ext_tabledetest_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_tabledetest_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_tabledetest_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_tabledetest_id_seq OWNED BY public.ext_tabledetest.id;


--
-- Name: kpi; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi (
    kpi_id integer NOT NULL,
    kpi_name character varying(255) NOT NULL,
    kpi_sub_title character varying(255),
    kpi_code character varying(100),
    kpi_explanation text NOT NULL,
    kpi_formula text,
    target_value numeric,
    target_direction character varying(255),
    target_auto_adjustment character varying(255),
    min_value numeric,
    min_type character varying(255),
    max_value numeric,
    max_type character varying(255),
    low_limit numeric,
    high_limit numeric,
    tolerance_type character varying(255),
    low_tolerance character varying(255),
    up_tolerance character varying(255),
    unit character varying(50) NOT NULL,
    frequency character varying(50),
    nombre_periode character varying(255),
    calculation_mode character varying(255),
    calculation_on character varying(255),
    display_trend character varying(255),
    regression character varying(255),
    reference_kpi_id integer,
    owner_role_id integer,
    status character varying(50) NOT NULL,
    comments text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by_people_id integer,
    kpi_knowledge_base jsonb,
    kpi_knowledge_updated_date timestamp with time zone,
    currency_type character varying(20),
    kfs character varying(255),
    reactivity_status character varying(255),
    importance character varying(255),
    reactivity_need character varying(255),
    pricing_type character varying(255),
    file character varying,
    type character varying(255) DEFAULT 'Manual'::character varying,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: kpi_action_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_action_link (
    link_id bigint NOT NULL,
    action_id integer NOT NULL,
    kpi_id integer NOT NULL,
    kpi_target_allocation_id integer,
    relationship_type character varying(30),
    expected_impact text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_kal_type CHECK (((relationship_type IS NULL) OR ((relationship_type)::text = ANY (ARRAY[('root_cause'::character varying)::text, ('symptom'::character varying)::text, ('prevention'::character varying)::text]))))
);


--
-- Name: TABLE kpi_action_link; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_action_link IS 'Lien action(autre base, réf. logique) <-> KPI. action_id SANS FK (cross-database). V1.';


--
-- Name: COLUMN kpi_action_link.action_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_action_link.action_id IS 'Référence logique vers action.id de ACTION_PLAN_DATABASE_URL. Pas de FK possible.';


--
-- Name: kpi_action_link_link_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_action_link ALTER COLUMN link_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_action_link_link_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_app_assistant_conversation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_assistant_conversation (
    conversation_id integer NOT NULL,
    user_id integer NOT NULL,
    title character varying(200) DEFAULT ''::character varying NOT NULL,
    messages jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kpi_app_assistant_conversation_conversation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_assistant_conversation_conversation_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_assistant_conversation_conversation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_assistant_conversation_conversation_id_seq OWNED BY public.kpi_app_assistant_conversation.conversation_id;


--
-- Name: kpi_app_auth_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_auth_event (
    event_id integer NOT NULL,
    event_type character varying(64) NOT NULL,
    user_id integer,
    actor_user_id integer,
    email character varying(255),
    ip_address character varying(64),
    user_agent character varying(512),
    details jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kpi_app_auth_event_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_auth_event_event_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_auth_event_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_auth_event_event_id_seq OWNED BY public.kpi_app_auth_event.event_id;


--
-- Name: kpi_app_change_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_change_log (
    change_id integer NOT NULL,
    entity_type character varying(30) NOT NULL,
    entity_id integer NOT NULL,
    kpi_id integer,
    action character varying(20) NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    owner_people_id integer,
    owner_name character varying(255),
    actor_user_id integer,
    actor_people_id integer,
    actor_name character varying(255),
    actor_email character varying(255),
    source character varying(30) NOT NULL,
    changes jsonb,
    note text,
    ip_address character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT kpi_app_change_log_action_check CHECK (((action)::text = ANY (ARRAY[('created'::character varying)::text, ('updated'::character varying)::text, ('deleted'::character varying)::text]))),
    CONSTRAINT kpi_app_change_log_entity_type_check CHECK (((entity_type)::text = ANY (ARRAY[('kpi'::character varying)::text, ('target_allocation'::character varying)::text])))
);


--
-- Name: kpi_app_change_log_change_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_change_log_change_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_change_log_change_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_change_log_change_id_seq OWNED BY public.kpi_app_change_log.change_id;


--
-- Name: kpi_app_intake_row; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_intake_row (
    intake_id integer NOT NULL,
    entity_type character varying(30) NOT NULL,
    status character varying(20) NOT NULL,
    raw_values jsonb NOT NULL,
    resolved_values jsonb,
    error_messages jsonb,
    row_version integer DEFAULT 1 NOT NULL,
    submitted_by_people_id integer,
    submitted_by_name character varying(255),
    updated_by_people_id integer,
    updated_by_name character varying(255),
    ip_address character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_by_user_id integer,
    reviewed_by_name character varying(255),
    reviewed_at timestamp with time zone,
    review_comment text,
    created_record_id integer,
    target_record_id integer,
    base_values jsonb,
    CONSTRAINT kpi_app_intake_row_entity_type_check CHECK (((entity_type)::text = ANY (ARRAY[('kpi'::character varying)::text, ('target_allocation'::character varying)::text]))),
    CONSTRAINT kpi_app_intake_row_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('error'::character varying)::text, ('duplicate'::character varying)::text, ('submitted'::character varying)::text, ('rejected'::character varying)::text])))
);


--
-- Name: kpi_app_intake_row_intake_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_intake_row_intake_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_intake_row_intake_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_intake_row_intake_id_seq OWNED BY public.kpi_app_intake_row.intake_id;


--
-- Name: kpi_app_password_token; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_password_token (
    token_id integer NOT NULL,
    user_id integer NOT NULL,
    token_hash character varying(64) NOT NULL,
    purpose character varying(20) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT kpi_app_password_token_purpose_check CHECK (((purpose)::text = ANY (ARRAY[('reset'::character varying)::text, ('invite'::character varying)::text])))
);


--
-- Name: kpi_app_password_token_token_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_password_token_token_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_password_token_token_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_password_token_token_id_seq OWNED BY public.kpi_app_password_token.token_id;


--
-- Name: kpi_app_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_session (
    session_id integer NOT NULL,
    user_id integer NOT NULL,
    token_hash character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    ip_address character varying(64),
    user_agent character varying(512)
);


--
-- Name: kpi_app_session_session_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_session_session_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_session_session_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_session_session_id_seq OWNED BY public.kpi_app_session.session_id;


--
-- Name: kpi_app_setting; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_setting (
    setting_key character varying(64) NOT NULL,
    value jsonb NOT NULL,
    updated_by_user_id integer,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kpi_app_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_app_user (
    user_id integer NOT NULL,
    email character varying(255) NOT NULL,
    full_name character varying(255) DEFAULT ''::character varying NOT NULL,
    people_id integer,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    password_hash text,
    is_active boolean DEFAULT true NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL,
    failed_login_count integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    last_login_at timestamp with time zone,
    password_changed_at timestamp with time zone,
    created_by_user_id integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    microsoft_oid character varying(64),
    microsoft_linked_at timestamp with time zone,
    created_via character varying(20) DEFAULT 'admin'::character varying NOT NULL,
    last_login_method character varying(20),
    CONSTRAINT kpi_app_user_role_check CHECK (((role)::text = ANY (ARRAY[('user'::character varying)::text, ('plant_manager'::character varying)::text, ('zone_manager'::character varying)::text, ('vp'::character varying)::text, ('ceo'::character varying)::text, ('admin'::character varying)::text])))
);


--
-- Name: kpi_app_user_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_app_user_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_app_user_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_app_user_user_id_seq OWNED BY public.kpi_app_user.user_id;


--
-- Name: kpi_assistant_conversation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_assistant_conversation (
    conversation_id bigint NOT NULL,
    people_role_assignment_id bigint,
    kpi_id bigint,
    conversation jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    people_id integer
);


--
-- Name: kpi_assistant_conversation_conversation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_assistant_conversation_conversation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_assistant_conversation_conversation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_assistant_conversation_conversation_id_seq OWNED BY public.kpi_assistant_conversation.conversation_id;


--
-- Name: kpi_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_audit (
    audit_id bigint NOT NULL,
    kpi_id bigint,
    operation character varying(10) NOT NULL,
    changed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    changed_by text DEFAULT CURRENT_USER NOT NULL,
    application_name text,
    client_address inet,
    transaction_id bigint,
    old_data jsonb,
    new_data jsonb
);


--
-- Name: kpi_audit_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_audit_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_audit_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_audit_audit_id_seq OWNED BY public.kpi_audit.audit_id;


--
-- Name: kpi_backup_structure; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_backup_structure (
    kpi_target_allocation_id integer,
    kpi_id integer,
    plant_id integer,
    zone_id integer,
    unit_id integer,
    function character varying(100),
    product_id integer,
    product_line_id integer,
    market_id integer,
    customer_id integer,
    target_value numeric,
    target_unit character varying(50),
    local_currency character varying(50),
    kpi_type character varying(120),
    target_setup_date date,
    target_start_date date,
    target_end_date date,
    last_best_target numeric,
    previous_target_value numeric,
    target_status character varying(50),
    set_by_people_id integer,
    approved_by_people_id integer,
    comments text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    unit_type_id integer,
    role_id integer,
    created_by_people_id integer
);


--
-- Name: kpi_knowledge_refresh_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_knowledge_refresh_queue (
    kpi_id integer NOT NULL,
    reason text,
    requested_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kpi_kpi_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi ALTER COLUMN kpi_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_kpi_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_monitoring_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_monitoring_history (
    history_id bigint NOT NULL,
    kpi_id integer NOT NULL,
    kpi_target_allocation_id integer NOT NULL,
    change_date timestamp with time zone DEFAULT now() NOT NULL,
    old_status character varying(20),
    new_status character varying(20),
    old_escalation_level smallint,
    new_escalation_level smallint,
    change_reason text,
    details jsonb,
    changed_by character varying(100),
    old_priority_score numeric,
    new_priority_score numeric,
    old_priority_level text,
    new_priority_level text,
    old_action_status text,
    new_action_status text
);


--
-- Name: TABLE kpi_monitoring_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_monitoring_history IS 'Historique des transitions de statut/escalade par (kpi, allocation). V1.';


--
-- Name: kpi_monitoring_history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_monitoring_history ALTER COLUMN history_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_monitoring_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_monitoring_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_monitoring_status (
    monitoring_status_id bigint NOT NULL,
    kpi_id integer NOT NULL,
    kpi_target_allocation_id integer NOT NULL,
    owner_role_id bigint,
    calculation_date timestamp with time zone DEFAULT now() NOT NULL,
    current_status character varying(20) DEFAULT 'Green'::character varying NOT NULL,
    latest_value numeric,
    latest_target numeric,
    performance_gap numeric,
    latest_result_date date,
    latest_kpi_result_id integer,
    priority_score numeric(5,2),
    escalation_level smallint DEFAULT 0 NOT NULL,
    analysis_details jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    owner_people_id integer,
    business_status text,
    performance_score numeric,
    performance_trend text,
    priority_level text,
    escalation_target_people_id integer,
    escalation_target_role_id integer,
    escalation_reason text,
    last_escalation_date timestamp with time zone,
    spc_status text,
    spc_signal_count integer DEFAULT 0,
    periods_degraded integer DEFAULT 0,
    periods_without_improvement integer DEFAULT 0,
    recurrence_count integer DEFAULT 0,
    action_status text,
    open_action_count integer DEFAULT 0,
    late_action_count integer DEFAULT 0,
    ineffective_action_count integer DEFAULT 0,
    root_cause_category text,
    root_cause_confidence numeric,
    concentration_type text,
    concentration_value text,
    concentration_score numeric,
    customer_risk numeric,
    delivery_risk numeric,
    quality_risk numeric,
    cash_risk numeric,
    ebitda_risk numeric,
    CONSTRAINT chk_kpi_monitoring_status_esc CHECK ((escalation_level >= 0)),
    CONSTRAINT chk_kpi_monitoring_status_value CHECK (((current_status)::text = ANY (ARRAY['Green'::text, 'Yellow'::text, 'Red'::text, 'Critical'::text, 'Blocking'::text, 'Configuration Error'::text])))
);


--
-- Name: TABLE kpi_monitoring_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_monitoring_status IS 'Cache de l''état courant par (kpi, allocation). Reconstructible depuis kpi_result. V1.';


--
-- Name: kpi_monitoring_status_monitoring_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_monitoring_status ALTER COLUMN monitoring_status_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_monitoring_status_monitoring_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_relationship; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_relationship (
    relationship_id bigint NOT NULL,
    source_kpi_id integer NOT NULL,
    target_kpi_id integer NOT NULL,
    relationship_type character varying(30) NOT NULL,
    expected_lag_periods integer,
    confidence_score numeric(5,2),
    validated boolean DEFAULT false NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_kr_no_self CHECK ((source_kpi_id <> target_kpi_id)),
    CONSTRAINT chk_kr_type CHECK (((relationship_type)::text = ANY (ARRAY[('leading'::character varying)::text, ('driver'::character varying)::text, ('correlation'::character varying)::text])))
);


--
-- Name: TABLE kpi_relationship; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_relationship IS 'Relations de causalité entre KPI (leading/driver/correlation). validated=false par défaut. V1.';


--
-- Name: kpi_relationship_relationship_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_relationship ALTER COLUMN relationship_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_relationship_relationship_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_result; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_result (
    kpi_result_id integer NOT NULL,
    kpi_target_allocation_id integer NOT NULL,
    kpi_id integer NOT NULL,
    result_date date NOT NULL,
    week character varying(20),
    period_type character varying(50),
    period_label character varying(100),
    raw_value numeric,
    lower_limit numeric,
    upper_limit numeric,
    target_value numeric,
    best_new_target numeric(18,2),
    unit character varying(50),
    kpi_type character varying(120),
    result_status character varying(50),
    data_source character varying(100),
    recorded_by_people_id integer,
    recorded_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    comments text
);


--
-- Name: kpi_result_kpi_result_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_result ALTER COLUMN kpi_result_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_result_kpi_result_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_submission_email_dispatch_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_submission_email_dispatch_log (
    dispatch_id bigint NOT NULL,
    people_id integer NOT NULL,
    calculation_mode character varying(40) NOT NULL,
    target_week character varying(40) NOT NULL,
    local_dispatch_date date NOT NULL,
    unit_id integer,
    unit_name character varying(255),
    unit_short_name character varying(100),
    unit_timezone character varying(120) NOT NULL,
    sent_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    frequency_label character varying(50) NOT NULL,
    allocation_ids integer[]
);


--
-- Name: kpi_submission_email_dispatch_log_dispatch_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_submission_email_dispatch_log_dispatch_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_submission_email_dispatch_log_dispatch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_submission_email_dispatch_log_dispatch_id_seq OWNED BY public.kpi_submission_email_dispatch_log.dispatch_id;


--
-- Name: kpi_submission_reminder_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_submission_reminder_log (
    reminder_id bigint NOT NULL,
    people_id integer NOT NULL,
    frequency_label character varying(50) NOT NULL,
    target_week character varying(40) NOT NULL,
    stage character varying(20) NOT NULL,
    local_send_date date NOT NULL,
    unit_timezone character varying(120) NOT NULL,
    to_email character varying(320) NOT NULL,
    cc jsonb DEFAULT '[]'::jsonb NOT NULL,
    missing jsonb DEFAULT '[]'::jsonb NOT NULL,
    missing_count integer NOT NULL,
    expected_count integer NOT NULL,
    sent_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: kpi_submission_reminder_log_reminder_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_submission_reminder_log_reminder_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_submission_reminder_log_reminder_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_submission_reminder_log_reminder_id_seq OWNED BY public.kpi_submission_reminder_log.reminder_id;


--
-- Name: kpi_target_adjustment_proposal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_target_adjustment_proposal (
    proposal_id bigint NOT NULL,
    kpi_target_allocation_id integer NOT NULL,
    kpi_id integer NOT NULL,
    triggered_by_people_id integer,
    approver_people_id integer,
    decision_by_people_id integer,
    previous_target_value numeric NOT NULL,
    proposed_target_value numeric NOT NULL,
    trigger_actual_value numeric NOT NULL,
    improvement_percentage numeric,
    consecutive_periods integer DEFAULT 1 NOT NULL,
    trigger_reason character varying(80) NOT NULL,
    target_direction character varying(16) NOT NULL,
    trigger_period_label character varying(120),
    evidence jsonb DEFAULT '[]'::jsonb NOT NULL,
    approval_token_hash character varying(64) NOT NULL,
    status character varying(40) DEFAULT 'Pending approval'::character varying NOT NULL,
    decision_comment text,
    proposed_low_limit numeric,
    proposed_high_limit numeric,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    decided_at timestamp with time zone
);


--
-- Name: kpi_target_adjustment_proposal_proposal_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_target_adjustment_proposal_proposal_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_target_adjustment_proposal_proposal_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_target_adjustment_proposal_proposal_id_seq OWNED BY public.kpi_target_adjustment_proposal.proposal_id;


--
-- Name: kpi_target_allocation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_target_allocation (
    kpi_target_allocation_id integer NOT NULL,
    kpi_id integer NOT NULL,
    plant_id integer,
    zone_id integer,
    unit_id integer,
    function character varying(100),
    product_id integer,
    product_line_id integer,
    market_id integer,
    customer_id integer,
    target_value numeric,
    target_unit character varying(50),
    local_currency character varying(50),
    kpi_type character varying(120),
    target_setup_date date,
    target_start_date date,
    target_end_date date,
    last_best_target numeric,
    previous_target_value numeric,
    target_status character varying(50),
    set_by_people_id integer,
    approved_by_people_id integer,
    comments text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    unit_type_id integer,
    role_id integer,
    created_by_people_id integer,
    low_limit numeric,
    high_limit numeric,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: kpi_target_allocation_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_target_allocation_audit (
    audit_id bigint NOT NULL,
    kpi_target_allocation_id bigint,
    operation character varying(10) NOT NULL,
    changed_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    changed_by text DEFAULT CURRENT_USER NOT NULL,
    application_name text,
    client_address inet,
    transaction_id bigint,
    old_data jsonb,
    new_data jsonb
);


--
-- Name: kpi_target_allocation_audit_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_target_allocation_audit_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_target_allocation_audit_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_target_allocation_audit_audit_id_seq OWNED BY public.kpi_target_allocation_audit.audit_id;


--
-- Name: kpi_target_allocation_kpi_target_allocation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_target_allocation ALTER COLUMN kpi_target_allocation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_target_allocation_kpi_target_allocation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_test; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_test (
    test_id integer NOT NULL,
    kpi_id integer NOT NULL,
    training_id integer,
    test_name character varying(255) NOT NULL,
    questionnaire_json jsonb NOT NULL,
    pass_score numeric(5,2) DEFAULT 80.00,
    version character varying(20) DEFAULT '1.0'::character varying,
    creation_date date DEFAULT CURRENT_DATE NOT NULL,
    replacement_date date,
    replaced_by_id integer,
    status character varying(50) DEFAULT 'Active'::character varying NOT NULL,
    created_by_people_id integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    comments text,
    CONSTRAINT kpi_test_status_check CHECK (((status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Draft'::character varying)::text, ('Archived'::character varying)::text])))
);


--
-- Name: TABLE kpi_test; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_test IS 'Questionnaire per KPI. questionnaire_json contains questions, options, and correct answers.';


--
-- Name: COLUMN kpi_test.questionnaire_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_test.questionnaire_json IS 'Format: {"questions": [{"id":1,"question":"...","options":["A","B","C","D"],"correct":"A","explanation":"..."}]}';


--
-- Name: COLUMN kpi_test.pass_score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_test.pass_score IS 'Minimum score % to be considered trained. Default 80%.';


--
-- Name: kpi_test_test_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_test ALTER COLUMN test_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_test_test_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_training; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_training (
    training_id integer NOT NULL,
    kpi_id integer NOT NULL,
    training_name character varying(255) NOT NULL,
    training_content jsonb,
    training_html text,
    version character varying(20) DEFAULT '1.0'::character varying,
    creation_date date DEFAULT CURRENT_DATE NOT NULL,
    replacement_date date,
    replaced_by_id integer,
    status character varying(50) DEFAULT 'Active'::character varying NOT NULL,
    created_by_people_id integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    comments text,
    CONSTRAINT kpi_training_status_check CHECK (((status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Draft'::character varying)::text, ('Archived'::character varying)::text])))
);


--
-- Name: TABLE kpi_training; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_training IS 'Training content per KPI. Each KPI can have multiple versions; only one Active at a time.';


--
-- Name: COLUMN kpi_training.training_content; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training.training_content IS 'Structured JSON of the training (topics, scripts, knowledge units).';


--
-- Name: COLUMN kpi_training.training_html; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training.training_html IS 'Raw HTML version of the training, as generated by the prompt.';


--
-- Name: COLUMN kpi_training.replaced_by_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training.replaced_by_id IS 'Points to the newer version that replaced this one.';


--
-- Name: kpi_training_escalation_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_training_escalation_log (
    training_escalation_id bigint NOT NULL,
    result_id bigint NOT NULL,
    people_id bigint,
    escalation_stage text NOT NULL,
    sent_to_email text NOT NULL,
    sent_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kpi_training_escalation_log_training_escalation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.kpi_training_escalation_log_training_escalation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: kpi_training_escalation_log_training_escalation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.kpi_training_escalation_log_training_escalation_id_seq OWNED BY public.kpi_training_escalation_log.training_escalation_id;


--
-- Name: kpi_training_result; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kpi_training_result (
    result_id integer NOT NULL,
    training_id integer NOT NULL,
    test_id integer,
    people_id integer NOT NULL,
    kpi_id integer NOT NULL,
    kpi_target_allocation_id integer,
    training_sent_at timestamp with time zone,
    training_completed_at timestamp with time zone,
    test_sent_at timestamp with time zone,
    test_completed_at timestamp with time zone,
    test_due_date date,
    answers_json jsonb,
    score numeric(5,2),
    passed boolean GENERATED ALWAYS AS ((score >= (80)::numeric)) STORED,
    escalation_level smallint DEFAULT 0 NOT NULL,
    last_reminder_sent_at timestamp with time zone,
    next_reminder_due_at timestamp with time zone,
    status character varying(50) DEFAULT 'training_sent'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    comments text,
    CONSTRAINT kpi_training_result_escalation_level_check CHECK ((escalation_level >= 0)),
    CONSTRAINT kpi_training_result_status_check CHECK (((status)::text = ANY (ARRAY[('training_sent'::character varying)::text, ('training_done'::character varying)::text, ('test_sent'::character varying)::text, ('test_overdue'::character varying)::text, ('completed_pass'::character varying)::text, ('completed_fail'::character varying)::text, ('escalated'::character varying)::text])))
);


--
-- Name: TABLE kpi_training_result; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.kpi_training_result IS 'Tracks the full training + test lifecycle per person per KPI. 
     Workflow: training_sent → training_done → test_sent → (completed_pass | test_overdue → escalated).';


--
-- Name: COLUMN kpi_training_result.kpi_target_allocation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training_result.kpi_target_allocation_id IS 'Optional link to kpi_target_allocation to track KPI performance alongside training.';


--
-- Name: COLUMN kpi_training_result.answers_json; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training_result.answers_json IS 'Person answers. Format: {"answers": [{"question_id":1,"answer":"A"}]}';


--
-- Name: COLUMN kpi_training_result.passed; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training_result.passed IS 'Auto-computed: true if score >= 80. Used for dashboard scoring.';


--
-- Name: COLUMN kpi_training_result.escalation_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.kpi_training_result.escalation_level IS '0 = first reminder to direct manager. Increments +1 each week until test is done.
     Use role.parent_role_id to resolve escalation target.';


--
-- Name: kpi_training_result_result_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_training_result ALTER COLUMN result_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_training_result_result_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_training_training_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.kpi_training ALTER COLUMN training_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.kpi_training_training_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: manufacturing_strategy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manufacturing_strategy (
    manufacturing_strategy_id integer NOT NULL,
    product_id integer NOT NULL,
    zone_id integer NOT NULL,
    plant_id integer NOT NULL,
    strategy_status character varying(50) NOT NULL,
    start_date date,
    end_date date,
    comments text
);


--
-- Name: manufacturing_strategy_manufacturing_strategy_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.manufacturing_strategy ALTER COLUMN manufacturing_strategy_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.manufacturing_strategy_manufacturing_strategy_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: market; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.market (
    market_id integer NOT NULL,
    market_name character varying(255) NOT NULL
);


--
-- Name: market_market_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.market ALTER COLUMN market_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.market_market_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: people; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.people (
    people_id integer NOT NULL,
    name character varying(255) NOT NULL,
    first_name character varying(255) NOT NULL,
    seniority integer,
    email character varying(255),
    cell_phone character varying(50),
    paid_by_unit_id integer NOT NULL,
    work_at_unit_id integer NOT NULL,
    native_language character varying(100),
    other_languages character varying(255),
    other_information text,
    status character varying(50) NOT NULL,
    comments text,
    hr_employee_id character varying(50),
    hr_site character varying(100),
    gender character varying(10),
    hire_date date,
    date_of_birth date,
    nationality character varying(100),
    CONSTRAINT chk_people_gender CHECK (((gender IS NULL) OR ((gender)::text = ANY (ARRAY[('Male'::character varying)::text, ('Female'::character varying)::text])))),
    CONSTRAINT chk_people_status CHECK (((status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Inactive'::character varying)::text])))
);


--
-- Name: people_people_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.people ALTER COLUMN people_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.people_people_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: people_role_assignment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.people_role_assignment (
    assignment_id bigint NOT NULL,
    people_id bigint NOT NULL,
    role_id bigint,
    unit_id bigint,
    zone_id bigint,
    market_id bigint,
    assignment_type character varying(50),
    allocation_percentage numeric(5,2) DEFAULT 100,
    is_primary boolean DEFAULT true,
    functional_manager_role_id bigint,
    assistant_context text,
    knowledge_context text,
    start_date date DEFAULT CURRENT_DATE,
    end_date date,
    assignment_status character varying(50) DEFAULT 'ACTIVE'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    product_line_id integer,
    reports_to integer,
    target_role_label character varying(80),
    functional_manager_assignment_id bigint,
    admin_manager_assignment_id integer,
    CONSTRAINT chk_pra_assignment_status CHECK (((assignment_status)::text = ANY (ARRAY[('ACTIVE'::character varying)::text, ('INACTIVE'::character varying)::text, ('ENDED'::character varying)::text])))
);


--
-- Name: people_role_assignment_assignment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.people_role_assignment_assignment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: people_role_assignment_assignment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.people_role_assignment_assignment_id_seq OWNED BY public.people_role_assignment.assignment_id;


--
-- Name: product; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product (
    product_id integer NOT NULL,
    product_line_id integer NOT NULL,
    product_name character varying(255) NOT NULL,
    product_code character varying(100),
    product_description text,
    product_knowledge_file text,
    status character varying(50) NOT NULL,
    operating_environment text,
    technical_parameters text,
    machines_and_tooling text,
    manufacturing_strategy text,
    purchasing_strategy text,
    prototypes_ppap_and_sop text,
    engineering_and_testing text,
    capacity text,
    our_advantages text,
    gmdc_pct numeric(5,2),
    product_pictures text,
    customers_in_production text,
    customer_in_development text,
    level_of_interest_and_why text,
    estimated_price_per_product text,
    prod_if_customer_in_china boolean,
    costing_data text,
    comments text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by integer,
    updated_by integer
);


--
-- Name: product_line; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.product_line (
    product_line_id integer NOT NULL,
    product_line_name character varying(255) NOT NULL,
    product_line_code character varying(100),
    scope text NOT NULL,
    product_definition text NOT NULL,
    manager_role_id integer,
    status character varying(50) NOT NULL,
    type_of_products text,
    manufacturing_locations text,
    design_center text,
    history text,
    type_of_customers text,
    metiers text,
    strength text,
    weakness text,
    perspectives text,
    compliance_resource_id text,
    attachments_raw text,
    comments text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by integer,
    updated_by integer
);


--
-- Name: product_line_product_line_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.product_line ALTER COLUMN product_line_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.product_line_product_line_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: product_product_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.product ALTER COLUMN product_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.product_product_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: role; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role (
    role_id bigint NOT NULL,
    role_name character varying(255) NOT NULL,
    normalized_role_name character varying(255),
    parent_role_id bigint,
    role_family character varying(100),
    role_level character varying(100),
    role_definition text,
    main_responsibilities text,
    expected_knowledge text,
    assistant_context text,
    decision_level character varying(100),
    is_managerial boolean DEFAULT false,
    is_active boolean DEFAULT true,
    valid_from date DEFAULT CURRENT_DATE,
    valid_to date,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    functional_report_role_id bigint,
    expectation text,
    function_definition text,
    assistant_name character varying(255),
    knowledge_files text,
    attached_file_reference text,
    role_slug character varying(150),
    grade character varying(2),
    ceo_level character varying(50),
    scope character varying(10),
    CONSTRAINT chk_role_ceo_level CHECK (((ceo_level IS NULL) OR ((ceo_level)::text = ANY (ARRAY[('VP'::character varying)::text, ('Director'::character varying)::text, ('Senior Manager'::character varying)::text, ('Manager / Supervisor'::character varying)::text, ('Specialist / Engineer'::character varying)::text, ('Technician / Admin.'::character varying)::text, ('Operator / Assistant'::character varying)::text])))),
    CONSTRAINT chk_role_grade CHECK (((grade IS NULL) OR ((grade)::text = ANY (ARRAY[('G1'::character varying)::text, ('G2'::character varying)::text, ('G3'::character varying)::text, ('G4'::character varying)::text, ('G5'::character varying)::text, ('G6'::character varying)::text, ('G7'::character varying)::text])))),
    CONSTRAINT chk_role_level CHECK (((role_level)::text = ANY (ARRAY[('Executive'::character varying)::text, ('VP'::character varying)::text, ('Director'::character varying)::text, ('Manager'::character varying)::text, ('Supervisor'::character varying)::text, ('Lead'::character varying)::text, ('Senior'::character varying)::text, ('Professional'::character varying)::text, ('Operational'::character varying)::text, ('Coordinator'::character varying)::text, ('Assistant'::character varying)::text]))),
    CONSTRAINT chk_role_scope CHECK (((scope IS NULL) OR ((scope)::text = ANY (ARRAY[('Groupe'::character varying)::text, ('Usine'::character varying)::text]))))
);


--
-- Name: role_reporting_relationship; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_reporting_relationship (
    relationship_id bigint NOT NULL,
    source_role_id bigint NOT NULL,
    target_role_id bigint NOT NULL,
    relationship_type character varying(30) NOT NULL,
    source_unit_id bigint,
    target_unit_id bigint,
    zone_id bigint,
    market_id bigint,
    product_line_id integer,
    priority integer DEFAULT 100 NOT NULL,
    valid_from date DEFAULT CURRENT_DATE NOT NULL,
    valid_to date,
    status character varying(20) DEFAULT 'DRAFT'::character varying NOT NULL,
    comments text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_reporting_dates CHECK (((valid_to IS NULL) OR (valid_to >= valid_from))),
    CONSTRAINT chk_reporting_distinct_roles CHECK ((source_role_id <> target_role_id)),
    CONSTRAINT chk_reporting_status CHECK (((status)::text = ANY (ARRAY[('DRAFT'::character varying)::text, ('VALIDATED'::character varying)::text, ('ACTIVE'::character varying)::text, ('INACTIVE'::character varying)::text]))),
    CONSTRAINT chk_reporting_type CHECK (((relationship_type)::text = ANY (ARRAY[('HIERARCHICAL'::character varying)::text, ('FUNCTIONAL'::character varying)::text, ('OPERATIONAL'::character varying)::text, ('MATRIX'::character varying)::text])))
);


--
-- Name: role_reporting_relationship_relationship_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.role_reporting_relationship_relationship_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: role_reporting_relationship_relationship_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.role_reporting_relationship_relationship_id_seq OWNED BY public.role_reporting_relationship.relationship_id;


--
-- Name: role_role_id_seq1; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.role_role_id_seq1
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: role_role_id_seq1; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.role_role_id_seq1 OWNED BY public.role.role_id;


--
-- Name: status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.status (
    status_id integer NOT NULL,
    status_name character varying(255) NOT NULL
);


--
-- Name: status_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.status ALTER COLUMN status_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.status_status_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: subject; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject (
    subject_id integer NOT NULL,
    parent_subject_id integer,
    subject_name character varying(255) NOT NULL,
    subject_explanation text NOT NULL,
    subject_example text,
    knowledge text,
    status character varying(50) NOT NULL,
    comments text
);


--
-- Name: subject_kpi; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.subject_kpi (
    subject_kpi_id integer NOT NULL,
    subject_id integer NOT NULL,
    kpi_id integer NOT NULL,
    importance_level character varying(50),
    is_primary boolean NOT NULL,
    comments text
);


--
-- Name: subject_kpi_subject_kpi_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.subject_kpi ALTER COLUMN subject_kpi_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.subject_kpi_subject_kpi_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: subject_subject_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.subject ALTER COLUMN subject_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.subject_subject_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: unit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unit (
    unit_id integer NOT NULL,
    parent_unit_id integer,
    unit_name character varying(255) NOT NULL,
    unit_short_name character varying(100),
    address text,
    city character varying(100),
    country character varying(100),
    spoken_language character varying(255),
    selling_currency character varying(10),
    operating_currency character varying(10),
    manager_id integer,
    vat_number character varying(100),
    company_number character varying(100),
    created_in integer,
    comments text,
    status_id integer DEFAULT 1 NOT NULL,
    unit_type_id integer NOT NULL,
    zone_id integer,
    direct_labor_cost_per_hour numeric(18,4),
    base_variable_overhead_cost_per_hour numeric(18,4),
    number_of_shifts numeric(5,2),
    hours_per_shift numeric(5,2),
    foh_dc_percent numeric(7,4),
    fee_dc_percent numeric(7,4),
    company_tax_percent numeric(7,4),
    y_minus_1_sales numeric(18,2),
    shift_1_premium_percent numeric(7,4),
    shift_2_premium_percent numeric(7,4),
    shift_3_premium_percent numeric(7,4),
    fully_loaded_hourly_cost numeric(18,4),
    factory_cost_comment text,
    factory_cost_updated_at timestamp without time zone,
    open_hours_per_year numeric(10,2),
    CONSTRAINT chk_no_self_parent CHECK ((unit_id <> parent_unit_id))
);


--
-- Name: unit_factory_cost_change_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unit_factory_cost_change_log (
    change_id bigint NOT NULL,
    unit_id integer NOT NULL,
    unit_name character varying(255),
    column_name text NOT NULL,
    old_value text,
    new_value text,
    old_row_data jsonb,
    new_row_data jsonb,
    changed_at timestamp without time zone DEFAULT now() NOT NULL,
    changed_by text DEFAULT CURRENT_USER NOT NULL,
    change_comment text
);


--
-- Name: unit_factory_cost_change_log_change_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.unit_factory_cost_change_log_change_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: unit_factory_cost_change_log_change_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.unit_factory_cost_change_log_change_id_seq OWNED BY public.unit_factory_cost_change_log.change_id;


--
-- Name: unit_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unit_type (
    unit_type_id integer NOT NULL,
    unit_type_name text NOT NULL
);


--
-- Name: unit_type_unit_type_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.unit_type_unit_type_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: unit_type_unit_type_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.unit_type_unit_type_id_seq OWNED BY public.unit_type.unit_type_id;


--
-- Name: unit_unit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.unit ALTER COLUMN unit_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.unit_unit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_auth; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_auth (
    auth_id integer NOT NULL,
    people_id integer NOT NULL,
    email character varying(255) NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    must_change_password boolean DEFAULT true,
    last_login timestamp without time zone
);


--
-- Name: user_auth_auth_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_auth_auth_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_auth_auth_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_auth_auth_id_seq OWNED BY public.user_auth.auth_id;


--
-- Name: v_assignment_reporting_candidates; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_assignment_reporting_candidates AS
 WITH applicable_relations AS (
         SELECT source_assignment.assignment_id AS source_assignment_id,
            source_assignment.people_id AS source_people_id,
            source_assignment.role_id AS source_role_id,
            source_assignment.unit_id AS source_unit_id,
            source_assignment.zone_id AS source_zone_id,
            source_assignment.market_id AS source_market_id,
            source_assignment.product_line_id AS source_product_line_id,
            rel.relationship_id,
            rel.relationship_type,
            rel.target_role_id,
            rel.target_unit_id,
            rel.priority,
            target_role.scope AS target_role_scope,
                CASE
                    WHEN ((rel.product_line_id IS NOT NULL) AND (rel.product_line_id = source_assignment.product_line_id)) THEN 1
                    WHEN ((rel.source_unit_id IS NOT NULL) AND (rel.source_unit_id = source_assignment.unit_id)) THEN 2
                    WHEN ((rel.zone_id IS NOT NULL) AND (rel.zone_id = source_assignment.zone_id)) THEN 3
                    WHEN ((rel.market_id IS NOT NULL) AND (rel.market_id = source_assignment.market_id)) THEN 4
                    WHEN ((rel.source_unit_id IS NULL) AND (rel.zone_id IS NULL) AND (rel.market_id IS NULL) AND (rel.product_line_id IS NULL)) THEN 5
                    ELSE 99
                END AS scope_rank
           FROM ((public.people_role_assignment source_assignment
             JOIN public.role_reporting_relationship rel ON (((rel.source_role_id = source_assignment.role_id) AND ((rel.status)::text = 'ACTIVE'::text) AND (rel.valid_from <= CURRENT_DATE) AND ((rel.valid_to IS NULL) OR (rel.valid_to >= CURRENT_DATE)))))
             JOIN public.role target_role ON (((target_role.role_id = rel.target_role_id) AND (target_role.is_active = true))))
          WHERE ((upper((source_assignment.assignment_status)::text) = 'ACTIVE'::text) AND ((rel.source_unit_id IS NULL) OR (rel.source_unit_id = source_assignment.unit_id)) AND ((rel.zone_id IS NULL) OR (rel.zone_id = source_assignment.zone_id)) AND ((rel.market_id IS NULL) OR (rel.market_id = source_assignment.market_id)) AND ((rel.product_line_id IS NULL) OR (rel.product_line_id = source_assignment.product_line_id)))
        ), manager_candidates AS (
         SELECT applicable.source_assignment_id,
            applicable.source_people_id,
            applicable.source_role_id,
            applicable.source_unit_id,
            applicable.source_zone_id,
            applicable.source_market_id,
            applicable.source_product_line_id,
            applicable.relationship_id,
            applicable.relationship_type,
            applicable.target_role_id,
            applicable.target_unit_id,
            applicable.priority,
            applicable.target_role_scope,
            applicable.scope_rank,
            manager_assignment.assignment_id AS manager_assignment_id,
            manager_assignment.people_id AS manager_people_id,
            manager_assignment.role_id AS manager_role_id,
            manager_assignment.unit_id AS manager_unit_id,
            manager_assignment.is_primary AS manager_is_primary,
            row_number() OVER (PARTITION BY applicable.source_assignment_id, applicable.relationship_type ORDER BY applicable.scope_rank, applicable.priority, manager_assignment.is_primary DESC, manager_assignment.assignment_id) AS candidate_rank,
            count(*) OVER (PARTITION BY applicable.source_assignment_id, applicable.relationship_type, applicable.scope_rank, applicable.priority) AS candidate_count
           FROM (applicable_relations applicable
             JOIN public.people_role_assignment manager_assignment ON (((manager_assignment.role_id = applicable.target_role_id) AND (upper((manager_assignment.assignment_status)::text) = 'ACTIVE'::text) AND (((applicable.target_unit_id IS NOT NULL) AND (manager_assignment.unit_id = applicable.target_unit_id)) OR ((applicable.target_unit_id IS NULL) AND ((applicable.target_role_scope)::text = 'Usine'::text) AND (manager_assignment.unit_id = applicable.source_unit_id)) OR ((applicable.target_unit_id IS NULL) AND ((applicable.target_role_scope)::text = 'Groupe'::text) AND (manager_assignment.unit_id = 1))) AND ((applicable.source_zone_id IS NULL) OR (manager_assignment.zone_id IS NULL) OR (manager_assignment.zone_id = applicable.source_zone_id)) AND ((applicable.source_market_id IS NULL) OR (manager_assignment.market_id IS NULL) OR (manager_assignment.market_id = applicable.source_market_id)) AND ((applicable.source_product_line_id IS NULL) OR (manager_assignment.product_line_id IS NULL) OR (manager_assignment.product_line_id = applicable.source_product_line_id)))))
        )
 SELECT source_assignment_id,
    source_people_id,
    source_role_id,
    source_unit_id,
    source_zone_id,
    source_market_id,
    source_product_line_id,
    relationship_id,
    relationship_type,
    target_role_id,
    target_unit_id,
    priority,
    target_role_scope,
    scope_rank,
    manager_assignment_id,
    manager_people_id,
    manager_role_id,
    manager_unit_id,
    manager_is_primary,
    candidate_rank,
    candidate_count
   FROM manager_candidates;


--
-- Name: v_customer_group_allocation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_customer_group_allocation AS
 SELECT g.customer_id AS group_id,
    g.customer_name AS group_name,
    g.customer_code AS group_code,
    kam.people_id AS kam_people_id,
    NULLIF(TRIM(BOTH FROM (((kam.first_name)::text || ' '::text) || (kam.name)::text)), ''::text) AS kam_name,
    kam.email AS kam_email,
    g.strategic_level,
    g.country,
    count(DISTINCT e.customer_id) AS entity_count,
    count(DISTINCT cdu.unit_id) AS plant_count,
    array_remove(array_agg(DISTINCT u.unit_short_name), NULL::character varying) AS plants
   FROM ((((public.customer g
     LEFT JOIN public.customer e ON (((e.parent_customer_id = g.customer_id) AND (COALESCE(e.is_deleted, false) = false) AND ((e.customer_status)::text <> 'Archived'::text))))
     LEFT JOIN public.people kam ON ((kam.people_id = g.main_kam_people_id)))
     LEFT JOIN public.customer_delivery_unit cdu ON ((cdu.customer_id = e.customer_id)))
     LEFT JOIN public.unit u ON ((u.unit_id = cdu.unit_id)))
  WHERE (((g.customer_type)::text = 'Customer Group'::text) AND (COALESCE(g.is_deleted, false) = false) AND ((g.customer_status)::text <> 'Archived'::text))
  GROUP BY g.customer_id, g.customer_name, g.customer_code, kam.people_id, kam.first_name, kam.name, kam.email, g.strategic_level, g.country;


--
-- Name: v_customer_tree; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_customer_tree AS
 WITH RECURSIVE cte AS (
         SELECT c.customer_id,
            c.parent_customer_id,
            c.customer_name,
            c.customer_code,
            c.customer_type,
            c.customer_status,
            c.strategic_level,
            c.country,
            c.zone_id,
            c.market_id,
            0 AS depth,
            (c.customer_name)::text AS full_path,
            ARRAY[c.customer_id] AS id_path
           FROM public.customer c
          WHERE ((c.parent_customer_id IS NULL) AND (COALESCE(c.is_deleted, false) = false) AND ((c.customer_status)::text <> 'Archived'::text))
        UNION ALL
         SELECT c.customer_id,
            c.parent_customer_id,
            c.customer_name,
            c.customer_code,
            c.customer_type,
            c.customer_status,
            c.strategic_level,
            c.country,
            c.zone_id,
            c.market_id,
            (cte_1.depth + 1),
            ((cte_1.full_path || ' > '::text) || (c.customer_name)::text),
            (cte_1.id_path || c.customer_id)
           FROM (public.customer c
             JOIN cte cte_1 ON ((c.parent_customer_id = cte_1.customer_id)))
          WHERE ((COALESCE(c.is_deleted, false) = false) AND ((c.customer_status)::text <> 'Archived'::text) AND (NOT (c.customer_id = ANY (cte_1.id_path))))
        )
 SELECT customer_id,
    parent_customer_id,
    customer_name,
    customer_code,
    customer_type,
    customer_status,
    strategic_level,
    country,
    zone_id,
    market_id,
    depth,
    full_path,
    id_path,
    (NOT (EXISTS ( SELECT 1
           FROM public.customer ch
          WHERE ((ch.parent_customer_id = cte.customer_id) AND (COALESCE(ch.is_deleted, false) = false) AND ((ch.customer_status)::text <> 'Archived'::text))))) AS is_leaf
   FROM cte;


--
-- Name: VIEW v_customer_tree; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_customer_tree IS 'Hiérarchie clients : Groupe → Entité légale → Site de production.
is_leaf=TRUE : sites finaux (purchasing centers, design centers…).
depth=0 : groupes racines (Valeo, Stellantis, Nidec…).';


--
-- Name: v_customer_group_kam_resolved; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_customer_group_kam_resolved AS
 WITH RECURSIVE customer_ancestors AS (
         SELECT c.customer_id,
            c.parent_customer_id,
            c.customer_id AS ancestor_customer_id,
            c.customer_name AS ancestor_customer_name,
            c.customer_code AS ancestor_customer_code,
            c.customer_type AS ancestor_customer_type,
            c.main_kam_people_id AS ancestor_kam_people_id,
            0 AS inheritance_distance,
            ARRAY[c.customer_id] AS visited_customer_ids
           FROM public.customer c
          WHERE ((COALESCE(c.is_deleted, false) = false) AND ((c.customer_status)::text <> 'Archived'::text))
        UNION ALL
         SELECT ca.customer_id,
            parent.parent_customer_id,
            parent.customer_id AS ancestor_customer_id,
            parent.customer_name AS ancestor_customer_name,
            parent.customer_code AS ancestor_customer_code,
            parent.customer_type AS ancestor_customer_type,
            parent.main_kam_people_id AS ancestor_kam_people_id,
            (ca.inheritance_distance + 1),
            (ca.visited_customer_ids || parent.customer_id)
           FROM (customer_ancestors ca
             JOIN public.customer parent ON ((parent.customer_id = ca.parent_customer_id)))
          WHERE ((COALESCE(parent.is_deleted, false) = false) AND ((parent.customer_status)::text <> 'Archived'::text) AND (NOT (parent.customer_id = ANY (ca.visited_customer_ids))))
        ), resolved_customer AS (
         SELECT c.customer_id,
            c.parent_customer_id,
            c.customer_name,
            c.customer_code,
            c.customer_type,
            c.customer_status,
            c.strategic_level,
            c.country,
            c.city,
            c.zone_id,
            c.market_id,
            tree.depth,
            tree.full_path,
            tree.is_leaf,
            group_match.ancestor_customer_id AS customer_group_id,
            group_match.ancestor_customer_name AS customer_group_name,
            group_match.ancestor_customer_code AS customer_group_code,
            group_match.inheritance_distance AS customer_group_distance,
            kam_match.ancestor_kam_people_id AS effective_kam_people_id,
            kam_match.ancestor_customer_id AS kam_source_customer_id,
            kam_match.ancestor_customer_name AS kam_source_customer_name,
            kam_match.ancestor_customer_type AS kam_source_customer_type,
            kam_match.inheritance_distance AS kam_inheritance_distance
           FROM (((public.customer c
             LEFT JOIN public.v_customer_tree tree ON ((tree.customer_id = c.customer_id)))
             LEFT JOIN LATERAL ( SELECT ca.ancestor_customer_id,
                    ca.ancestor_customer_name,
                    ca.ancestor_customer_code,
                    ca.inheritance_distance
                   FROM customer_ancestors ca
                  WHERE ((ca.customer_id = c.customer_id) AND ((ca.ancestor_customer_type)::text = 'Customer Group'::text))
                  ORDER BY ca.inheritance_distance
                 LIMIT 1) group_match ON (true))
             LEFT JOIN LATERAL ( SELECT ca.ancestor_kam_people_id,
                    ca.ancestor_customer_id,
                    ca.ancestor_customer_name,
                    ca.ancestor_customer_type,
                    ca.inheritance_distance
                   FROM customer_ancestors ca
                  WHERE ((ca.customer_id = c.customer_id) AND (ca.ancestor_kam_people_id IS NOT NULL))
                  ORDER BY ca.inheritance_distance
                 LIMIT 1) kam_match ON (true))
          WHERE ((COALESCE(c.is_deleted, false) = false) AND ((c.customer_status)::text <> 'Archived'::text))
        )
 SELECT rc.customer_id,
    rc.parent_customer_id,
    rc.customer_name,
    rc.customer_code,
    rc.customer_type,
    rc.customer_status,
    rc.strategic_level,
    rc.country,
    rc.city,
    rc.zone_id,
    rc.market_id,
    rc.depth,
    rc.full_path,
    rc.is_leaf,
    rc.customer_group_id,
    rc.customer_group_name,
    rc.customer_group_code,
    rc.customer_group_distance,
    rc.effective_kam_people_id,
    NULLIF(TRIM(BOTH FROM concat_ws(' '::text, kam.first_name, kam.name)), ''::text) AS effective_kam_name,
    kam.email AS effective_kam_email,
    kam.status AS effective_kam_status,
    rc.kam_source_customer_id,
    rc.kam_source_customer_name,
    rc.kam_source_customer_type,
    rc.kam_inheritance_distance,
        CASE
            WHEN (rc.effective_kam_people_id IS NULL) THEN 'UNASSIGNED'::text
            WHEN (rc.kam_inheritance_distance = 0) THEN 'DIRECT'::text
            ELSE 'INHERITED'::text
        END AS kam_assignment_source,
        CASE
            WHEN (rc.customer_group_id IS NULL) THEN 'MISSING_CUSTOMER_GROUP'::text
            WHEN (rc.effective_kam_people_id IS NULL) THEN 'MISSING_KAM'::text
            WHEN (lower((COALESCE(kam.status, ''::character varying))::text) <> 'active'::text) THEN 'INACTIVE_KAM'::text
            ELSE NULL::text
        END AS data_quality_warning
   FROM (resolved_customer rc
     LEFT JOIN public.people kam ON ((kam.people_id = rc.effective_kam_people_id)));


--
-- Name: v_customer_kam_plant_revenue; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_customer_kam_plant_revenue AS
 SELECT COALESCE(g.customer_id, e.customer_id) AS group_id,
    COALESCE(g.customer_name, (m.csv_global_name)::character varying) AS group_name,
    kam.people_id AS kam_people_id,
    NULLIF(TRIM(BOTH FROM (((kam.first_name)::text || ' '::text) || (kam.name)::text)), ''::text) AS kam_name,
    m.csv_site AS plant,
    m.csv_site_unit_id AS plant_unit_id,
    sum(m.revenue_2024) AS revenue_2024,
    sum(m.revenue_2025) AS revenue_2025,
    count(*) AS entity_count
   FROM (((public.ext_customer_master_csv m
     JOIN public.customer e ON (((e.customer_code)::text = (m.customer_code)::text)))
     LEFT JOIN public.customer g ON ((g.customer_id = e.parent_customer_id)))
     LEFT JOIN public.people kam ON ((kam.people_id = COALESCE(g.main_kam_people_id, e.main_kam_people_id))))
  GROUP BY COALESCE(g.customer_id, e.customer_id), COALESCE(g.customer_name, (m.csv_global_name)::character varying), kam.people_id, kam.first_name, kam.name, m.csv_site, m.csv_site_unit_id;


--
-- Name: v_customer_orphans; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_customer_orphans AS
 SELECT e.customer_id,
    e.customer_name,
    e.customer_code,
    e.main_kam_people_id,
    m.csv_global_name AS suggested_group_name
   FROM (public.customer e
     LEFT JOIN public.ext_customer_master_csv m ON (((m.customer_code)::text = (e.customer_code)::text)))
  WHERE (((e.customer_type)::text = 'Customer Legal Entity'::text) AND (e.parent_customer_id IS NULL) AND (COALESCE(e.is_deleted, false) = false) AND ((e.customer_status)::text <> 'Archived'::text));


--
-- Name: v_role_tree; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_role_tree AS
 WITH RECURSIVE cte AS (
         SELECT r.role_id,
            r.parent_role_id,
            r.role_name,
            r.normalized_role_name,
            r.role_slug,
            r.role_level,
            r.is_managerial,
            r.is_active,
            r.role_family,
            0 AS depth,
            (r.role_name)::text AS full_path,
            ARRAY[r.role_id] AS id_path,
            r.role_id AS root_role_id,
            (r.role_name)::text AS root_role_name
           FROM public.role r
          WHERE ((r.parent_role_id IS NULL) AND (r.is_active = true))
        UNION ALL
         SELECT r.role_id,
            r.parent_role_id,
            r.role_name,
            r.normalized_role_name,
            r.role_slug,
            r.role_level,
            r.is_managerial,
            r.is_active,
            r.role_family,
            (cte_1.depth + 1),
            ((cte_1.full_path || ' > '::text) || (r.role_name)::text),
            (cte_1.id_path || r.role_id),
            cte_1.root_role_id,
            cte_1.root_role_name
           FROM (public.role r
             JOIN cte cte_1 ON ((r.parent_role_id = cte_1.role_id)))
          WHERE ((r.is_active = true) AND (NOT (r.role_id = ANY (cte_1.id_path))))
        )
 SELECT role_id,
    parent_role_id,
    role_name,
    normalized_role_name,
    role_slug,
    role_level,
    is_managerial,
    is_active,
    role_family,
    depth,
    full_path,
    id_path,
    root_role_id,
    root_role_name,
    (NOT (EXISTS ( SELECT 1
           FROM public.role child
          WHERE ((child.parent_role_id = cte.role_id) AND (child.is_active = true))))) AS is_leaf
   FROM cte;


--
-- Name: VIEW v_role_tree; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_role_tree IS 'Hiérarchie des rôles (récursive).
depth=0 : CEO, depth=1 : VPs, depth=2 : Plant/Zone managers, depth=3 : dept managers, depth=4+ : opérationnels.
is_leaf=TRUE : rôles sans sous-rôles (Operator, Technician…).';


--
-- Name: v_unit_tree; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_unit_tree AS
 WITH RECURSIVE cte AS (
         SELECT u.unit_id,
            u.parent_unit_id,
            u.unit_name,
            u.unit_short_name,
            u.country,
            u.spoken_language,
            u.selling_currency,
            u.operating_currency,
            u.unit_type_id,
            ut.unit_type_name,
            u.status_id,
            u.manager_id,
            0 AS depth,
            (u.unit_name)::text AS full_path,
            ARRAY[u.unit_id] AS id_path,
            u.unit_id AS root_id,
            (u.unit_name)::text AS root_name,
            NULL::integer AS factory_id,
            NULL::character varying AS factory_name,
            NULL::integer AS dept_id,
            NULL::character varying AS dept_name
           FROM (public.unit u
             JOIN public.unit_type ut ON ((ut.unit_type_id = u.unit_type_id)))
          WHERE (u.parent_unit_id IS NULL)
        UNION ALL
         SELECT u.unit_id,
            u.parent_unit_id,
            u.unit_name,
            u.unit_short_name,
            u.country,
            u.spoken_language,
            u.selling_currency,
            u.operating_currency,
            u.unit_type_id,
            ut.unit_type_name,
            u.status_id,
            u.manager_id,
            (cte_1.depth + 1),
            ((cte_1.full_path || ' > '::text) || (u.unit_name)::text),
            (cte_1.id_path || u.unit_id),
            cte_1.root_id,
            cte_1.root_name,
                CASE
                    WHEN (u.unit_type_id = 4) THEN u.unit_id
                    ELSE cte_1.factory_id
                END AS factory_id,
                CASE
                    WHEN (u.unit_type_id = 4) THEN u.unit_name
                    ELSE cte_1.factory_name
                END AS factory_name,
                CASE
                    WHEN (u.unit_type_id = 5) THEN u.unit_id
                    ELSE cte_1.dept_id
                END AS dept_id,
                CASE
                    WHEN (u.unit_type_id = 5) THEN u.unit_name
                    ELSE cte_1.dept_name
                END AS dept_name
           FROM ((public.unit u
             JOIN public.unit_type ut ON ((ut.unit_type_id = u.unit_type_id)))
             JOIN cte cte_1 ON ((u.parent_unit_id = cte_1.unit_id)))
          WHERE (NOT (u.unit_id = ANY (cte_1.id_path)))
        )
 SELECT unit_id,
    parent_unit_id,
    unit_name,
    unit_short_name,
    country,
    spoken_language,
    selling_currency,
    operating_currency,
    unit_type_id,
    unit_type_name,
    status_id,
    manager_id,
    depth,
    full_path,
    id_path,
    root_id,
    root_name,
    factory_id,
    factory_name,
    dept_id,
    dept_name,
    (NOT (EXISTS ( SELECT 1
           FROM public.unit child
          WHERE (child.parent_unit_id = cte.unit_id)))) AS is_leaf
   FROM cte;


--
-- Name: VIEW v_unit_tree; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_unit_tree IS 'Arbre organisationnel complet (récursif).
Colonnes clés : full_path (breadcrumb), depth (0=groupe, 1=pays, 2=factory, 3=dept),
factory_name (factory parente), dept_name (département parent), is_leaf.
Utilisation Power BI : connecter comme table de dimension.';


--
-- Name: zone; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.zone (
    zone_id integer NOT NULL,
    zone_name character varying(255) NOT NULL
);


--
-- Name: v_people_assignments; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_people_assignments AS
 SELECT p.people_id,
    p.name AS last_name,
    p.first_name,
    p.email,
    p.status AS people_status,
    p.hr_employee_id,
    p.hr_site,
    wu.unit_id AS work_unit_id,
    wu.unit_name AS work_unit_name,
    wu_type.unit_type_name AS work_unit_type,
    wu.country AS work_country,
    pra.assignment_id,
    pra.assignment_status,
    pra.is_primary,
    pra.start_date,
    pra.end_date,
    pra.assignment_type,
    r.role_id,
    r.role_name,
    r.role_level,
    r.is_managerial,
    vr.full_path AS role_full_path,
    vr.depth AS role_depth,
    ru.unit_id AS role_unit_id,
    ru.unit_name AS role_unit_name,
    vut.full_path AS role_unit_full_path,
    vut.factory_name,
    vut.dept_name,
    z.zone_id,
    z.zone_name,
    m.market_id,
    m.market_name
   FROM (((((((((public.people p
     LEFT JOIN public.unit wu ON ((wu.unit_id = p.work_at_unit_id)))
     LEFT JOIN public.unit_type wu_type ON ((wu_type.unit_type_id = wu.unit_type_id)))
     LEFT JOIN public.people_role_assignment pra ON ((pra.people_id = p.people_id)))
     LEFT JOIN public.role r ON ((r.role_id = pra.role_id)))
     LEFT JOIN public.v_role_tree vr ON ((vr.role_id = pra.role_id)))
     LEFT JOIN public.unit ru ON ((ru.unit_id = pra.unit_id)))
     LEFT JOIN public.v_unit_tree vut ON ((vut.unit_id = pra.unit_id)))
     LEFT JOIN public.zone z ON ((z.zone_id = pra.zone_id)))
     LEFT JOIN public.market m ON ((m.market_id = pra.market_id)))
  WHERE (((pra.assignment_status)::text = 'ACTIVE'::text) OR (pra.assignment_id IS NULL));


--
-- Name: VIEW v_people_assignments; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_people_assignments IS 'Personnes avec leurs affectations de rôles actives.
Une personne avec plusieurs rôles = plusieurs lignes.
Colonnes clés : factory_name, dept_name, role_full_path, zone_name.
Filtrer sur assignment_status=ACTIVE pour les affectations courantes.';


--
-- Name: v_sales_customer_directory; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_sales_customer_directory AS
 SELECT c.customer_id,
    c.customer_code,
    c.customer_name,
    c.customer_type,
    c.customer_status,
    c.strategic_level,
    c.country,
    c.city,
    t.depth,
    t.full_path,
    t.is_leaf,
    p.customer_id AS parent_id,
    p.customer_name AS parent_group_name,
    p.customer_code AS parent_group_code,
    COALESCE(kam.people_id, gkam.people_id) AS kam_people_id,
    NULLIF(TRIM(BOTH FROM (((COALESCE(kam.first_name, gkam.first_name))::text || ' '::text) || (COALESCE(kam.name, gkam.name))::text)), ''::text) AS kam_name,
    COALESCE(kam.email, gkam.email) AS kam_email,
    ((kam.people_id IS NULL) AND (gkam.people_id IS NOT NULL)) AS kam_inherited_from_group,
    COALESCE(sal.people_id, gsal.people_id) AS sales_people_id,
    NULLIF(TRIM(BOTH FROM (((COALESCE(sal.first_name, gsal.first_name))::text || ' '::text) || (COALESCE(sal.name, gsal.name))::text)), ''::text) AS sales_name,
    COALESCE(sal.email, gsal.email) AS sales_email
   FROM ((((((public.customer c
     JOIN public.v_customer_tree t ON ((t.customer_id = c.customer_id)))
     LEFT JOIN public.customer p ON (((p.customer_id = c.parent_customer_id) AND (COALESCE(p.is_deleted, false) = false) AND ((p.customer_status)::text <> 'Archived'::text))))
     LEFT JOIN public.people kam ON ((kam.people_id = c.main_kam_people_id)))
     LEFT JOIN public.people sal ON ((sal.people_id = c.main_sales_people_id)))
     LEFT JOIN public.people gkam ON ((gkam.people_id = p.main_kam_people_id)))
     LEFT JOIN public.people gsal ON ((gsal.people_id = p.main_sales_people_id)))
  WHERE ((COALESCE(c.is_deleted, false) = false) AND ((c.customer_status)::text <> 'Archived'::text));


--
-- Name: v_customer_sales_organisation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_customer_sales_organisation AS
 SELECT c.customer_id,
        CASE
            WHEN ((c.customer_type)::text = 'Customer Group'::text) THEN c.customer_id
            ELSE parent.customer_id
        END AS customer_group_id,
        CASE
            WHEN ((c.customer_type)::text = 'Customer Group'::text) THEN c.customer_name
            ELSE parent.customer_name
        END AS customer_group_name,
    c.customer_name,
    c.customer_code,
    c.customer_type,
    c.customer_status,
    c.country,
    c.city,
    directory.kam_people_id,
    directory.kam_name,
    directory.kam_email,
    directory.kam_inherited_from_group,
    c.zone_id,
    z.zone_name,
    zm.zone_manager_names,
    zm.zone_manager_emails
   FROM ((((public.customer c
     LEFT JOIN public.customer parent ON (((parent.customer_id = c.parent_customer_id) AND (COALESCE(parent.is_deleted, false) = false) AND ((parent.customer_status)::text <> 'Archived'::text))))
     LEFT JOIN public.zone z ON ((z.zone_id = c.zone_id)))
     LEFT JOIN public.v_sales_customer_directory directory ON ((directory.customer_id = c.customer_id)))
     LEFT JOIN LATERAL ( SELECT string_agg(DISTINCT concat_ws(' '::text, pa.first_name, pa.last_name), ', '::text) AS zone_manager_names,
            string_agg(DISTINCT (pa.email)::text, ', '::text) AS zone_manager_emails
           FROM public.v_people_assignments pa
          WHERE ((pa.zone_id = c.zone_id) AND ((pa.role_name)::text ~~* '%Zone Manager%'::text) AND (lower((pa.people_status)::text) = 'active'::text) AND (lower((pa.assignment_status)::text) = 'active'::text))) zm ON (true))
  WHERE ((COALESCE(c.is_deleted, false) = false) AND ((c.customer_status)::text <> 'Archived'::text));


--
-- Name: v_effectifs; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_effectifs AS
 SELECT u.unit_name,
    COALESCE(r.role_name, pra.target_role_label) AS role,
    count(DISTINCT p.people_id) AS effectif
   FROM (((public.people p
     JOIN public.people_role_assignment pra ON (((pra.people_id = p.people_id) AND ((pra.assignment_status)::text = 'ACTIVE'::text) AND pra.is_primary)))
     LEFT JOIN public.role r ON ((r.role_id = pra.role_id)))
     LEFT JOIN public.unit u ON ((u.unit_id = p.work_at_unit_id)))
  GROUP BY u.unit_name, COALESCE(r.role_name, pra.target_role_label);


--
-- Name: v_factory_headcount; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_factory_headcount AS
 SELECT f.unit_id AS factory_id,
    f.unit_name AS factory_name,
    f.country,
    f.operating_currency,
    d.unit_id AS dept_id,
    d.unit_name AS dept_name,
    count(DISTINCT p.people_id) AS headcount,
    count(DISTINCT
        CASE
            WHEN ((p.status)::text = 'Active'::text) THEN p.people_id
            ELSE NULL::integer
        END) AS active_headcount,
    count(DISTINCT pra.role_id) AS nb_distinct_roles,
    string_agg(DISTINCT (r.role_name)::text, ', '::text ORDER BY (r.role_name)::text) AS roles_present
   FROM ((((public.unit f
     JOIN public.unit d ON (((d.parent_unit_id = f.unit_id) AND (d.unit_type_id = 5))))
     LEFT JOIN public.people p ON (((p.work_at_unit_id = d.unit_id) OR (p.work_at_unit_id = f.unit_id))))
     LEFT JOIN public.people_role_assignment pra ON (((pra.people_id = p.people_id) AND ((pra.unit_id = f.unit_id) OR (pra.unit_id = d.unit_id)) AND ((pra.assignment_status)::text = 'ACTIVE'::text))))
     LEFT JOIN public.role r ON ((r.role_id = pra.role_id)))
  WHERE (f.unit_type_id = 4)
  GROUP BY f.unit_id, f.unit_name, f.country, f.operating_currency, d.unit_id, d.unit_name
  ORDER BY f.unit_name, d.unit_name;


--
-- Name: VIEW v_factory_headcount; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_factory_headcount IS 'Effectif par factory et département.
headcount        : toutes personnes rattachées.
active_headcount : personnes avec status=Active.
roles_present    : liste des rôles actifs dans ce département.';


--
-- Name: v_hierarchie_reelle; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_hierarchie_reelle AS
 WITH RECURSIVE effective AS (
         SELECT a_1.assignment_id,
            a_1.people_id,
            a_1.role_id,
            a_1.unit_id,
            a_1.is_primary,
            COALESCE(explicit_mgr.assignment_id, fallback.assignment_id) AS effective_reports_to
           FROM (((public.people_role_assignment a_1
             JOIN public.role r_1 ON ((r_1.role_id = a_1.role_id)))
             LEFT JOIN public.people_role_assignment explicit_mgr ON (((explicit_mgr.assignment_id = a_1.reports_to) AND (upper((explicit_mgr.assignment_status)::text) = 'ACTIVE'::text) AND (explicit_mgr.assignment_id <> a_1.assignment_id))))
             LEFT JOIN LATERAL ( SELECT candidate.assignment_id
                   FROM (public.people_role_assignment candidate
                     JOIN public.role candidate_role ON ((candidate_role.role_id = candidate.role_id)))
                  WHERE ((explicit_mgr.assignment_id IS NULL) AND (upper((candidate.assignment_status)::text) = 'ACTIVE'::text) AND (candidate.assignment_id <> a_1.assignment_id) AND (candidate.role_id = r_1.parent_role_id) AND (((candidate_role.scope)::text = 'Groupe'::text) OR (candidate.unit_id = a_1.unit_id) OR (candidate.unit_id IS NULL)) AND ((a_1.zone_id IS NULL) OR (candidate.zone_id IS NULL) OR (candidate.zone_id = a_1.zone_id)) AND ((a_1.market_id IS NULL) OR (candidate.market_id IS NULL) OR (candidate.market_id = a_1.market_id)) AND ((a_1.product_line_id IS NULL) OR (candidate.product_line_id IS NULL) OR (candidate.product_line_id = a_1.product_line_id)))
                  ORDER BY
                        CASE
                            WHEN (candidate.unit_id = a_1.unit_id) THEN 1
                            WHEN (candidate.unit_id IS NULL) THEN 2
                            ELSE 3
                        END,
                        CASE
                            WHEN (candidate.zone_id = a_1.zone_id) THEN 1
                            WHEN (candidate.zone_id IS NULL) THEN 2
                            ELSE 3
                        END,
                        CASE
                            WHEN (candidate.product_line_id = a_1.product_line_id) THEN 1
                            WHEN (candidate.product_line_id IS NULL) THEN 2
                            ELSE 3
                        END, candidate.is_primary DESC NULLS LAST, candidate.assignment_id
                 LIMIT 1) fallback ON (true))
          WHERE (upper((a_1.assignment_status)::text) = 'ACTIVE'::text)
        ), arbre AS (
         SELECT e.assignment_id,
            e.people_id,
            e.effective_reports_to AS reports_to,
            e.role_id,
            e.unit_id,
            0 AS niveau,
            (((p_1.first_name)::text || ' '::text) || (p_1.name)::text) AS chemin,
            ARRAY[e.assignment_id] AS assignment_path
           FROM (effective e
             JOIN public.people p_1 ON ((p_1.people_id = e.people_id)))
          WHERE ((e.effective_reports_to IS NULL) AND (e.is_primary = true))
        UNION ALL
         SELECT c.assignment_id,
            c.people_id,
            c.effective_reports_to,
            c.role_id,
            c.unit_id,
            (a_1.niveau + 1),
            ((((a_1.chemin || ' > '::text) || (p_1.first_name)::text) || ' '::text) || (p_1.name)::text),
            (a_1.assignment_path || c.assignment_id)
           FROM ((effective c
             JOIN arbre a_1 ON ((c.effective_reports_to = a_1.assignment_id)))
             JOIN public.people p_1 ON ((p_1.people_id = c.people_id)))
          WHERE (NOT (c.assignment_id = ANY (a_1.assignment_path)))
        )
 SELECT a.niveau,
    a.assignment_id,
    a.people_id,
    (((p.first_name)::text || ' '::text) || (p.name)::text) AS personne,
    p.email,
    r.role_name,
    u.unit_name,
    (((mgr.first_name)::text || ' '::text) || (mgr.name)::text) AS manager,
    a.chemin
   FROM (((((arbre a
     JOIN public.people p ON ((p.people_id = a.people_id)))
     LEFT JOIN public.role r ON ((r.role_id = a.role_id)))
     LEFT JOIN public.unit u ON ((u.unit_id = a.unit_id)))
     LEFT JOIN effective mgr_e ON ((mgr_e.assignment_id = a.reports_to)))
     LEFT JOIN public.people mgr ON ((mgr.people_id = mgr_e.people_id)));


--
-- Name: v_hierarchie_roles; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_hierarchie_roles AS
 WITH RECURSIVE rtree AS (
         SELECT role.role_id,
            role.role_name,
            role.parent_role_id,
            0 AS niveau,
            lpad((role.role_id)::text, 3, '0'::text) AS sort_path
           FROM public.role
          WHERE ((role.parent_role_id IS NULL) AND role.is_active)
        UNION ALL
         SELECT r.role_id,
            r.role_name,
            r.parent_role_id,
            (t.niveau + 1),
            ((t.sort_path || '.'::text) || lpad((r.role_id)::text, 3, '0'::text))
           FROM (public.role r
             JOIN rtree t ON ((r.parent_role_id = t.role_id)))
          WHERE r.is_active
        )
 SELECT niveau,
    role_id,
    role_name,
    parent_role_id,
    sort_path,
    (repeat('    '::text, niveau) || (role_name)::text) AS hierarchie
   FROM rtree
  ORDER BY sort_path;


--
-- Name: v_kam_customer_visits_weekly; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kam_customer_visits_weekly AS
 WITH active_kams AS (
         SELECT a.kpi_target_allocation_id AS allocation_id,
            a.set_by_people_id AS kam_people_id,
            TRIM(BOTH FROM concat_ws(' '::text, p.first_name, p.name)) AS kam_name,
            p.email AS kam_email,
            a.target_value AS weekly_target
           FROM (public.kpi_target_allocation a
             JOIN public.people p ON ((p.people_id = a.set_by_people_id)))
          WHERE ((a.kpi_id = 655) AND (a.is_active IS TRUE) AND ((p.status)::text = 'Active'::text))
        ), weeks AS (
         SELECT (gs.gs)::date AS week_start,
            to_char(gs.gs, 'IYYY-"Week"IW'::text) AS week
           FROM generate_series(((date_trunc('week'::text, ('2026-08-27'::date)::timestamp with time zone))::date)::timestamp with time zone, ((date_trunc('week'::text, (CURRENT_DATE)::timestamp with time zone))::date)::timestamp with time zone, '7 days'::interval) gs(gs)
        ), latest_results AS (
         SELECT DISTINCT ON (r_1.kpi_target_allocation_id, r_1.week) r_1.kpi_target_allocation_id AS allocation_id,
            r_1.week,
            r_1.raw_value AS visits,
            r_1.result_date,
            r_1.recorded_at,
            r_1.kpi_result_id
           FROM public.kpi_result r_1
          WHERE ((r_1.kpi_id = 655) AND ((r_1.period_type)::text = 'Week'::text))
          ORDER BY r_1.kpi_target_allocation_id, r_1.week, r_1.recorded_at DESC NULLS LAST, r_1.kpi_result_id DESC
        )
 SELECT k.allocation_id,
    k.kam_people_id,
    k.kam_name,
    k.kam_email,
    w.week,
    w.week_start,
    (w.week_start + 6) AS week_end,
    k.weekly_target,
    r.visits AS customer_visits,
        CASE
            WHEN (r.kpi_result_id IS NULL) THEN 'Not submitted'::text
            WHEN (r.visits IS NULL) THEN 'Missing value'::text
            WHEN (k.weekly_target IS NULL) THEN 'Target not configured'::text
            WHEN (r.visits >= k.weekly_target) THEN 'Target achieved'::text
            ELSE 'Below target'::text
        END AS performance_status,
        CASE
            WHEN ((r.visits IS NOT NULL) AND (k.weekly_target > (0)::numeric)) THEN round(((r.visits * 100.0) / k.weekly_target), 2)
            ELSE NULL::numeric
        END AS achievement_percent,
    (r.kpi_result_id IS NOT NULL) AS is_submitted,
    r.result_date,
    r.recorded_at AS last_submission_at
   FROM ((active_kams k
     CROSS JOIN weeks w)
     LEFT JOIN latest_results r ON (((r.allocation_id = k.allocation_id) AND ((r.week)::text = w.week))));


--
-- Name: v_kpi_audit; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_audit AS
 SELECT audit_id,
    kpi_id,
    operation,
    changed_at,
    changed_by,
    application_name,
    client_address,
    transaction_id,
    old_data,
    new_data
   FROM public.kpi_audit;


--
-- Name: v_kpi_audit_changes; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_audit_changes AS
 SELECT a.audit_id,
    a.kpi_id,
    a.operation,
    a.changed_at,
    a.changed_by,
    a.application_name,
    a.client_address,
    changed_field.key AS column_name,
    (a.old_data -> changed_field.key) AS old_value,
    (a.new_data -> changed_field.key) AS new_value
   FROM (public.kpi_audit a
     CROSS JOIN LATERAL jsonb_object_keys((COALESCE(a.old_data, '{}'::jsonb) || COALESCE(a.new_data, '{}'::jsonb))) changed_field(key))
  WHERE (((a.operation)::text <> 'UPDATE'::text) OR ((a.old_data -> changed_field.key) IS DISTINCT FROM (a.new_data -> changed_field.key)));


--
-- Name: v_kpi_coverage; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_coverage AS
 SELECT f.unit_id AS factory_id,
    f.unit_name AS factory_name,
    f.country AS factory_country,
    d.unit_id AS dept_id,
    d.unit_name AS dept_name,
    count(DISTINCT a.kpi_id) AS nb_kpi,
    count(DISTINCT a.kpi_target_allocation_id) AS nb_allocations,
    count(DISTINCT
        CASE
            WHEN ((a.target_status)::text = 'Active'::text) THEN a.kpi_id
            ELSE NULL::integer
        END) AS nb_kpi_active,
    min(a.target_start_date) AS first_target_date,
    max(a.target_end_date) AS last_target_date
   FROM ((public.unit f
     JOIN public.unit d ON (((d.parent_unit_id = f.unit_id) AND (d.unit_type_id = 5))))
     LEFT JOIN public.kpi_target_allocation a ON (((a.plant_id = f.unit_id) OR (a.unit_id = d.unit_id))))
  WHERE (f.unit_type_id = 4)
  GROUP BY f.unit_id, f.unit_name, f.country, d.unit_id, d.unit_name
  ORDER BY f.unit_name, d.unit_name;


--
-- Name: VIEW v_kpi_coverage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_kpi_coverage IS 'Couverture KPI par factory et département.
nb_kpi = 0 identifie les départements sans KPI alloué.
Utile pour le tableau de bord de déploiement des KPIs.';


--
-- Name: v_kpi_full_context; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_full_context AS
 SELECT k.kpi_id,
    k.kpi_name,
    k.kpi_sub_title,
    k.kpi_code,
    k.unit AS kpi_unit,
    k.frequency,
    k.status AS kpi_status,
    k.target_direction,
    k.low_limit,
    k.high_limit,
    k.target_value AS kpi_default_target,
    k.calculation_mode,
    a.kpi_type AS kpi_category,
    a.kpi_target_allocation_id,
    a.target_value AS allocated_target,
    a.target_unit,
    a.target_status,
    a.target_start_date,
    a.target_end_date,
    a.kpi_type,
    plant.unit_id AS plant_id,
    plant.unit_name AS plant_name,
    plant.country AS plant_country,
    dept.unit_id,
    dept.unit_name,
    ut_dept.unit_type_name AS unit_type,
    vut.full_path AS unit_full_path,
    vut.depth AS unit_depth,
    vut.factory_name AS unit_factory,
    z.zone_id,
    z.zone_name,
    r.role_id,
    r.role_name,
    r.role_level,
    r.is_managerial,
    vr.full_path AS role_full_path,
    vr.depth AS role_depth,
    owner_r.role_id AS owner_role_id,
    owner_r.role_name AS owner_role_name,
    s.subject_id AS primary_subject_id,
    s.subject_name AS primary_subject_name,
    m.market_id,
    m.market_name
   FROM ((((((((((((public.kpi k
     LEFT JOIN public.kpi_target_allocation a ON ((a.kpi_id = k.kpi_id)))
     LEFT JOIN public.unit plant ON ((plant.unit_id = a.plant_id)))
     LEFT JOIN public.unit dept ON ((dept.unit_id = a.unit_id)))
     LEFT JOIN public.unit_type ut_dept ON ((ut_dept.unit_type_id = dept.unit_type_id)))
     LEFT JOIN public.v_unit_tree vut ON ((vut.unit_id = COALESCE(a.unit_id, a.plant_id))))
     LEFT JOIN public.zone z ON ((z.zone_id = a.zone_id)))
     LEFT JOIN public.role r ON ((r.role_id = a.role_id)))
     LEFT JOIN public.v_role_tree vr ON ((vr.role_id = a.role_id)))
     LEFT JOIN public.role owner_r ON ((owner_r.role_id = k.owner_role_id)))
     LEFT JOIN public.subject_kpi sk ON (((sk.kpi_id = k.kpi_id) AND (sk.is_primary = true))))
     LEFT JOIN public.subject s ON ((s.subject_id = sk.subject_id)))
     LEFT JOIN public.market m ON ((m.market_id = a.market_id)));


--
-- Name: VIEW v_kpi_full_context; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_kpi_full_context IS 'Vue plate dénormalisée pour Power BI — 1 ligne par KPI × allocation.
Jointure KPI + target + factory + département + zone + rôle + sujet primaire.
Table principale à connecter en DirectQuery dans Power BI.';


--
-- Name: v_kpi_monitoring; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_monitoring AS
 SELECT s.monitoring_status_id,
    s.kpi_id,
    k.kpi_name,
    k.kpi_code,
    k.unit AS kpi_unit,
    k.frequency,
    k.target_direction,
    s.kpi_target_allocation_id,
    a.target_status,
    a.plant_id,
    plant.unit_name AS plant_name,
    a.unit_id,
    d.unit_name,
    a.role_id,
    r.role_name,
    z.zone_id,
    z.zone_name,
    s.owner_role_id,
    owner_r.role_name AS owner_role_name,
    s.current_status,
    s.latest_value,
    s.latest_target,
    s.performance_gap,
    s.latest_result_date,
    s.priority_score,
    s.escalation_level,
    s.calculation_date,
    s.updated_at
   FROM (((((((public.kpi_monitoring_status s
     JOIN public.kpi k ON ((k.kpi_id = s.kpi_id)))
     JOIN public.kpi_target_allocation a ON ((a.kpi_target_allocation_id = s.kpi_target_allocation_id)))
     LEFT JOIN public.unit plant ON ((plant.unit_id = a.plant_id)))
     LEFT JOIN public.unit d ON ((d.unit_id = a.unit_id)))
     LEFT JOIN public.role r ON ((r.role_id = a.role_id)))
     LEFT JOIN public.zone z ON ((z.zone_id = a.zone_id)))
     LEFT JOIN public.role owner_r ON ((owner_r.role_id = s.owner_role_id)));


--
-- Name: VIEW v_kpi_monitoring; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_kpi_monitoring IS 'Lecture enrichie de l''état courant des KPI (statut + contexte). V1.';


--
-- Name: v_kpi_target_allocation_audit; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_target_allocation_audit AS
 SELECT audit_id,
    kpi_target_allocation_id,
    operation,
    changed_at,
    changed_by,
    application_name,
    client_address,
    transaction_id,
    old_data,
    new_data
   FROM public.kpi_target_allocation_audit;


--
-- Name: v_kpi_target_allocation_audit_changes; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_kpi_target_allocation_audit_changes AS
 SELECT a.audit_id,
    a.kpi_target_allocation_id,
    a.operation,
    a.changed_at,
    a.changed_by,
    changed.key AS column_name,
    (a.old_data -> changed.key) AS old_value,
    (a.new_data -> changed.key) AS new_value
   FROM (public.kpi_target_allocation_audit a
     CROSS JOIN LATERAL jsonb_object_keys((COALESCE(a.old_data, '{}'::jsonb) || COALESCE(a.new_data, '{}'::jsonb))) changed(key))
  WHERE (((a.operation)::text <> 'UPDATE'::text) OR ((a.old_data -> changed.key) IS DISTINCT FROM (a.new_data -> changed.key)));


--
-- Name: v_people_department; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_people_department AS
 WITH role_department_mapping AS (
         SELECT pra_1.assignment_id,
            pra_1.people_id,
            pra_1.role_id,
            pra_1.unit_id AS factory_id,
                CASE
                    WHEN ((r_1.role_name)::text ~~* '%Production%'::text) THEN 'Production'::text
                    WHEN ((r_1.role_name)::text ~~* '%Quality%'::text) THEN 'Quality'::text
                    WHEN ((r_1.role_name)::text ~~* '%Finance%'::text) THEN 'Finance'::text
                    WHEN ((r_1.role_name)::text ~~* '%HR%'::text) THEN 'HR'::text
                    WHEN ((r_1.role_name)::text ~~* '%Supply%'::text) THEN 'Supply Chain'::text
                    WHEN ((r_1.role_name)::text ~~* '%Maintenance%'::text) THEN 'Maintenance'::text
                    WHEN ((r_1.role_name)::text ~~* '%Project%'::text) THEN 'Project'::text
                    WHEN ((r_1.role_name)::text ~~* '%Process%'::text) THEN 'Process Engineering'::text
                    ELSE NULL::text
                END AS target_department
           FROM (public.people_role_assignment pra_1
             JOIN public.role r_1 ON ((r_1.role_id = pra_1.role_id)))
          WHERE ((pra_1.assignment_status)::text = 'ACTIVE'::text)
        )
 SELECT p.people_id,
    p.first_name,
    p.name,
    concat(p.first_name, ' ', p.name) AS employee_name,
    p.email,
    r.role_id,
    r.role_name,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN parent_unit.unit_id
            ELSE current_unit.unit_id
        END AS factory_id,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN parent_unit.unit_name
            ELSE current_unit.unit_name
        END AS factory_name,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN parent_unit.country
            ELSE current_unit.country
        END AS country,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN parent_unit.selling_currency
            ELSE current_unit.selling_currency
        END AS selling_currency,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN parent_unit.operating_currency
            ELSE current_unit.operating_currency
        END AS operating_currency,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN current_unit.unit_id
            ELSE dept.unit_id
        END AS department_id,
        CASE
            WHEN (current_unit.unit_type_id = 5) THEN current_unit.unit_name
            ELSE dept.unit_name
        END AS department_name,
    pra.is_primary,
    pra.assignment_status
   FROM ((((((public.people_role_assignment pra
     JOIN public.people p ON ((p.people_id = pra.people_id)))
     JOIN public.role r ON ((r.role_id = pra.role_id)))
     JOIN public.unit current_unit ON ((current_unit.unit_id = pra.unit_id)))
     LEFT JOIN public.unit parent_unit ON ((parent_unit.unit_id = current_unit.parent_unit_id)))
     LEFT JOIN role_department_mapping m ON ((m.assignment_id = pra.assignment_id)))
     LEFT JOIN public.unit dept ON (((dept.parent_unit_id = current_unit.unit_id) AND (dept.unit_type_id = 5) AND ((dept.unit_name)::text = m.target_department))))
  WHERE ((pra.assignment_status)::text = 'ACTIVE'::text);


--
-- Name: v_people_kpi_role_visibility; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_people_kpi_role_visibility AS
 WITH RECURSIVE role_descendants AS (
         SELECT r.role_id AS viewer_role_id,
            r.role_id AS visible_role_id,
            0 AS hierarchy_depth,
            ARRAY[r.role_id] AS role_path
           FROM public.role r
          WHERE (r.is_active = true)
        UNION ALL
         SELECT rd_1.viewer_role_id,
            child.role_id AS visible_role_id,
            (rd_1.hierarchy_depth + 1),
            (rd_1.role_path || child.role_id)
           FROM (role_descendants rd_1
             JOIN public.role child ON (((child.parent_role_id = rd_1.visible_role_id) AND (child.is_active = true))))
          WHERE (NOT (child.role_id = ANY (rd_1.role_path)))
        ), active_viewer_assignments AS (
         SELECT pra.assignment_id AS viewer_assignment_id,
            pra.people_id AS viewer_people_id,
            pra.role_id AS viewer_role_id,
            pra.unit_id AS viewer_unit_id,
            pra.zone_id AS viewer_zone_id,
            pra.market_id AS viewer_market_id,
            pra.product_line_id AS viewer_product_line_id,
            (((p.first_name)::text || ' '::text) || (p.name)::text) AS viewer_name,
            p.email AS viewer_email,
            vr.role_name AS viewer_role_name,
            vr.grade AS viewer_grade,
            vr.ceo_level AS viewer_level,
            vu.unit_name AS viewer_unit_name,
            vpl.product_line_name AS viewer_product_line_name
           FROM ((((public.people_role_assignment pra
             JOIN public.people p ON ((p.people_id = pra.people_id)))
             JOIN public.role vr ON ((vr.role_id = pra.role_id)))
             LEFT JOIN public.unit vu ON ((vu.unit_id = pra.unit_id)))
             LEFT JOIN public.product_line vpl ON ((vpl.product_line_id = pra.product_line_id)))
          WHERE ((upper((pra.assignment_status)::text) = 'ACTIVE'::text) AND (upper((p.status)::text) = 'ACTIVE'::text))
        ), kpi_allocations AS (
         SELECT kta.kpi_target_allocation_id,
            kta.kpi_id,
            (COALESCE(kta.role_id, k.owner_role_id))::bigint AS allocated_role_id,
            kta.unit_id AS allocated_unit_id,
            kta.plant_id,
            kta.zone_id AS allocated_zone_id,
            kta.market_id AS allocated_market_id,
            kta.product_line_id AS allocated_product_line_id,
            kta.target_value,
            kta.target_unit,
            kta.target_status,
            kta.target_start_date,
            kta.target_end_date,
            kta.kpi_type,
            k.kpi_name,
            k.kpi_code,
            k.kpi_sub_title,
            k.kpi_explanation,
            k.frequency,
            k.status AS kpi_status,
            k.owner_role_id,
            ar.role_name AS allocated_role_name,
            ar.grade AS allocated_role_grade,
            ar.ceo_level AS allocated_role_level,
            au.unit_name AS allocated_unit_name,
            apl.product_line_name AS allocated_product_line_name
           FROM ((((public.kpi_target_allocation kta
             JOIN public.kpi k ON ((k.kpi_id = kta.kpi_id)))
             LEFT JOIN public.role ar ON ((ar.role_id = COALESCE(kta.role_id, k.owner_role_id))))
             LEFT JOIN public.unit au ON ((au.unit_id = kta.unit_id)))
             LEFT JOIN public.product_line apl ON ((apl.product_line_id = kta.product_line_id)))
          WHERE ((upper((COALESCE(k.status, 'ACTIVE'::character varying))::text) = 'ACTIVE'::text) AND (upper((COALESCE(kta.target_status, 'ACTIVE'::character varying))::text) = 'ACTIVE'::text))
        ), responsible_people AS (
         SELECT pra.assignment_id AS responsible_assignment_id,
            pra.people_id AS responsible_people_id,
            pra.role_id AS responsible_role_id,
            pra.unit_id AS responsible_unit_id,
            pra.zone_id AS responsible_zone_id,
            pra.market_id AS responsible_market_id,
            pra.product_line_id AS responsible_product_line_id,
            (((p.first_name)::text || ' '::text) || (p.name)::text) AS responsible_name,
            p.email AS responsible_email,
            r.role_name AS responsible_role_name,
            u.unit_name AS responsible_unit_name,
            pl.product_line_name AS responsible_product_line_name
           FROM ((((public.people_role_assignment pra
             JOIN public.people p ON ((p.people_id = pra.people_id)))
             JOIN public.role r ON ((r.role_id = pra.role_id)))
             LEFT JOIN public.unit u ON ((u.unit_id = pra.unit_id)))
             LEFT JOIN public.product_line pl ON ((pl.product_line_id = pra.product_line_id)))
          WHERE ((upper((pra.assignment_status)::text) = 'ACTIVE'::text) AND (upper((p.status)::text) = 'ACTIVE'::text))
        )
 SELECT DISTINCT viewer.viewer_people_id,
    viewer.viewer_name,
    viewer.viewer_email,
    viewer.viewer_assignment_id,
    viewer.viewer_role_id,
    viewer.viewer_role_name,
    viewer.viewer_grade,
    viewer.viewer_level,
    viewer.viewer_unit_id,
    viewer.viewer_unit_name,
    viewer.viewer_product_line_id,
    viewer.viewer_product_line_name,
    rd.visible_role_id,
    rd.hierarchy_depth,
    visible_role.role_name AS visible_role_name,
    visible_tree.full_path AS visible_role_path,
    ka.kpi_id,
    ka.kpi_name,
    ka.kpi_code,
    ka.kpi_sub_title,
    ka.kpi_explanation,
    ka.frequency,
    ka.kpi_status,
    ka.kpi_target_allocation_id,
    ka.target_value,
    ka.target_unit,
    ka.target_status,
    ka.target_start_date,
    ka.target_end_date,
    ka.kpi_type,
    ka.allocated_role_id,
    ka.allocated_role_name,
    ka.allocated_role_grade,
    ka.allocated_role_level,
    ka.allocated_unit_id,
    ka.allocated_unit_name,
    ka.allocated_product_line_id,
    ka.allocated_product_line_name,
    responsible.responsible_people_id,
    responsible.responsible_name,
    responsible.responsible_email,
    responsible.responsible_assignment_id,
    responsible.responsible_role_id,
    responsible.responsible_role_name,
    responsible.responsible_unit_id,
    responsible.responsible_unit_name,
    responsible.responsible_product_line_id,
    responsible.responsible_product_line_name,
        CASE
            WHEN (rd.hierarchy_depth = 0) THEN 'OWN_ROLE'::text
            ELSE 'DESCENDANT_ROLE'::text
        END AS visibility_reason
   FROM (((((active_viewer_assignments viewer
     JOIN role_descendants rd ON ((rd.viewer_role_id = viewer.viewer_role_id)))
     JOIN public.role visible_role ON ((visible_role.role_id = rd.visible_role_id)))
     LEFT JOIN public.v_role_tree visible_tree ON ((visible_tree.role_id = rd.visible_role_id)))
     JOIN kpi_allocations ka ON ((ka.allocated_role_id = rd.visible_role_id)))
     LEFT JOIN responsible_people responsible ON (((responsible.responsible_role_id = ka.allocated_role_id) AND ((ka.allocated_unit_id IS NULL) OR (responsible.responsible_unit_id = ka.allocated_unit_id)) AND ((ka.allocated_product_line_id IS NULL) OR (responsible.responsible_product_line_id = ka.allocated_product_line_id)) AND ((ka.allocated_zone_id IS NULL) OR (responsible.responsible_zone_id = ka.allocated_zone_id)) AND ((ka.allocated_market_id IS NULL) OR (responsible.responsible_market_id = ka.allocated_market_id)))));


--
-- Name: v_people_with_boss; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_people_with_boss AS
 SELECT p.people_id,
    p.first_name,
    p.name AS last_name,
    (((p.first_name)::text || ' '::text) || (p.name)::text) AS person,
    p.email,
    r.role_id,
    r.role_name,
    r.role_level,
    pra.unit_id,
    u.unit_name AS factory,
    u.country,
    pra.zone_id,
    z.zone_name,
    COALESCE(explicit_boss.people_id, role_boss.people_id) AS boss_people_id,
    COALESCE((((explicit_boss.first_name)::text || ' '::text) || (explicit_boss.name)::text), (((role_boss.first_name)::text || ' '::text) || (role_boss.name)::text)) AS boss_person,
    COALESCE(explicit_boss.email, role_boss.email) AS boss_email,
    COALESCE(explicit_boss_role.role_id, role_boss_role.role_id) AS boss_role_id,
    COALESCE(explicit_boss_role.role_name, role_boss_role.role_name) AS boss_role,
        CASE
            WHEN (COALESCE(explicit_boss.people_id, role_boss.people_id) IS NULL) THEN NULL::text
            ELSE (((COALESCE(explicit_boss_role.role_name, role_boss_role.role_name))::text || ' -> '::text) || (r.role_name)::text)
        END AS hierarchy_path,
    pra.product_line_id,
    pl.product_line_name,
    pra.assignment_id,
    COALESCE(explicit_boss_pra.assignment_id, role_boss_pra.assignment_id) AS boss_assignment_id,
    r.parent_role_id
   FROM (((((((((((public.people_role_assignment pra
     JOIN public.people p ON ((p.people_id = pra.people_id)))
     JOIN public.role r ON ((r.role_id = pra.role_id)))
     LEFT JOIN public.unit u ON ((u.unit_id = pra.unit_id)))
     LEFT JOIN public.zone z ON ((z.zone_id = pra.zone_id)))
     LEFT JOIN public.product_line pl ON ((pl.product_line_id = pra.product_line_id)))
     LEFT JOIN public.people_role_assignment explicit_boss_pra ON (((explicit_boss_pra.assignment_id = pra.reports_to) AND (upper((explicit_boss_pra.assignment_status)::text) = 'ACTIVE'::text) AND (explicit_boss_pra.assignment_id <> pra.assignment_id))))
     LEFT JOIN public.people explicit_boss ON ((explicit_boss.people_id = explicit_boss_pra.people_id)))
     LEFT JOIN public.role explicit_boss_role ON ((explicit_boss_role.role_id = explicit_boss_pra.role_id)))
     LEFT JOIN LATERAL ( SELECT candidate.assignment_id,
            candidate.people_id,
            candidate.role_id,
            candidate.unit_id,
            candidate.zone_id,
            candidate.market_id,
            candidate.assignment_type,
            candidate.allocation_percentage,
            candidate.is_primary,
            candidate.functional_manager_role_id,
            candidate.assistant_context,
            candidate.knowledge_context,
            candidate.start_date,
            candidate.end_date,
            candidate.assignment_status,
            candidate.created_at,
            candidate.updated_at,
            candidate.product_line_id,
            candidate.reports_to,
            candidate.target_role_label,
            candidate.functional_manager_assignment_id,
            candidate.admin_manager_assignment_id
           FROM (public.people_role_assignment candidate
             JOIN public.role candidate_role ON ((candidate_role.role_id = candidate.role_id)))
          WHERE ((explicit_boss_pra.assignment_id IS NULL) AND (upper((candidate.assignment_status)::text) = 'ACTIVE'::text) AND (candidate.assignment_id <> pra.assignment_id) AND (candidate.role_id = r.parent_role_id) AND (((candidate_role.scope)::text = 'Groupe'::text) OR (candidate.unit_id = pra.unit_id) OR (candidate.unit_id IS NULL)) AND ((pra.zone_id IS NULL) OR (candidate.zone_id IS NULL) OR (candidate.zone_id = pra.zone_id)) AND ((pra.market_id IS NULL) OR (candidate.market_id IS NULL) OR (candidate.market_id = pra.market_id)) AND ((pra.product_line_id IS NULL) OR (candidate.product_line_id IS NULL) OR (candidate.product_line_id = pra.product_line_id)))
          ORDER BY
                CASE
                    WHEN (candidate.unit_id = pra.unit_id) THEN 1
                    WHEN (candidate.unit_id IS NULL) THEN 2
                    ELSE 3
                END,
                CASE
                    WHEN (candidate.zone_id = pra.zone_id) THEN 1
                    WHEN (candidate.zone_id IS NULL) THEN 2
                    ELSE 3
                END,
                CASE
                    WHEN (candidate.product_line_id = pra.product_line_id) THEN 1
                    WHEN (candidate.product_line_id IS NULL) THEN 2
                    ELSE 3
                END, candidate.is_primary DESC NULLS LAST, candidate.assignment_id
         LIMIT 1) role_boss_pra ON (true))
     LEFT JOIN public.people role_boss ON ((role_boss.people_id = role_boss_pra.people_id)))
     LEFT JOIN public.role role_boss_role ON ((role_boss_role.role_id = role_boss_pra.role_id)))
  WHERE ((upper((pra.assignment_status)::text) = 'ACTIVE'::text) AND ((pra.is_primary = true) OR (r.role_id = ANY (ARRAY[(206)::bigint, (207)::bigint, (11)::bigint, (12)::bigint, (13)::bigint, (14)::bigint, (15)::bigint, (16)::bigint])) OR (lower((COALESCE(r.normalized_role_name, r.role_name))::text) = 'product line manager'::text)));


--
-- Name: v_people_with_boss_strict_test; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_people_with_boss_strict_test AS
 SELECT v.people_id,
    v.first_name,
    v.last_name,
    v.person,
    v.email,
    v.role_id,
    v.role_name,
    v.role_level,
    v.unit_id,
    v.factory,
    v.country,
    v.zone_id,
    v.zone_name,
    boss.people_id AS boss_people_id,
        CASE
            WHEN (boss.people_id IS NOT NULL) THEN (((boss.first_name)::text || ' '::text) || (boss.name)::text)
            ELSE NULL::text
        END AS boss_person,
    boss.email AS boss_email,
    boss_role.role_id AS boss_role_id,
    boss_role.role_name AS boss_role,
        CASE
            WHEN (boss.people_id IS NOT NULL) THEN (((boss_role.role_name)::text || ' -> '::text) || (v.role_name)::text)
            ELSE NULL::text
        END AS hierarchy_path,
    v.product_line_id,
    v.product_line_name,
    v.assignment_id,
    manager_assignment.assignment_id AS boss_assignment_id,
    v.parent_role_id
   FROM ((((public.v_people_with_boss v
     JOIN public.people_role_assignment pra ON ((pra.assignment_id = v.assignment_id)))
     LEFT JOIN public.people_role_assignment manager_assignment ON (((manager_assignment.assignment_id = pra.reports_to) AND (upper((manager_assignment.assignment_status)::text) = 'ACTIVE'::text) AND (manager_assignment.people_id <> pra.people_id))))
     LEFT JOIN public.people boss ON ((boss.people_id = manager_assignment.people_id)))
     LEFT JOIN public.role boss_role ON ((boss_role.role_id = manager_assignment.role_id)));


--
-- Name: v_personne_complete; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_personne_complete AS
 SELECT p.people_id,
    (((p.first_name)::text || ' '::text) || (p.name)::text) AS personne,
    p.email,
    p.hr_employee_id,
    p.hr_site,
    uw.unit_name AS travaille_a,
    up.unit_name AS paye_par,
    r.role_name AS role,
    COALESCE(pra.target_role_label, r.role_name) AS role_cible,
    rp.role_name AS role_parent,
    pra.is_primary,
    (((mgr.first_name)::text || ' '::text) || (mgr.name)::text) AS manager_hierarchique,
    mgr_r.role_name AS manager_role,
    fr.role_name AS manager_fonctionnel,
    pra.assignment_id
   FROM (((((((((public.people p
     LEFT JOIN public.people_role_assignment pra ON (((pra.people_id = p.people_id) AND ((pra.assignment_status)::text = 'ACTIVE'::text))))
     LEFT JOIN public.role r ON ((r.role_id = pra.role_id)))
     LEFT JOIN public.role rp ON ((rp.role_id = r.parent_role_id)))
     LEFT JOIN public.unit uw ON ((uw.unit_id = p.work_at_unit_id)))
     LEFT JOIN public.unit up ON ((up.unit_id = p.paid_by_unit_id)))
     LEFT JOIN public.people_role_assignment mgr_a ON ((mgr_a.assignment_id = pra.reports_to)))
     LEFT JOIN public.people mgr ON ((mgr.people_id = mgr_a.people_id)))
     LEFT JOIN public.role mgr_r ON ((mgr_r.role_id = mgr_a.role_id)))
     LEFT JOIN public.role fr ON ((fr.role_id = pra.functional_manager_role_id)));


--
-- Name: v_plant_managers_details; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_plant_managers_details AS
 SELECT unit_id,
    site,
    site_code,
    ville,
    pays,
    people_id,
    matricule,
    prenom,
    nom,
    nom_complet,
    email,
    telephone_professionnel,
    site_rh,
    lieu_de_travail_rh,
    unite_employeur,
    date_embauche,
    statut_personne,
    role_id,
    fonction,
    grade,
    assignment_id,
    type_affectation,
    affectation_principale,
    pourcentage_affectation,
    statut_affectation,
    debut_affectation,
    fin_affectation,
    responsable_hierarchique_assignment_id,
    responsable_hierarchique,
    email_responsable_hierarchique,
    statut_affectation_responsable,
    fin_affectation_responsable,
    functional_manager_assignment_id,
    responsable_fonctionnel,
    email_responsable_fonctionnel
   FROM ( SELECT u.unit_id,
            u.unit_name AS site,
            u.unit_short_name AS site_code,
            u.city AS ville,
            u.country AS pays,
            p.people_id,
            p.hr_employee_id AS matricule,
            p.first_name AS prenom,
            p.name AS nom,
            concat_ws(' '::text, p.first_name, p.name) AS nom_complet,
            p.email,
            p.cell_phone AS telephone_professionnel,
            p.hr_site AS site_rh,
            wu.unit_name AS lieu_de_travail_rh,
            pu.unit_name AS unite_employeur,
            p.hire_date AS date_embauche,
            p.status AS statut_personne,
            r.role_id,
            r.role_name AS fonction,
            r.grade,
            a.assignment_id,
            a.assignment_type AS type_affectation,
            a.is_primary AS affectation_principale,
            a.allocation_percentage AS pourcentage_affectation,
            a.assignment_status AS statut_affectation,
            a.start_date AS debut_affectation,
            a.end_date AS fin_affectation,
            a.reports_to AS responsable_hierarchique_assignment_id,
            NULLIF(concat_ws(' '::text, hp.first_name, hp.name), ''::text) AS responsable_hierarchique,
            hp.email AS email_responsable_hierarchique,
            ha.assignment_status AS statut_affectation_responsable,
            ha.end_date AS fin_affectation_responsable,
            a.functional_manager_assignment_id,
            NULLIF(concat_ws(' '::text, fp.first_name, fp.name), ''::text) AS responsable_fonctionnel,
            fp.email AS email_responsable_fonctionnel
           FROM (((((((((public.people_role_assignment a
             JOIN public.people p ON ((p.people_id = a.people_id)))
             JOIN public.role r ON ((r.role_id = a.role_id)))
             JOIN public.unit u ON ((u.unit_id = a.unit_id)))
             LEFT JOIN public.unit wu ON ((wu.unit_id = p.work_at_unit_id)))
             LEFT JOIN public.unit pu ON ((pu.unit_id = p.paid_by_unit_id)))
             LEFT JOIN public.people_role_assignment ha ON ((ha.assignment_id = a.reports_to)))
             LEFT JOIN public.people hp ON ((hp.people_id = ha.people_id)))
             LEFT JOIN public.people_role_assignment fa ON ((fa.assignment_id = a.functional_manager_assignment_id)))
             LEFT JOIN public.people fp ON ((fp.people_id = fa.people_id)))
          WHERE (((r.role_name)::text = 'Plant manager'::text) AND (r.is_active IS TRUE) AND (upper((p.status)::text) = 'ACTIVE'::text) AND (upper((a.assignment_status)::text) = 'ACTIVE'::text) AND ((a.start_date IS NULL) OR (a.start_date <= CURRENT_DATE)) AND ((a.end_date IS NULL) OR (a.end_date >= CURRENT_DATE)))) existing_view
  WHERE (unit_id <> 26);


--
-- Name: v_redundant_group_wrappers; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_redundant_group_wrappers AS
 SELECT g.customer_id AS group_id,
    g.customer_name AS group_name,
    e.customer_id AS entity_id,
    e.customer_code AS entity_code,
    g.main_kam_people_id AS group_kam,
    e.main_kam_people_id AS entity_kam
   FROM (public.customer g
     JOIN public.customer e ON (((e.parent_customer_id = g.customer_id) AND (COALESCE(e.is_deleted, false) = false) AND ((e.customer_status)::text <> 'Archived'::text))))
  WHERE (((g.customer_type)::text = 'Customer Group'::text) AND (COALESCE(g.is_deleted, false) = false) AND ((g.customer_status)::text <> 'Archived'::text) AND (lower(regexp_replace((g.customer_name)::text, '[^a-zA-Z0-9]'::text, ''::text, 'g'::text)) = lower(regexp_replace((e.customer_name)::text, '[^a-zA-Z0-9]'::text, ''::text, 'g'::text))) AND (( SELECT count(*) AS count
           FROM public.customer c2
          WHERE ((c2.parent_customer_id = g.customer_id) AND (COALESCE(c2.is_deleted, false) = false) AND ((c2.customer_status)::text <> 'Archived'::text))) = 1));


--
-- Name: v_sales_organisation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_sales_organisation AS
 WITH assignments AS (
         SELECT DISTINCT ON (pra.role_id, pra.people_id, pra.zone_id) pra.role_id,
            pra.people_id,
            pra.zone_id,
            pra.is_primary,
            r.role_name,
            lower((COALESCE(r.normalized_role_name, r.role_name))::text) AS role_key
           FROM (public.people_role_assignment pra
             JOIN public.role r ON ((r.role_id = pra.role_id)))
          WHERE ((upper((pra.assignment_status)::text) = 'ACTIVE'::text) AND (lower((COALESCE(r.normalized_role_name, r.role_name))::text) = ANY (ARRAY['ceo'::text, 'vp development'::text, 'large accounts director'::text, 'automotive director'::text, 'industry director'::text, 'marketing'::text, 'zone managers'::text, 'kam'::text])))
          ORDER BY pra.role_id, pra.people_id, pra.zone_id, pra.is_primary DESC, pra.assignment_id
        ), ceo AS (
         SELECT a.people_id,
            concat_ws(' '::text, p.first_name, p.name) AS person,
            p.email
           FROM (assignments a
             JOIN public.people p ON ((p.people_id = a.people_id)))
          WHERE (a.role_key = 'ceo'::text)
        ), vp AS (
         SELECT a.people_id,
            concat_ws(' '::text, p.first_name, p.name) AS person,
            p.email
           FROM (assignments a
             JOIN public.people p ON ((p.people_id = a.people_id)))
          WHERE (a.role_key = 'vp development'::text)
        )
 SELECT '-1'::integer AS depth,
    'CEO'::text AS role,
    NULL::character varying AS zone,
    ceo.person AS hierarchy,
    ceo.person,
    ceo.email,
    NULL::text AS reports_to,
    NULL::character varying AS reports_to_email,
    '-1'::text AS sort_path,
    ceo.people_id,
    NULL::integer AS reports_to_people_id
   FROM ceo
UNION ALL
 SELECT 0 AS depth,
    'VP Development'::text AS role,
    NULL::character varying AS zone,
    vp.person AS hierarchy,
    vp.person,
    vp.email,
    ceo.person AS reports_to,
    (ceo.email)::character varying AS reports_to_email,
    '0'::text AS sort_path,
    vp.people_id,
    ceo.people_id AS reports_to_people_id
   FROM (vp
     CROSS JOIN ceo)
UNION ALL
 SELECT 1 AS depth,
    (a.role_name)::text AS role,
    NULL::character varying AS zone,
    ((((('    '::text || (a.role_name)::text) || ' - '::text) || (p.first_name)::text) || ' '::text) || (p.name)::text) AS hierarchy,
    (((p.first_name)::text || ' '::text) || (p.name)::text) AS person,
    p.email,
    vp.person AS reports_to,
    (vp.email)::character varying AS reports_to_email,
        CASE a.role_key
            WHEN 'automotive director'::text THEN '0.1.automotive'::text
            WHEN 'industry director'::text THEN '0.2.industry'::text
            WHEN 'large accounts director'::text THEN '0.3.large_accounts'::text
            WHEN 'marketing'::text THEN '0.4.marketing'::text
            ELSE '0.9.direct'::text
        END AS sort_path,
    p.people_id,
    vp.people_id AS reports_to_people_id
   FROM ((assignments a
     JOIN public.people p ON ((p.people_id = a.people_id)))
     CROSS JOIN vp)
  WHERE (a.role_key = ANY (ARRAY['automotive director'::text, 'industry director'::text, 'large accounts director'::text, 'marketing'::text]))
UNION ALL
 SELECT 2 AS depth,
    'Zone manager'::text AS role,
    (z.zone_name)::character varying AS zone,
    ((((('        '::text || (z.zone_name)::text) || ' - '::text) || (p.first_name)::text) || ' '::text) || (p.name)::text) AS hierarchy,
    (((p.first_name)::text || ' '::text) || (p.name)::text) AS person,
    p.email,
    vp.person AS reports_to,
    (vp.email)::character varying AS reports_to_email,
    (('0.5.'::text || lpad((z.zone_id)::text, 2, '0'::text)) || '.0'::text) AS sort_path,
    p.people_id,
    vp.people_id AS reports_to_people_id
   FROM (((assignments a
     JOIN public.people p ON ((p.people_id = a.people_id)))
     JOIN public.zone z ON ((z.zone_id = a.zone_id)))
     CROSS JOIN vp)
  WHERE (a.role_key = 'zone managers'::text)
UNION ALL
 SELECT 3 AS depth,
    'KAM'::text AS role,
    (z.zone_name)::character varying AS zone,
    ((('            '::text || (p.first_name)::text) || ' '::text) || (p.name)::text) AS hierarchy,
    (((p.first_name)::text || ' '::text) || (p.name)::text) AS person,
    p.email,
    (((zm_p.first_name)::text || ' '::text) || (zm_p.name)::text) AS reports_to,
    (zm_p.email)::character varying AS reports_to_email,
    ((('0.5.'::text || lpad((z.zone_id)::text, 2, '0'::text)) || '.1.'::text) || (p.name)::text) AS sort_path,
    p.people_id,
    zm_p.people_id AS reports_to_people_id
   FROM ((((assignments a
     JOIN public.people p ON ((p.people_id = a.people_id)))
     JOIN public.zone z ON ((z.zone_id = a.zone_id)))
     LEFT JOIN assignments zm ON (((zm.role_key = 'zone managers'::text) AND (zm.zone_id = a.zone_id))))
     LEFT JOIN public.people zm_p ON ((zm_p.people_id = zm.people_id)))
  WHERE (a.role_key = 'kam'::text)
  ORDER BY 9;


--
-- Name: v_subject_tree; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_subject_tree AS
 WITH RECURSIVE cte AS (
         SELECT s.subject_id,
            s.parent_subject_id,
            s.subject_name,
            s.subject_explanation,
            s.status,
            0 AS depth,
            (s.subject_name)::text AS full_path,
            ARRAY[s.subject_id] AS id_path
           FROM public.subject s
          WHERE (s.parent_subject_id IS NULL)
        UNION ALL
         SELECT s.subject_id,
            s.parent_subject_id,
            s.subject_name,
            s.subject_explanation,
            s.status,
            (cte_1.depth + 1),
            ((cte_1.full_path || ' > '::text) || (s.subject_name)::text),
            (cte_1.id_path || s.subject_id)
           FROM (public.subject s
             JOIN cte cte_1 ON ((s.parent_subject_id = cte_1.subject_id)))
          WHERE (NOT (s.subject_id = ANY (cte_1.id_path)))
        )
 SELECT subject_id,
    parent_subject_id,
    subject_name,
    subject_explanation,
    status,
    depth,
    full_path,
    id_path,
    (NOT (EXISTS ( SELECT 1
           FROM public.subject ch
          WHERE (ch.parent_subject_id = cte.subject_id)))) AS is_leaf,
    ( SELECT count(*) AS count
           FROM public.subject_kpi sk
          WHERE (sk.subject_id = cte.subject_id)) AS kpi_count_direct,
    ( SELECT count(DISTINCT sk2.kpi_id) AS count
           FROM (public.subject_kpi sk2
             JOIN public.subject s2 ON ((s2.subject_id = sk2.subject_id)))
          WHERE ((s2.subject_id = cte.subject_id) OR (cte.subject_id IN ( SELECT subject.subject_id
                   FROM public.subject
                  WHERE (subject.parent_subject_id = cte.subject_id))))) AS kpi_count_total
   FROM cte;


--
-- Name: VIEW v_subject_tree; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_subject_tree IS 'Domaines KPI hiérarchiques avec comptage.
kpi_count_direct : KPIs directement liés à ce sujet.
kpi_count_total  : KPIs incluant les sous-sujets.';


--
-- Name: v_training_dashboard; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_training_dashboard AS
 SELECT r.result_id,
    r.kpi_id,
    k.kpi_name,
    r.people_id,
    (((p.name)::text || ' '::text) || (p.first_name)::text) AS person_name,
    p.email,
    t.training_name,
    tst.test_name,
    r.training_sent_at,
    r.training_completed_at,
        CASE
            WHEN (r.training_completed_at IS NOT NULL) THEN 'Done'::text
            ELSE 'Pending'::text
        END AS training_status,
    r.test_sent_at,
    r.test_completed_at,
    r.test_due_date,
    r.score,
    r.passed,
    r.escalation_level,
    r.status,
        CASE
            WHEN (r.test_completed_at IS NULL) THEN (0)::numeric
            WHEN (r.score < (80)::numeric) THEN 2.5
            ELSE (5)::numeric
        END AS training_score,
    NULL::numeric AS kpi_performance_score,
        CASE
            WHEN (r.test_completed_at IS NULL) THEN (0)::numeric
            WHEN (r.score < (80)::numeric) THEN 2.5
            ELSE (5)::numeric
        END AS global_score
   FROM ((((public.kpi_training_result r
     JOIN public.kpi k ON ((k.kpi_id = r.kpi_id)))
     JOIN public.people p ON ((p.people_id = r.people_id)))
     JOIN public.kpi_training t ON ((t.training_id = r.training_id)))
     LEFT JOIN public.kpi_test tst ON ((tst.test_id = r.test_id)));


--
-- Name: VIEW v_training_dashboard; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_training_dashboard IS 'Main dashboard view. training_score + kpi_performance_score = global_score (0–10).
     kpi_performance_score must be updated separately from kpi_result analysis.';


--
-- Name: zone_zone_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.zone ALTER COLUMN zone_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.zone_zone_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: anomalies; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.anomalies (
    anomaly_id integer NOT NULL,
    severity text,
    category text,
    stg_id integer,
    detail text,
    created_at timestamp without time zone DEFAULT now(),
    CONSTRAINT anomalies_severity_check CHECK ((severity = ANY (ARRAY['CRITIQUE'::text, 'HAUTE'::text, 'MOYENNE'::text, 'FAIBLE'::text])))
);


--
-- Name: anomalies_anomaly_id_seq; Type: SEQUENCE; Schema: stg; Owner: -
--

CREATE SEQUENCE stg.anomalies_anomaly_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: anomalies_anomaly_id_seq; Type: SEQUENCE OWNED BY; Schema: stg; Owner: -
--

ALTER SEQUENCE stg.anomalies_anomaly_id_seq OWNED BY stg.anomalies.anomaly_id;


--
-- Name: assignments_ended; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.assignments_ended (
    assignment_id bigint NOT NULL,
    old_status text,
    old_end_date date,
    ended_at timestamp without time zone DEFAULT now()
);


--
-- Name: assignments_inserted; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.assignments_inserted (
    assignment_id bigint NOT NULL
);


--
-- Name: cleanup_backup; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.cleanup_backup (
    assignment_id bigint NOT NULL,
    people_id integer,
    role_id integer,
    is_primary boolean,
    allocation numeric,
    assignment_status text,
    action text,
    saved_at timestamp with time zone DEFAULT now()
);


--
-- Name: cleanup_target; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.cleanup_target (
    assignment_id bigint NOT NULL,
    people_id integer,
    role_id integer,
    unit_id integer,
    old_status text,
    saved_at timestamp with time zone DEFAULT now()
);


--
-- Name: employees; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.employees (
    stg_id integer NOT NULL,
    src_sheet text NOT NULL,
    src_row integer,
    matricule text,
    gender text,
    last_name text,
    first_name text,
    email_raw text,
    email_clean text,
    email_dup_flag boolean DEFAULT false,
    date_of_birth date,
    hire_date date,
    site_raw text,
    unit_id integer,
    job_title text,
    classification text,
    department text,
    line_manager_raw text,
    functional_manager_raw text,
    role_ceo_raw text,
    role_1 text,
    role_2 text,
    role1_in_tree boolean,
    role2_in_tree boolean,
    grade_ceo_raw text,
    excluded boolean DEFAULT false,
    exclusion_reason text,
    ln_norm text,
    fn_norm text,
    lm_norm text,
    fm_norm text,
    matched_people_id integer,
    match_method text,
    CONSTRAINT employees_gender_check CHECK (((gender = ANY (ARRAY['Male'::text, 'Female'::text])) OR (gender IS NULL)))
);


--
-- Name: employees_stg_id_seq; Type: SEQUENCE; Schema: stg; Owner: -
--

CREATE SEQUENCE stg.employees_stg_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: employees_stg_id_seq; Type: SEQUENCE OWNED BY; Schema: stg; Owner: -
--

ALTER SEQUENCE stg.employees_stg_id_seq OWNED BY stg.employees.stg_id;


--
-- Name: import_people_map; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.import_people_map (
    src_sheet text NOT NULL,
    src_row integer NOT NULL,
    people_id integer NOT NULL,
    mapped_at timestamp without time zone DEFAULT now()
);


--
-- Name: kpi_assignments_inserted; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_assignments_inserted (
    assignment_id bigint NOT NULL
);


--
-- Name: kpi_divergences; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_divergences (
    stg_id integer NOT NULL,
    people_id integer,
    employe text,
    role_rh text,
    roles_actuels text,
    decision text DEFAULT 'A ARBITRER'::text
);


--
-- Name: kpi_field_fills; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_field_fills (
    tbl text NOT NULL,
    pk bigint NOT NULL,
    col text NOT NULL,
    filled_value text,
    filled_at timestamp with time zone DEFAULT now()
);


--
-- Name: kpi_layer_snapshot; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_layer_snapshot (
    n_kpi bigint,
    n_alloc bigint,
    sum_targets numeric,
    n_result bigint,
    n_training bigint,
    n_monitoring bigint,
    snap_at timestamp with time zone
);


--
-- Name: kpi_mgr; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_mgr (
    stg_id integer,
    lm_people_id integer,
    fm_people_id integer
);


--
-- Name: kpi_people_inserted; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_people_inserted (
    people_id integer NOT NULL
);


--
-- Name: kpi_people_map; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_people_map (
    src_sheet text NOT NULL,
    src_row integer NOT NULL,
    people_id integer,
    method text
);


--
-- Name: kpi_people_snapshot; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_people_snapshot (
    people_id integer,
    name character varying(255),
    first_name character varying(255),
    seniority integer,
    email character varying(255),
    cell_phone character varying(50),
    paid_by_unit_id integer,
    work_at_unit_id integer,
    native_language character varying(100),
    other_languages character varying(255),
    other_information text,
    status character varying(50),
    comments text,
    hr_employee_id character varying(50),
    hr_site character varying(100),
    gender character varying(10),
    hire_date date,
    date_of_birth date
);


--
-- Name: kpi_pra_snapshot; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_pra_snapshot (
    assignment_id bigint,
    people_id bigint,
    role_id bigint,
    unit_id bigint,
    zone_id bigint,
    market_id bigint,
    assignment_type character varying(50),
    allocation_percentage numeric(5,2),
    is_primary boolean,
    functional_manager_role_id bigint,
    assistant_context text,
    knowledge_context text,
    start_date date,
    end_date date,
    assignment_status character varying(50),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    product_line_id integer,
    reports_to integer,
    target_role_label character varying(80),
    functional_manager_assignment_id bigint,
    admin_manager_assignment_id integer
);


--
-- Name: kpi_primary; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_primary (
    people_id bigint,
    assignment_id bigint
);


--
-- Name: kpi_role_alias; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_role_alias (
    ref_norm text,
    existing_norm text
);


--
-- Name: kpi_role_match; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_role_match (
    ref_name text,
    ref_norm text,
    existing_role_id bigint
);


--
-- Name: kpi_roles_inserted; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_roles_inserted (
    role_id integer NOT NULL,
    role_name text
);


--
-- Name: kpi_targets; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.kpi_targets (
    stg_id integer,
    people_id integer,
    role_id bigint,
    is_primary boolean,
    allocation_percentage numeric,
    unit_id integer,
    target_role_label text
);


--
-- Name: people_inserted; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.people_inserted (
    people_id integer NOT NULL
);


--
-- Name: roles_ref; Type: TABLE; Schema: stg; Owner: -
--

CREATE TABLE stg.roles_ref (
    role_name text NOT NULL,
    grade text NOT NULL,
    ceo_level text NOT NULL,
    parent_role_name text,
    pole text NOT NULL,
    scope text NOT NULL,
    sort_order integer,
    CONSTRAINT roles_ref_grade_check CHECK ((grade = ANY (ARRAY['G1'::text, 'G2'::text, 'G3'::text, 'G4'::text, 'G5'::text, 'G6'::text, 'G7'::text]))),
    CONSTRAINT roles_ref_scope_check CHECK ((scope = ANY (ARRAY['Groupe'::text, 'Usine'::text])))
);


--
-- Name: hr_kpi_identity_map; Type: TABLE; Schema: sync; Owner: -
--

CREATE TABLE sync.hr_kpi_identity_map (
    matricule character varying(50) NOT NULL,
    people_id integer NOT NULL,
    match_method text NOT NULL,
    matched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hr_kpi_identity_unmatched; Type: TABLE; Schema: sync; Owner: -
--

CREATE TABLE sync.hr_kpi_identity_unmatched (
    id bigint NOT NULL,
    matricule character varying(50),
    hr_name text,
    hr_email text,
    reason text,
    flagged_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hr_kpi_identity_unmatched_id_seq; Type: SEQUENCE; Schema: sync; Owner: -
--

CREATE SEQUENCE sync.hr_kpi_identity_unmatched_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hr_kpi_identity_unmatched_id_seq; Type: SEQUENCE OWNED BY; Schema: sync; Owner: -
--

ALTER SEQUENCE sync.hr_kpi_identity_unmatched_id_seq OWNED BY sync.hr_kpi_identity_unmatched.id;


--
-- Name: corrective_action_escalation_log escalation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.corrective_action_escalation_log ALTER COLUMN escalation_id SET DEFAULT nextval('public.corrective_action_escalation_log_escalation_id_seq'::regclass);


--
-- Name: ext_agent_artifact agent_artifact_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_artifact ALTER COLUMN agent_artifact_id SET DEFAULT nextval('public.ext_agent_artifact_agent_artifact_id_seq'::regclass);


--
-- Name: ext_agent_build_project agent_project_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_project ALTER COLUMN agent_project_id SET DEFAULT nextval('public.ext_agent_build_project_agent_project_id_seq'::regclass);


--
-- Name: ext_agent_build_step agent_build_step_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_step ALTER COLUMN agent_build_step_id SET DEFAULT nextval('public.ext_agent_build_step_agent_build_step_id_seq'::regclass);


--
-- Name: ext_agent_build_step_v2 agent_build_step_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_step_v2 ALTER COLUMN agent_build_step_id SET DEFAULT nextval('public.ext_agent_build_step_v2_agent_build_step_id_seq'::regclass);


--
-- Name: ext_agent_build_step_v3 agent_step_revision_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_step_v3 ALTER COLUMN agent_step_revision_id SET DEFAULT nextval('public.ext_agent_build_step_v3_agent_step_revision_id_seq'::regclass);


--
-- Name: ext_agent_definition agent_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_definition ALTER COLUMN agent_id SET DEFAULT nextval('public.ext_agent_definition_agent_id_seq'::regclass);


--
-- Name: ext_agent_factory_prompt factory_prompt_step_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_prompt ALTER COLUMN factory_prompt_step_id SET DEFAULT nextval('public.ext_agent_factory_prompt_factory_prompt_step_id_seq'::regclass);


--
-- Name: ext_agent_factory_prompt_v2 factory_prompt_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_prompt_v2 ALTER COLUMN factory_prompt_id SET DEFAULT nextval('public.ext_agent_factory_prompt_v2_factory_prompt_id_seq'::regclass);


--
-- Name: ext_agent_factory_prompt_v3 factory_prompt_revision_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_prompt_v3 ALTER COLUMN factory_prompt_revision_id SET DEFAULT nextval('public.ext_agent_factory_prompt_v3_factory_prompt_revision_id_seq'::regclass);


--
-- Name: ext_agent_factory_question question_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_question ALTER COLUMN question_id SET DEFAULT nextval('public.ext_agent_factory_question_question_id_seq'::regclass);


--
-- Name: ext_tabledetest id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_tabledetest ALTER COLUMN id SET DEFAULT nextval('public.ext_tabledetest_id_seq'::regclass);


--
-- Name: kpi_app_assistant_conversation conversation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_assistant_conversation ALTER COLUMN conversation_id SET DEFAULT nextval('public.kpi_app_assistant_conversation_conversation_id_seq'::regclass);


--
-- Name: kpi_app_auth_event event_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_auth_event ALTER COLUMN event_id SET DEFAULT nextval('public.kpi_app_auth_event_event_id_seq'::regclass);


--
-- Name: kpi_app_change_log change_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_change_log ALTER COLUMN change_id SET DEFAULT nextval('public.kpi_app_change_log_change_id_seq'::regclass);


--
-- Name: kpi_app_intake_row intake_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_intake_row ALTER COLUMN intake_id SET DEFAULT nextval('public.kpi_app_intake_row_intake_id_seq'::regclass);


--
-- Name: kpi_app_password_token token_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_password_token ALTER COLUMN token_id SET DEFAULT nextval('public.kpi_app_password_token_token_id_seq'::regclass);


--
-- Name: kpi_app_session session_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_session ALTER COLUMN session_id SET DEFAULT nextval('public.kpi_app_session_session_id_seq'::regclass);


--
-- Name: kpi_app_user user_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_user ALTER COLUMN user_id SET DEFAULT nextval('public.kpi_app_user_user_id_seq'::regclass);


--
-- Name: kpi_assistant_conversation conversation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_assistant_conversation ALTER COLUMN conversation_id SET DEFAULT nextval('public.kpi_assistant_conversation_conversation_id_seq'::regclass);


--
-- Name: kpi_audit audit_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_audit ALTER COLUMN audit_id SET DEFAULT nextval('public.kpi_audit_audit_id_seq'::regclass);


--
-- Name: kpi_submission_email_dispatch_log dispatch_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_email_dispatch_log ALTER COLUMN dispatch_id SET DEFAULT nextval('public.kpi_submission_email_dispatch_log_dispatch_id_seq'::regclass);


--
-- Name: kpi_submission_reminder_log reminder_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_reminder_log ALTER COLUMN reminder_id SET DEFAULT nextval('public.kpi_submission_reminder_log_reminder_id_seq'::regclass);


--
-- Name: kpi_target_adjustment_proposal proposal_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_adjustment_proposal ALTER COLUMN proposal_id SET DEFAULT nextval('public.kpi_target_adjustment_proposal_proposal_id_seq'::regclass);


--
-- Name: kpi_target_allocation_audit audit_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation_audit ALTER COLUMN audit_id SET DEFAULT nextval('public.kpi_target_allocation_audit_audit_id_seq'::regclass);


--
-- Name: kpi_training_escalation_log training_escalation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_escalation_log ALTER COLUMN training_escalation_id SET DEFAULT nextval('public.kpi_training_escalation_log_training_escalation_id_seq'::regclass);


--
-- Name: people_role_assignment assignment_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment ALTER COLUMN assignment_id SET DEFAULT nextval('public.people_role_assignment_assignment_id_seq'::regclass);


--
-- Name: role role_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role ALTER COLUMN role_id SET DEFAULT nextval('public.role_role_id_seq1'::regclass);


--
-- Name: role_reporting_relationship relationship_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship ALTER COLUMN relationship_id SET DEFAULT nextval('public.role_reporting_relationship_relationship_id_seq'::regclass);


--
-- Name: unit_factory_cost_change_log change_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit_factory_cost_change_log ALTER COLUMN change_id SET DEFAULT nextval('public.unit_factory_cost_change_log_change_id_seq'::regclass);


--
-- Name: unit_type unit_type_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit_type ALTER COLUMN unit_type_id SET DEFAULT nextval('public.unit_type_unit_type_id_seq'::regclass);


--
-- Name: user_auth auth_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_auth ALTER COLUMN auth_id SET DEFAULT nextval('public.user_auth_auth_id_seq'::regclass);


--
-- Name: anomalies anomaly_id; Type: DEFAULT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.anomalies ALTER COLUMN anomaly_id SET DEFAULT nextval('stg.anomalies_anomaly_id_seq'::regclass);


--
-- Name: employees stg_id; Type: DEFAULT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.employees ALTER COLUMN stg_id SET DEFAULT nextval('stg.employees_stg_id_seq'::regclass);


--
-- Name: hr_kpi_identity_unmatched id; Type: DEFAULT; Schema: sync; Owner: -
--

ALTER TABLE ONLY sync.hr_kpi_identity_unmatched ALTER COLUMN id SET DEFAULT nextval('sync.hr_kpi_identity_unmatched_id_seq'::regclass);


--
-- Name: corrective_action_escalation_log corrective_action_escalation_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.corrective_action_escalation_log
    ADD CONSTRAINT corrective_action_escalation_log_pkey PRIMARY KEY (escalation_id);


--
-- Name: corrective_action_escalation_log corrective_action_escalation_log_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.corrective_action_escalation_log
    ADD CONSTRAINT corrective_action_escalation_log_unique UNIQUE (corrective_action_id, escalation_stage);


--
-- Name: customer_delivery_unit customer_delivery_unit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_delivery_unit
    ADD CONSTRAINT customer_delivery_unit_pkey PRIMARY KEY (customer_delivery_id);


--
-- Name: customer_document_access customer_document_access_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_access
    ADD CONSTRAINT customer_document_access_pkey PRIMARY KEY (customer_id, principal_id);


--
-- Name: customer_document_attachment customer_document_attachment_blob_container_blob_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_attachment
    ADD CONSTRAINT customer_document_attachment_blob_container_blob_name_key UNIQUE (blob_container, blob_name);


--
-- Name: customer_document_attachment customer_document_attachment_customer_id_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_attachment
    ADD CONSTRAINT customer_document_attachment_customer_id_request_key_key UNIQUE (customer_id, request_key);


--
-- Name: customer_document_attachment customer_document_attachment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_attachment
    ADD CONSTRAINT customer_document_attachment_pkey PRIMARY KEY (attachment_id);


--
-- Name: customer_document_event customer_document_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_event
    ADD CONSTRAINT customer_document_event_pkey PRIMARY KEY (event_id);


--
-- Name: customer_dossier customer_dossier_customer_id_dossier_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier
    ADD CONSTRAINT customer_dossier_customer_id_dossier_type_key UNIQUE (customer_id, dossier_type);


--
-- Name: customer_dossier customer_dossier_dossier_id_customer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier
    ADD CONSTRAINT customer_dossier_dossier_id_customer_id_key UNIQUE (dossier_id, customer_id);


--
-- Name: customer_dossier customer_dossier_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier
    ADD CONSTRAINT customer_dossier_pkey PRIMARY KEY (dossier_id);


--
-- Name: customer_dossier_revision customer_dossier_revision_customer_id_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier_revision
    ADD CONSTRAINT customer_dossier_revision_customer_id_request_key_key UNIQUE (customer_id, request_key);


--
-- Name: customer_dossier_revision customer_dossier_revision_dossier_id_revision_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier_revision
    ADD CONSTRAINT customer_dossier_revision_dossier_id_revision_no_key UNIQUE (dossier_id, revision_no);


--
-- Name: customer_dossier_revision customer_dossier_revision_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier_revision
    ADD CONSTRAINT customer_dossier_revision_pkey PRIMARY KEY (revision_id);


--
-- Name: customer_dossier_revision customer_dossier_revision_revision_id_customer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier_revision
    ADD CONSTRAINT customer_dossier_revision_revision_id_customer_id_key UNIQUE (revision_id, customer_id);


--
-- Name: customer_file customer_file_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_file
    ADD CONSTRAINT customer_file_pkey PRIMARY KEY (customer_file_id);


--
-- Name: customer_master_api_audit customer_master_api_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_master_api_audit
    ADD CONSTRAINT customer_master_api_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: customer customer_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_pkey PRIMARY KEY (customer_id);


--
-- Name: customer_project customer_project_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_project
    ADD CONSTRAINT customer_project_pkey PRIMARY KEY (customer_project_id);


--
-- Name: customer_rfq_link customer_rfq_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_rfq_link
    ADD CONSTRAINT customer_rfq_link_pkey PRIMARY KEY (rfq_id);


--
-- Name: customer_rfq_link customer_rfq_link_rfq_id_customer_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_rfq_link
    ADD CONSTRAINT customer_rfq_link_rfq_id_customer_id_key UNIQUE (rfq_id, customer_id);


--
-- Name: customer_visit_attachment customer_visit_attachment_blob_container_blob_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_attachment
    ADD CONSTRAINT customer_visit_attachment_blob_container_blob_name_key UNIQUE (blob_container, blob_name);


--
-- Name: customer_visit_attachment customer_visit_attachment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_attachment
    ADD CONSTRAINT customer_visit_attachment_pkey PRIMARY KEY (attachment_id);


--
-- Name: customer_visit_attachment customer_visit_attachment_uploaded_by_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_attachment
    ADD CONSTRAINT customer_visit_attachment_uploaded_by_request_key_key UNIQUE (uploaded_by, request_key);


--
-- Name: customer_visit_event customer_visit_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_event
    ADD CONSTRAINT customer_visit_event_pkey PRIMARY KEY (event_id);


--
-- Name: customer_visit_report customer_visit_report_created_by_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_report
    ADD CONSTRAINT customer_visit_report_created_by_request_key_key UNIQUE (created_by, request_key);


--
-- Name: customer_visit_report customer_visit_report_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_report
    ADD CONSTRAINT customer_visit_report_pkey PRIMARY KEY (visit_report_id);


--
-- Name: ext_agent_artifact ext_agent_artifact_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_artifact
    ADD CONSTRAINT ext_agent_artifact_pkey PRIMARY KEY (agent_artifact_id);


--
-- Name: ext_agent_build_project ext_agent_build_project_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_project
    ADD CONSTRAINT ext_agent_build_project_pkey PRIMARY KEY (agent_project_id);


--
-- Name: ext_agent_build_step ext_agent_build_step_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_step
    ADD CONSTRAINT ext_agent_build_step_pkey PRIMARY KEY (agent_build_step_id);


--
-- Name: ext_agent_build_step_v2 ext_agent_build_step_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_step_v2
    ADD CONSTRAINT ext_agent_build_step_v2_pkey PRIMARY KEY (agent_build_step_id);


--
-- Name: ext_agent_build_step_v3 ext_agent_build_step_v3_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_build_step_v3
    ADD CONSTRAINT ext_agent_build_step_v3_pkey PRIMARY KEY (agent_step_revision_id);


--
-- Name: ext_agent_definition ext_agent_definition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_definition
    ADD CONSTRAINT ext_agent_definition_pkey PRIMARY KEY (agent_id);


--
-- Name: ext_agent_factory_prompt ext_agent_factory_prompt_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_prompt
    ADD CONSTRAINT ext_agent_factory_prompt_pkey PRIMARY KEY (factory_prompt_step_id);


--
-- Name: ext_agent_factory_prompt_v2 ext_agent_factory_prompt_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_prompt_v2
    ADD CONSTRAINT ext_agent_factory_prompt_v2_pkey PRIMARY KEY (factory_prompt_id);


--
-- Name: ext_agent_factory_prompt_v3 ext_agent_factory_prompt_v3_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_prompt_v3
    ADD CONSTRAINT ext_agent_factory_prompt_v3_pkey PRIMARY KEY (factory_prompt_revision_id);


--
-- Name: ext_agent_factory_question ext_agent_factory_question_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_factory_question
    ADD CONSTRAINT ext_agent_factory_question_pkey PRIMARY KEY (question_id);


--
-- Name: ext_customer_master_csv ext_customer_master_csv_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_customer_master_csv
    ADD CONSTRAINT ext_customer_master_csv_pkey PRIMARY KEY (customer_code);


--
-- Name: ext_group_merge_map ext_group_merge_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_group_merge_map
    ADD CONSTRAINT ext_group_merge_map_pkey PRIMARY KEY (source_group_id);


--
-- Name: ext_kam_name_map ext_kam_name_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_kam_name_map
    ADD CONSTRAINT ext_kam_name_map_pkey PRIMARY KEY (csv_kam_name);


--
-- Name: ext_site_unit_map ext_site_unit_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_site_unit_map
    ADD CONSTRAINT ext_site_unit_map_pkey PRIMARY KEY (csv_site);


--
-- Name: ext_tabledetest ext_tabledetest_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_tabledetest
    ADD CONSTRAINT ext_tabledetest_pkey PRIMARY KEY (id);


--
-- Name: kpi_action_link kpi_action_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_action_link
    ADD CONSTRAINT kpi_action_link_pkey PRIMARY KEY (link_id);


--
-- Name: kpi_app_assistant_conversation kpi_app_assistant_conversation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_assistant_conversation
    ADD CONSTRAINT kpi_app_assistant_conversation_pkey PRIMARY KEY (conversation_id);


--
-- Name: kpi_app_auth_event kpi_app_auth_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_auth_event
    ADD CONSTRAINT kpi_app_auth_event_pkey PRIMARY KEY (event_id);


--
-- Name: kpi_app_change_log kpi_app_change_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_change_log
    ADD CONSTRAINT kpi_app_change_log_pkey PRIMARY KEY (change_id);


--
-- Name: kpi_app_intake_row kpi_app_intake_row_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_intake_row
    ADD CONSTRAINT kpi_app_intake_row_pkey PRIMARY KEY (intake_id);


--
-- Name: kpi_app_password_token kpi_app_password_token_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_password_token
    ADD CONSTRAINT kpi_app_password_token_pkey PRIMARY KEY (token_id);


--
-- Name: kpi_app_password_token kpi_app_password_token_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_password_token
    ADD CONSTRAINT kpi_app_password_token_token_hash_key UNIQUE (token_hash);


--
-- Name: kpi_app_session kpi_app_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_session
    ADD CONSTRAINT kpi_app_session_pkey PRIMARY KEY (session_id);


--
-- Name: kpi_app_session kpi_app_session_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_session
    ADD CONSTRAINT kpi_app_session_token_hash_key UNIQUE (token_hash);


--
-- Name: kpi_app_setting kpi_app_setting_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_setting
    ADD CONSTRAINT kpi_app_setting_pkey PRIMARY KEY (setting_key);


--
-- Name: kpi_app_user kpi_app_user_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_user
    ADD CONSTRAINT kpi_app_user_email_key UNIQUE (email);


--
-- Name: kpi_app_user kpi_app_user_people_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_user
    ADD CONSTRAINT kpi_app_user_people_id_key UNIQUE (people_id);


--
-- Name: kpi_app_user kpi_app_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_user
    ADD CONSTRAINT kpi_app_user_pkey PRIMARY KEY (user_id);


--
-- Name: kpi_assistant_conversation kpi_assistant_conversation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_assistant_conversation
    ADD CONSTRAINT kpi_assistant_conversation_pkey PRIMARY KEY (conversation_id);


--
-- Name: kpi_audit kpi_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_audit
    ADD CONSTRAINT kpi_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: kpi_knowledge_refresh_queue kpi_knowledge_refresh_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_knowledge_refresh_queue
    ADD CONSTRAINT kpi_knowledge_refresh_queue_pkey PRIMARY KEY (kpi_id);


--
-- Name: kpi_monitoring_history kpi_monitoring_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_history
    ADD CONSTRAINT kpi_monitoring_history_pkey PRIMARY KEY (history_id);


--
-- Name: kpi_monitoring_status kpi_monitoring_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_status
    ADD CONSTRAINT kpi_monitoring_status_pkey PRIMARY KEY (monitoring_status_id);


--
-- Name: kpi kpi_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi
    ADD CONSTRAINT kpi_pkey PRIMARY KEY (kpi_id);


--
-- Name: kpi_relationship kpi_relationship_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_relationship
    ADD CONSTRAINT kpi_relationship_pkey PRIMARY KEY (relationship_id);


--
-- Name: kpi_result kpi_result_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_result
    ADD CONSTRAINT kpi_result_pkey PRIMARY KEY (kpi_result_id);


--
-- Name: kpi_submission_email_dispatch_log kpi_submission_email_dispatch_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_email_dispatch_log
    ADD CONSTRAINT kpi_submission_email_dispatch_log_pkey PRIMARY KEY (dispatch_id);


--
-- Name: kpi_submission_reminder_log kpi_submission_reminder_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_reminder_log
    ADD CONSTRAINT kpi_submission_reminder_log_pkey PRIMARY KEY (reminder_id);


--
-- Name: kpi_target_adjustment_proposal kpi_target_adjustment_proposal_approval_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_adjustment_proposal
    ADD CONSTRAINT kpi_target_adjustment_proposal_approval_token_hash_key UNIQUE (approval_token_hash);


--
-- Name: kpi_target_adjustment_proposal kpi_target_adjustment_proposal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_adjustment_proposal
    ADD CONSTRAINT kpi_target_adjustment_proposal_pkey PRIMARY KEY (proposal_id);


--
-- Name: kpi_target_allocation_audit kpi_target_allocation_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation_audit
    ADD CONSTRAINT kpi_target_allocation_audit_pkey PRIMARY KEY (audit_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_pkey PRIMARY KEY (kpi_target_allocation_id);


--
-- Name: kpi_test kpi_test_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_test
    ADD CONSTRAINT kpi_test_pkey PRIMARY KEY (test_id);


--
-- Name: kpi_training_escalation_log kpi_training_escalation_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_escalation_log
    ADD CONSTRAINT kpi_training_escalation_log_pkey PRIMARY KEY (training_escalation_id);


--
-- Name: kpi_training kpi_training_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training
    ADD CONSTRAINT kpi_training_pkey PRIMARY KEY (training_id);


--
-- Name: kpi_training_result kpi_training_result_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_result
    ADD CONSTRAINT kpi_training_result_pkey PRIMARY KEY (result_id);


--
-- Name: manufacturing_strategy manufacturing_strategy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturing_strategy
    ADD CONSTRAINT manufacturing_strategy_pkey PRIMARY KEY (manufacturing_strategy_id);


--
-- Name: market market_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.market
    ADD CONSTRAINT market_pkey PRIMARY KEY (market_id);


--
-- Name: people people_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_email_key UNIQUE (email);


--
-- Name: people people_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_pkey PRIMARY KEY (people_id);


--
-- Name: people_role_assignment people_role_assignment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT people_role_assignment_pkey PRIMARY KEY (assignment_id);


--
-- Name: product_line product_line_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_line
    ADD CONSTRAINT product_line_pkey PRIMARY KEY (product_line_id);


--
-- Name: product_line product_line_product_line_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product_line
    ADD CONSTRAINT product_line_product_line_name_key UNIQUE (product_line_name);


--
-- Name: product product_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product
    ADD CONSTRAINT product_pkey PRIMARY KEY (product_id);


--
-- Name: role role_pkey1; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT role_pkey1 PRIMARY KEY (role_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_pkey PRIMARY KEY (relationship_id);


--
-- Name: status status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.status
    ADD CONSTRAINT status_pkey PRIMARY KEY (status_id);


--
-- Name: subject_kpi subject_kpi_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_kpi
    ADD CONSTRAINT subject_kpi_pkey PRIMARY KEY (subject_kpi_id);


--
-- Name: subject subject_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject
    ADD CONSTRAINT subject_pkey PRIMARY KEY (subject_id);


--
-- Name: unit_factory_cost_change_log unit_factory_cost_change_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit_factory_cost_change_log
    ADD CONSTRAINT unit_factory_cost_change_log_pkey PRIMARY KEY (change_id);


--
-- Name: unit unit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit
    ADD CONSTRAINT unit_pkey PRIMARY KEY (unit_id);


--
-- Name: unit_type unit_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit_type
    ADD CONSTRAINT unit_type_pkey PRIMARY KEY (unit_type_id);


--
-- Name: unit_type unit_type_unit_type_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit_type
    ADD CONSTRAINT unit_type_unit_type_name_key UNIQUE (unit_type_name);


--
-- Name: kpi_action_link uq_kal_action_kpi; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_action_link
    ADD CONSTRAINT uq_kal_action_kpi UNIQUE (action_id, kpi_id, kpi_target_allocation_id);


--
-- Name: kpi_monitoring_status uq_kpi_monitoring_status_kpi_alloc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_status
    ADD CONSTRAINT uq_kpi_monitoring_status_kpi_alloc UNIQUE (kpi_id, kpi_target_allocation_id);


--
-- Name: kpi_submission_reminder_log uq_kpi_submission_reminder_log_stage; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_reminder_log
    ADD CONSTRAINT uq_kpi_submission_reminder_log_stage UNIQUE (people_id, frequency_label, target_week, stage);


--
-- Name: kpi_training_escalation_log uq_kpi_training_escalation_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_escalation_log
    ADD CONSTRAINT uq_kpi_training_escalation_log UNIQUE (result_id, escalation_stage, sent_to_email);


--
-- Name: kpi_relationship uq_kr_triplet; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_relationship
    ADD CONSTRAINT uq_kr_triplet UNIQUE (source_kpi_id, target_kpi_id, relationship_type);


--
-- Name: role uq_normalized_role_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT uq_normalized_role_name UNIQUE (normalized_role_name);


--
-- Name: people_role_assignment uq_pra_assignment_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT uq_pra_assignment_id UNIQUE (assignment_id);


--
-- Name: role uq_role_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT uq_role_name UNIQUE (role_name);


--
-- Name: user_auth user_auth_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_auth
    ADD CONSTRAINT user_auth_email_key UNIQUE (email);


--
-- Name: user_auth user_auth_people_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_auth
    ADD CONSTRAINT user_auth_people_id_key UNIQUE (people_id);


--
-- Name: user_auth user_auth_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_auth
    ADD CONSTRAINT user_auth_pkey PRIMARY KEY (auth_id);


--
-- Name: zone zone_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.zone
    ADD CONSTRAINT zone_pkey PRIMARY KEY (zone_id);


--
-- Name: anomalies anomalies_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.anomalies
    ADD CONSTRAINT anomalies_pkey PRIMARY KEY (anomaly_id);


--
-- Name: assignments_ended assignments_ended_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.assignments_ended
    ADD CONSTRAINT assignments_ended_pkey PRIMARY KEY (assignment_id);


--
-- Name: assignments_inserted assignments_inserted_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.assignments_inserted
    ADD CONSTRAINT assignments_inserted_pkey PRIMARY KEY (assignment_id);


--
-- Name: cleanup_backup cleanup_backup_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.cleanup_backup
    ADD CONSTRAINT cleanup_backup_pkey PRIMARY KEY (assignment_id);


--
-- Name: cleanup_target cleanup_target_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.cleanup_target
    ADD CONSTRAINT cleanup_target_pkey PRIMARY KEY (assignment_id);


--
-- Name: employees employees_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.employees
    ADD CONSTRAINT employees_pkey PRIMARY KEY (stg_id);


--
-- Name: import_people_map import_people_map_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.import_people_map
    ADD CONSTRAINT import_people_map_pkey PRIMARY KEY (src_sheet, src_row);


--
-- Name: kpi_assignments_inserted kpi_assignments_inserted_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.kpi_assignments_inserted
    ADD CONSTRAINT kpi_assignments_inserted_pkey PRIMARY KEY (assignment_id);


--
-- Name: kpi_divergences kpi_divergences_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.kpi_divergences
    ADD CONSTRAINT kpi_divergences_pkey PRIMARY KEY (stg_id);


--
-- Name: kpi_field_fills kpi_field_fills_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.kpi_field_fills
    ADD CONSTRAINT kpi_field_fills_pkey PRIMARY KEY (tbl, pk, col);


--
-- Name: kpi_people_inserted kpi_people_inserted_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.kpi_people_inserted
    ADD CONSTRAINT kpi_people_inserted_pkey PRIMARY KEY (people_id);


--
-- Name: kpi_people_map kpi_people_map_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.kpi_people_map
    ADD CONSTRAINT kpi_people_map_pkey PRIMARY KEY (src_sheet, src_row);


--
-- Name: kpi_roles_inserted kpi_roles_inserted_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.kpi_roles_inserted
    ADD CONSTRAINT kpi_roles_inserted_pkey PRIMARY KEY (role_id);


--
-- Name: people_inserted people_inserted_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.people_inserted
    ADD CONSTRAINT people_inserted_pkey PRIMARY KEY (people_id);


--
-- Name: roles_ref roles_ref_pkey; Type: CONSTRAINT; Schema: stg; Owner: -
--

ALTER TABLE ONLY stg.roles_ref
    ADD CONSTRAINT roles_ref_pkey PRIMARY KEY (role_name);


--
-- Name: hr_kpi_identity_map hr_kpi_identity_map_people_id_key; Type: CONSTRAINT; Schema: sync; Owner: -
--

ALTER TABLE ONLY sync.hr_kpi_identity_map
    ADD CONSTRAINT hr_kpi_identity_map_people_id_key UNIQUE (people_id);


--
-- Name: hr_kpi_identity_map hr_kpi_identity_map_pkey; Type: CONSTRAINT; Schema: sync; Owner: -
--

ALTER TABLE ONLY sync.hr_kpi_identity_map
    ADD CONSTRAINT hr_kpi_identity_map_pkey PRIMARY KEY (matricule);


--
-- Name: hr_kpi_identity_unmatched hr_kpi_identity_unmatched_pkey; Type: CONSTRAINT; Schema: sync; Owner: -
--

ALTER TABLE ONLY sync.hr_kpi_identity_unmatched
    ADD CONSTRAINT hr_kpi_identity_unmatched_pkey PRIMARY KEY (id);


--
-- Name: customer_document_attachment_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_document_attachment_lookup_idx ON public.customer_document_attachment USING btree (customer_id, revision_id, status);


--
-- Name: customer_document_event_lookup_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_document_event_lookup_idx ON public.customer_document_event USING btree (customer_id, entity_table, entity_id, event_id DESC);


--
-- Name: customer_document_primary_file_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX customer_document_primary_file_idx ON public.customer_document_attachment USING btree (revision_id, document_type) WHERE ((document_type = ANY (ARRAY['dossier_json'::text, 'dossier_pdf'::text])) AND (status <> 'archived'::text));


--
-- Name: customer_dossier_revision_latest_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_dossier_revision_latest_idx ON public.customer_dossier_revision USING btree (dossier_id, revision_no DESC) WHERE (status = 'validated'::text);


--
-- Name: customer_rfq_link_customer_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_rfq_link_customer_idx ON public.customer_rfq_link USING btree (customer_id);


--
-- Name: customer_visit_attachment_report_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_visit_attachment_report_idx ON public.customer_visit_attachment USING btree (visit_report_id, status);


--
-- Name: customer_visit_event_entity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_visit_event_entity_idx ON public.customer_visit_event USING btree (entity_table, entity_id, event_id DESC);


--
-- Name: customer_visit_report_customer_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_visit_report_customer_date_idx ON public.customer_visit_report USING btree (customer_id, visit_date DESC);


--
-- Name: customer_visit_report_rfq_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_visit_report_rfq_idx ON public.customer_visit_report USING btree (rfq_id) WHERE (rfq_id IS NOT NULL);


--
-- Name: customer_visit_report_status_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX customer_visit_report_status_date_idx ON public.customer_visit_report USING btree (status, visit_date DESC);


--
-- Name: idx_corrective_action_escalation_log_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_corrective_action_escalation_log_action ON public.corrective_action_escalation_log USING btree (corrective_action_id);


--
-- Name: idx_customer_delivery_mapping; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_delivery_mapping ON public.customer_delivery_unit USING btree (customer_id, unit_id);


--
-- Name: idx_customer_master_api_audit_customer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_master_api_audit_customer ON public.customer_master_api_audit USING btree (customer_id, changed_at DESC);


--
-- Name: idx_customer_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_parent ON public.customer USING btree (parent_customer_id);


--
-- Name: idx_customer_sales_kam; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_sales_kam ON public.customer USING btree (main_sales_people_id, main_kam_people_id);


--
-- Name: idx_customer_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_status ON public.customer USING btree (customer_status);


--
-- Name: idx_customer_zone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_zone ON public.customer USING btree (zone_id);


--
-- Name: idx_customer_zone_market; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_zone_market ON public.customer USING btree (zone_id, market_id);


--
-- Name: idx_kal_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kal_action ON public.kpi_action_link USING btree (action_id);


--
-- Name: idx_kal_kpi; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kal_kpi ON public.kpi_action_link USING btree (kpi_id);


--
-- Name: idx_kmh_change_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kmh_change_date ON public.kpi_monitoring_history USING btree (change_date DESC);


--
-- Name: idx_kmh_kpi_alloc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kmh_kpi_alloc ON public.kpi_monitoring_history USING btree (kpi_id, kpi_target_allocation_id);


--
-- Name: idx_kms_calc_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kms_calc_date ON public.kpi_monitoring_status USING btree (calculation_date);


--
-- Name: idx_kms_kpi; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kms_kpi ON public.kpi_monitoring_status USING btree (kpi_id);


--
-- Name: idx_kms_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kms_status ON public.kpi_monitoring_status USING btree (current_status);


--
-- Name: idx_kpi_assistant_conversation_assignment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_assistant_conversation_assignment ON public.kpi_assistant_conversation USING btree (people_role_assignment_id, kpi_id, updated_at DESC);


--
-- Name: idx_kpi_assistant_conversation_people; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_assistant_conversation_people ON public.kpi_assistant_conversation USING btree (people_id, kpi_id, updated_at DESC);


--
-- Name: idx_kpi_audit_changed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_audit_changed_at ON public.kpi_audit USING btree (changed_at);


--
-- Name: idx_kpi_audit_kpi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_audit_kpi_id ON public.kpi_audit USING btree (kpi_id);


--
-- Name: idx_kpi_audit_operation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_audit_operation ON public.kpi_audit USING btree (operation);


--
-- Name: idx_kpi_created_by_people; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_created_by_people ON public.kpi USING btree (created_by_people_id);


--
-- Name: idx_kpi_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_is_active ON public.kpi USING btree (is_active);


--
-- Name: idx_kpi_knowledge_refresh_queue_requested_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_knowledge_refresh_queue_requested_at ON public.kpi_knowledge_refresh_queue USING btree (requested_at, kpi_id);


--
-- Name: idx_kpi_owner_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_owner_role ON public.kpi USING btree (owner_role_id);


--
-- Name: idx_kpi_reference_kpi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_reference_kpi_id ON public.kpi USING btree (reference_kpi_id);


--
-- Name: idx_kpi_result_alloc_latest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_result_alloc_latest ON public.kpi_result USING btree (kpi_target_allocation_id, result_date DESC, kpi_result_id DESC);


--
-- Name: idx_kpi_result_allocation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_result_allocation ON public.kpi_result USING btree (kpi_target_allocation_id);


--
-- Name: idx_kpi_result_kpi; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_result_kpi ON public.kpi_result USING btree (kpi_id);


--
-- Name: idx_kpi_result_kpi_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_result_kpi_date ON public.kpi_result USING btree (kpi_id, result_date DESC);


--
-- Name: idx_kpi_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_status ON public.kpi USING btree (status);


--
-- Name: idx_kpi_submission_email_dispatch_log_mode_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_submission_email_dispatch_log_mode_date ON public.kpi_submission_email_dispatch_log USING btree (calculation_mode, local_dispatch_date DESC);


--
-- Name: idx_kpi_submission_email_dispatch_log_mode_frequency_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_submission_email_dispatch_log_mode_frequency_date ON public.kpi_submission_email_dispatch_log USING btree (calculation_mode, frequency_label, local_dispatch_date DESC);


--
-- Name: idx_kpi_submission_reminder_log_round; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_submission_reminder_log_round ON public.kpi_submission_reminder_log USING btree (frequency_label, target_week);


--
-- Name: idx_kpi_target_adjustment_approver_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_target_adjustment_approver_status ON public.kpi_target_adjustment_proposal USING btree (approver_people_id, status, created_at DESC);


--
-- Name: idx_kpi_target_allocation_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_target_allocation_created_by ON public.kpi_target_allocation USING btree (created_by_people_id);


--
-- Name: idx_kpi_target_allocation_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_target_allocation_is_active ON public.kpi_target_allocation USING btree (is_active);


--
-- Name: idx_kpi_test_kpi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_test_kpi_id ON public.kpi_test USING btree (kpi_id);


--
-- Name: idx_kpi_test_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_test_status ON public.kpi_test USING btree (status);


--
-- Name: idx_kpi_test_training_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_test_training_id ON public.kpi_test USING btree (training_id);


--
-- Name: idx_kpi_training_escalation_log_result; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_training_escalation_log_result ON public.kpi_training_escalation_log USING btree (result_id, sent_at DESC);


--
-- Name: idx_kpi_training_kpi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_training_kpi_id ON public.kpi_training USING btree (kpi_id);


--
-- Name: idx_kpi_training_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kpi_training_status ON public.kpi_training USING btree (status);


--
-- Name: idx_kr_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kr_source ON public.kpi_relationship USING btree (source_kpi_id);


--
-- Name: idx_kr_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kr_target ON public.kpi_relationship USING btree (target_kpi_id);


--
-- Name: idx_kresult_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kresult_date ON public.kpi_result USING btree (result_date);


--
-- Name: idx_kresult_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kresult_status ON public.kpi_result USING btree (result_status);


--
-- Name: idx_kresult_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kresult_week ON public.kpi_result USING btree (week);


--
-- Name: idx_kta_audit_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_audit_date ON public.kpi_target_allocation_audit USING btree (changed_at);


--
-- Name: idx_kta_audit_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_audit_id ON public.kpi_target_allocation_audit USING btree (kpi_target_allocation_id);


--
-- Name: idx_kta_audit_operation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_audit_operation ON public.kpi_target_allocation_audit USING btree (operation);


--
-- Name: idx_kta_kpi; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_kpi ON public.kpi_target_allocation USING btree (kpi_id);


--
-- Name: idx_kta_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_plant ON public.kpi_target_allocation USING btree (plant_id);


--
-- Name: idx_kta_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_role ON public.kpi_target_allocation USING btree (role_id);


--
-- Name: idx_kta_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_status ON public.kpi_target_allocation USING btree (target_status);


--
-- Name: idx_kta_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_unit ON public.kpi_target_allocation USING btree (unit_id);


--
-- Name: idx_kta_zone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kta_zone ON public.kpi_target_allocation USING btree (zone_id);


--
-- Name: idx_ktr_alloc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_alloc ON public.kpi_training_result USING btree (kpi_target_allocation_id);


--
-- Name: idx_ktr_due_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_due_date ON public.kpi_training_result USING btree (test_due_date) WHERE ((status)::text = ANY (ARRAY[('test_sent'::character varying)::text, ('test_overdue'::character varying)::text]));


--
-- Name: idx_ktr_kpi_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_kpi_id ON public.kpi_training_result USING btree (kpi_id);


--
-- Name: idx_ktr_people_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_people_id ON public.kpi_training_result USING btree (people_id);


--
-- Name: idx_ktr_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_status ON public.kpi_training_result USING btree (status);


--
-- Name: idx_ktr_test_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_test_id ON public.kpi_training_result USING btree (test_id);


--
-- Name: idx_ktr_training_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ktr_training_id ON public.kpi_training_result USING btree (training_id);


--
-- Name: idx_people_hr; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_people_hr ON public.people USING btree (hr_site, hr_employee_id);


--
-- Name: idx_people_paid_by_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_people_paid_by_unit ON public.people USING btree (paid_by_unit_id);


--
-- Name: idx_people_paid_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_people_paid_unit ON public.people USING btree (paid_by_unit_id);


--
-- Name: idx_people_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_people_status ON public.people USING btree (status);


--
-- Name: idx_people_work_at_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_people_work_at_unit ON public.people USING btree (work_at_unit_id);


--
-- Name: idx_people_work_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_people_work_unit ON public.people USING btree (work_at_unit_id);


--
-- Name: idx_pra_functional_manager; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_functional_manager ON public.people_role_assignment USING btree (functional_manager_assignment_id);


--
-- Name: idx_pra_people; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_people ON public.people_role_assignment USING btree (people_id);


--
-- Name: idx_pra_people_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_people_status ON public.people_role_assignment USING btree (people_id, assignment_status);


--
-- Name: idx_pra_reports_to; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_reports_to ON public.people_role_assignment USING btree (reports_to);


--
-- Name: idx_pra_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_role ON public.people_role_assignment USING btree (role_id);


--
-- Name: idx_pra_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_status ON public.people_role_assignment USING btree (assignment_status);


--
-- Name: idx_pra_status_upper; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_status_upper ON public.people_role_assignment USING btree (upper((assignment_status)::text));


--
-- Name: idx_pra_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_unit ON public.people_role_assignment USING btree (unit_id);


--
-- Name: idx_pra_zone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pra_zone ON public.people_role_assignment USING btree (zone_id);


--
-- Name: idx_reporting_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reporting_scope ON public.role_reporting_relationship USING btree (source_unit_id, target_unit_id, zone_id, market_id, product_line_id);


--
-- Name: idx_reporting_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reporting_source ON public.role_reporting_relationship USING btree (source_role_id, relationship_type, status);


--
-- Name: idx_reporting_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reporting_target ON public.role_reporting_relationship USING btree (target_role_id, relationship_type, status);


--
-- Name: idx_role_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_role_active ON public.role USING btree (is_active);


--
-- Name: idx_role_family; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_role_family ON public.role USING btree (role_family);


--
-- Name: idx_role_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_role_level ON public.role USING btree (role_level);


--
-- Name: idx_role_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_role_parent ON public.role USING btree (parent_role_id);


--
-- Name: idx_role_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_role_slug ON public.role USING btree (role_slug);


--
-- Name: idx_unit_country; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_unit_country ON public.unit USING btree (country);


--
-- Name: idx_unit_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_unit_parent ON public.unit USING btree (parent_unit_id);


--
-- Name: idx_unit_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_unit_status ON public.unit USING btree (status_id);


--
-- Name: idx_unit_type_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_unit_type_id ON public.unit USING btree (unit_type_id);


--
-- Name: ix_kpi_monitoring_history_kpi_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kpi_monitoring_history_kpi_date ON public.kpi_monitoring_history USING btree (kpi_id, kpi_target_allocation_id, change_date DESC);


--
-- Name: ix_kpi_monitoring_status_escalation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kpi_monitoring_status_escalation ON public.kpi_monitoring_status USING btree (escalation_level DESC);


--
-- Name: ix_kpi_monitoring_status_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kpi_monitoring_status_owner ON public.kpi_monitoring_status USING btree (owner_people_id);


--
-- Name: ix_kpi_monitoring_status_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kpi_monitoring_status_priority ON public.kpi_monitoring_status USING btree (priority_score DESC NULLS LAST);


--
-- Name: kpi_app_assistant_conversation_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kpi_app_assistant_conversation_user_idx ON public.kpi_app_assistant_conversation USING btree (user_id);


--
-- Name: kpi_app_auth_event_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kpi_app_auth_event_created_idx ON public.kpi_app_auth_event USING btree (created_at);


--
-- Name: kpi_app_change_log_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kpi_app_change_log_created_idx ON public.kpi_app_change_log USING btree (created_at);


--
-- Name: kpi_app_intake_row_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kpi_app_intake_row_status_idx ON public.kpi_app_intake_row USING btree (status, entity_type);


--
-- Name: kpi_app_password_token_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kpi_app_password_token_user_idx ON public.kpi_app_password_token USING btree (user_id);


--
-- Name: kpi_app_session_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX kpi_app_session_user_idx ON public.kpi_app_session USING btree (user_id);


--
-- Name: kpi_app_user_microsoft_oid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX kpi_app_user_microsoft_oid_idx ON public.kpi_app_user USING btree (microsoft_oid);


--
-- Name: uq_active_reporting_relationship; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_active_reporting_relationship ON public.role_reporting_relationship USING btree (source_role_id, target_role_id, relationship_type, COALESCE(source_unit_id, (0)::bigint), COALESCE(target_unit_id, (0)::bigint), COALESCE(zone_id, (0)::bigint), COALESCE(market_id, (0)::bigint), COALESCE(product_line_id, 0), priority) WHERE (((status)::text = 'ACTIVE'::text) AND (valid_to IS NULL));


--
-- Name: uq_customer_standard_code; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_customer_standard_code ON public.customer USING btree (customer_code) WHERE ((customer_code)::text ~ '^CUS-[0-9]{8}$'::text);


--
-- Name: uq_kpi_submission_email_dispatch_log_people_mode_frequency_unit; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_kpi_submission_email_dispatch_log_people_mode_frequency_unit ON public.kpi_submission_email_dispatch_log USING btree (people_id, calculation_mode, frequency_label, COALESCE(unit_id, 0), local_dispatch_date);


--
-- Name: uq_kpi_target_adjustment_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_kpi_target_adjustment_pending ON public.kpi_target_adjustment_proposal USING btree (kpi_target_allocation_id) WHERE ((status)::text = 'Pending approval'::text);


--
-- Name: uq_people_hr; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_people_hr ON public.people USING btree (hr_employee_id, work_at_unit_id) WHERE (hr_employee_id IS NOT NULL);


--
-- Name: ux_kpi_monitoring_status_allocation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_kpi_monitoring_status_allocation ON public.kpi_monitoring_status USING btree (kpi_id, kpi_target_allocation_id);


--
-- Name: kpi trg_audit_kpi_changes; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_kpi_changes AFTER INSERT OR DELETE OR UPDATE ON public.kpi FOR EACH ROW EXECUTE FUNCTION public.audit_kpi_changes();


--
-- Name: kpi_target_allocation trg_audit_kpi_target_allocation_changes; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_audit_kpi_target_allocation_changes AFTER INSERT OR DELETE OR UPDATE ON public.kpi_target_allocation FOR EACH ROW EXECUTE FUNCTION public.audit_kpi_target_allocation_changes();


--
-- Name: customer_document_access trg_customer_document_access_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_document_access_audit AFTER INSERT OR DELETE OR UPDATE ON public.customer_document_access FOR EACH ROW EXECUTE FUNCTION public.customer_document_audit('principal_id');


--
-- Name: customer_document_attachment trg_customer_document_attachment_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_document_attachment_audit AFTER INSERT OR UPDATE ON public.customer_document_attachment FOR EACH ROW EXECUTE FUNCTION public.customer_document_audit('attachment_id');


--
-- Name: customer_document_attachment trg_customer_document_attachment_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_document_attachment_guard BEFORE INSERT OR DELETE OR UPDATE ON public.customer_document_attachment FOR EACH ROW EXECUTE FUNCTION public.customer_document_attachment_guard();


--
-- Name: customer_document_attachment trg_customer_document_bump_revision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_document_bump_revision AFTER INSERT OR UPDATE ON public.customer_document_attachment FOR EACH ROW EXECUTE FUNCTION public.customer_document_bump_revision();


--
-- Name: customer_dossier trg_customer_dossier_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_dossier_audit AFTER INSERT OR UPDATE ON public.customer_dossier FOR EACH ROW EXECUTE FUNCTION public.customer_document_audit('dossier_id');


--
-- Name: customer_dossier_revision trg_customer_dossier_revision_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_dossier_revision_audit AFTER INSERT OR UPDATE ON public.customer_dossier_revision FOR EACH ROW EXECUTE FUNCTION public.customer_document_audit('revision_id');


--
-- Name: customer_dossier_revision trg_customer_dossier_revision_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_dossier_revision_guard BEFORE INSERT OR DELETE OR UPDATE ON public.customer_dossier_revision FOR EACH ROW EXECUTE FUNCTION public.customer_dossier_revision_guard();


--
-- Name: customer_visit_report trg_customer_visit_report_before_update; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_customer_visit_report_before_update BEFORE UPDATE ON public.customer_visit_report FOR EACH ROW EXECUTE FUNCTION public.customer_visit_report_before_update();


--
-- Name: kpi_action_link trg_kal_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kal_updated_at BEFORE UPDATE ON public.kpi_action_link FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: kpi_result trg_kam_visits_changed; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kam_visits_changed AFTER INSERT OR DELETE OR UPDATE ON public.kpi_result FOR EACH ROW EXECUTE FUNCTION public.fn_notify_kam_visits_changed();


--
-- Name: kpi_monitoring_status trg_kms_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kms_updated_at BEFORE UPDATE ON public.kpi_monitoring_status FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: kpi trg_kpi_calc_limits; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_calc_limits BEFORE INSERT OR UPDATE OF target_value, tolerance_type, up_tolerance, low_tolerance ON public.kpi FOR EACH ROW EXECUTE FUNCTION public.fn_kpi_calc_limits();


--
-- Name: customer trg_kpi_knowledge_refresh_on_customer; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_customer AFTER INSERT OR DELETE OR UPDATE ON public.customer FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: kpi trg_kpi_knowledge_refresh_on_kpi; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_kpi AFTER INSERT OR UPDATE ON public.kpi FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: kpi_result trg_kpi_knowledge_refresh_on_kpi_result; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_kpi_result AFTER INSERT OR DELETE OR UPDATE ON public.kpi_result FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: kpi_target_allocation trg_kpi_knowledge_refresh_on_kpi_target_allocation; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_kpi_target_allocation AFTER INSERT OR DELETE OR UPDATE ON public.kpi_target_allocation FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: market trg_kpi_knowledge_refresh_on_market; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_market AFTER INSERT OR DELETE OR UPDATE ON public.market FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: people trg_kpi_knowledge_refresh_on_people; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_people AFTER INSERT OR DELETE OR UPDATE ON public.people FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: product trg_kpi_knowledge_refresh_on_product; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_product AFTER INSERT OR DELETE OR UPDATE ON public.product FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: product_line trg_kpi_knowledge_refresh_on_product_line; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_product_line AFTER INSERT OR DELETE OR UPDATE ON public.product_line FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: role trg_kpi_knowledge_refresh_on_role; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_role AFTER INSERT OR DELETE OR UPDATE ON public.role FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: subject trg_kpi_knowledge_refresh_on_subject; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_subject AFTER INSERT OR DELETE OR UPDATE ON public.subject FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: subject_kpi trg_kpi_knowledge_refresh_on_subject_kpi; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_subject_kpi AFTER INSERT OR DELETE OR UPDATE ON public.subject_kpi FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: unit trg_kpi_knowledge_refresh_on_unit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_unit AFTER INSERT OR DELETE OR UPDATE ON public.unit FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: unit_type trg_kpi_knowledge_refresh_on_unit_type; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_unit_type AFTER INSERT OR DELETE OR UPDATE ON public.unit_type FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: zone trg_kpi_knowledge_refresh_on_zone; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_knowledge_refresh_on_zone AFTER INSERT OR DELETE OR UPDATE ON public.zone FOR EACH ROW EXECUTE FUNCTION public.enqueue_kpi_knowledge_refresh_for_change();


--
-- Name: kpi_result trg_kpi_result_calc_limits; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_result_calc_limits BEFORE INSERT OR UPDATE OF target_value ON public.kpi_result FOR EACH ROW EXECUTE FUNCTION public.trg_kpi_result_calc_limits();


--
-- Name: kpi_result trg_kpi_result_check_kpi_id; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_result_check_kpi_id BEFORE INSERT OR UPDATE ON public.kpi_result FOR EACH ROW EXECUTE FUNCTION public.fn_check_kpi_result_kpi_id();


--
-- Name: kpi_test trg_kpi_test_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_test_updated_at BEFORE UPDATE ON public.kpi_test FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: kpi_training_result trg_kpi_training_result_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_training_result_updated_at BEFORE UPDATE ON public.kpi_training_result FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: kpi_training trg_kpi_training_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kpi_training_updated_at BEFORE UPDATE ON public.kpi_training FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: kpi_relationship trg_kr_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_kr_updated_at BEFORE UPDATE ON public.kpi_relationship FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: unit trg_log_unit_factory_cost_column_changes; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_log_unit_factory_cost_column_changes BEFORE UPDATE ON public.unit FOR EACH ROW EXECUTE FUNCTION public.fn_log_unit_factory_cost_column_changes();


--
-- Name: customer trg_manage_customer_code; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_manage_customer_code BEFORE INSERT OR UPDATE OF customer_code ON public.customer FOR EACH ROW EXECUTE FUNCTION public.fn_manage_customer_code();


--
-- Name: people_role_assignment trg_notify_role_assignment_change; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notify_role_assignment_change AFTER UPDATE OF role_id ON public.people_role_assignment FOR EACH ROW WHEN ((new.role_id IS DISTINCT FROM old.role_id)) EXECUTE FUNCTION public.notify_role_assignment_change();


--
-- Name: people_role_assignment trg_notify_role_assignment_change_insert; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notify_role_assignment_change_insert AFTER INSERT ON public.people_role_assignment FOR EACH ROW WHEN (((new.is_primary IS TRUE) AND ((new.assignment_status)::text = 'ACTIVE'::text))) EXECUTE FUNCTION public.notify_role_assignment_change();


--
-- Name: people_role_assignment trg_sync_customer_kam; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_customer_kam AFTER INSERT OR DELETE OR UPDATE ON public.people_role_assignment FOR EACH ROW EXECUTE FUNCTION public.fn_sync_customer_main_contacts();

ALTER TABLE public.people_role_assignment DISABLE TRIGGER trg_sync_customer_kam;


--
-- Name: kpi_target_allocation trg_sync_target_value_to_kpi_result; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_sync_target_value_to_kpi_result AFTER UPDATE OF target_value ON public.kpi_target_allocation FOR EACH ROW EXECUTE FUNCTION public.fn_sync_target_value_to_kpi_result();


--
-- Name: customer_visit_report trg_visit_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_visit_audit AFTER INSERT OR DELETE OR UPDATE ON public.customer_visit_report FOR EACH ROW EXECUTE FUNCTION public.customer_visit_audit('visit_report_id');


--
-- Name: customer_visit_attachment trg_visit_file_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_visit_file_audit AFTER INSERT OR DELETE OR UPDATE ON public.customer_visit_attachment FOR EACH ROW EXECUTE FUNCTION public.customer_visit_audit('attachment_id');


--
-- Name: customer_rfq_link trg_visit_link_audit; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_visit_link_audit AFTER INSERT OR DELETE OR UPDATE ON public.customer_rfq_link FOR EACH ROW EXECUTE FUNCTION public.customer_visit_audit('rfq_id');


--
-- Name: customer_document_access customer_document_access_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_access
    ADD CONSTRAINT customer_document_access_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE RESTRICT;


--
-- Name: customer_document_attachment customer_document_attachment_revision_id_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_attachment
    ADD CONSTRAINT customer_document_attachment_revision_id_customer_id_fkey FOREIGN KEY (revision_id, customer_id) REFERENCES public.customer_dossier_revision(revision_id, customer_id) ON DELETE RESTRICT;


--
-- Name: customer_document_event customer_document_event_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_document_event
    ADD CONSTRAINT customer_document_event_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE RESTRICT;


--
-- Name: customer_dossier customer_dossier_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier
    ADD CONSTRAINT customer_dossier_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE RESTRICT;


--
-- Name: customer_dossier_revision customer_dossier_revision_dossier_id_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_dossier_revision
    ADD CONSTRAINT customer_dossier_revision_dossier_id_customer_id_fkey FOREIGN KEY (dossier_id, customer_id) REFERENCES public.customer_dossier(dossier_id, customer_id) ON DELETE RESTRICT;


--
-- Name: customer customer_main_kam_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_main_kam_people_id_fkey FOREIGN KEY (main_kam_people_id) REFERENCES public.people(people_id);


--
-- Name: customer customer_main_sales_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_main_sales_people_id_fkey FOREIGN KEY (main_sales_people_id) REFERENCES public.people(people_id);


--
-- Name: customer customer_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.market(market_id);


--
-- Name: customer customer_parent_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_parent_customer_id_fkey FOREIGN KEY (parent_customer_id) REFERENCES public.customer(customer_id);


--
-- Name: customer_rfq_link customer_rfq_link_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_rfq_link
    ADD CONSTRAINT customer_rfq_link_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE RESTRICT;


--
-- Name: customer_visit_attachment customer_visit_attachment_visit_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_attachment
    ADD CONSTRAINT customer_visit_attachment_visit_report_id_fkey FOREIGN KEY (visit_report_id) REFERENCES public.customer_visit_report(visit_report_id) ON DELETE RESTRICT;


--
-- Name: customer_visit_report customer_visit_consistent_rfq_customer; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_report
    ADD CONSTRAINT customer_visit_consistent_rfq_customer FOREIGN KEY (rfq_id, customer_id) REFERENCES public.customer_rfq_link(rfq_id, customer_id);


--
-- Name: customer_visit_report customer_visit_report_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_report
    ADD CONSTRAINT customer_visit_report_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE RESTRICT;


--
-- Name: customer_visit_report customer_visit_report_rfq_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_visit_report
    ADD CONSTRAINT customer_visit_report_rfq_id_fkey FOREIGN KEY (rfq_id) REFERENCES public.customer_rfq_link(rfq_id) ON DELETE RESTRICT;


--
-- Name: customer customer_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer
    ADD CONSTRAINT customer_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zone(zone_id);


--
-- Name: customer_file fk_customer_file_customer; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_file
    ADD CONSTRAINT fk_customer_file_customer FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE CASCADE;


--
-- Name: customer_file fk_customer_file_people; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_file
    ADD CONSTRAINT fk_customer_file_people FOREIGN KEY (uploaded_by_people_id) REFERENCES public.people(people_id) ON DELETE SET NULL;


--
-- Name: customer_delivery_unit fk_delivery_customer; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_delivery_unit
    ADD CONSTRAINT fk_delivery_customer FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE CASCADE;


--
-- Name: customer_delivery_unit fk_delivery_product; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_delivery_unit
    ADD CONSTRAINT fk_delivery_product FOREIGN KEY (product_id) REFERENCES public.product(product_id) ON DELETE SET NULL;


--
-- Name: customer_delivery_unit fk_delivery_unit; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_delivery_unit
    ADD CONSTRAINT fk_delivery_unit FOREIGN KEY (unit_id) REFERENCES public.unit(unit_id) ON DELETE RESTRICT;


--
-- Name: role fk_functional_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT fk_functional_role FOREIGN KEY (functional_report_role_id) REFERENCES public.role(role_id);


--
-- Name: kpi_action_link fk_kal_alloc; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_action_link
    ADD CONSTRAINT fk_kal_alloc FOREIGN KEY (kpi_target_allocation_id) REFERENCES public.kpi_target_allocation(kpi_target_allocation_id);


--
-- Name: kpi_action_link fk_kal_kpi; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_action_link
    ADD CONSTRAINT fk_kal_kpi FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_monitoring_history fk_kmh_alloc; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_history
    ADD CONSTRAINT fk_kmh_alloc FOREIGN KEY (kpi_target_allocation_id) REFERENCES public.kpi_target_allocation(kpi_target_allocation_id) ON DELETE CASCADE;


--
-- Name: kpi_monitoring_history fk_kmh_kpi; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_history
    ADD CONSTRAINT fk_kmh_kpi FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_monitoring_status fk_kms_alloc; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_status
    ADD CONSTRAINT fk_kms_alloc FOREIGN KEY (kpi_target_allocation_id) REFERENCES public.kpi_target_allocation(kpi_target_allocation_id) ON DELETE CASCADE;


--
-- Name: kpi_monitoring_status fk_kms_kpi; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_status
    ADD CONSTRAINT fk_kms_kpi FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_monitoring_status fk_kms_owner_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_monitoring_status
    ADD CONSTRAINT fk_kms_owner_role FOREIGN KEY (owner_role_id) REFERENCES public.role(role_id);


--
-- Name: kpi fk_kpi_created_by_people; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi
    ADD CONSTRAINT fk_kpi_created_by_people FOREIGN KEY (created_by_people_id) REFERENCES public.people(people_id) ON DELETE SET NULL;


--
-- Name: kpi_target_allocation fk_kpi_target_allocation_role; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT fk_kpi_target_allocation_role FOREIGN KEY (role_id) REFERENCES public.role(role_id);


--
-- Name: kpi_target_allocation fk_kpi_target_allocation_unit_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT fk_kpi_target_allocation_unit_type FOREIGN KEY (unit_type_id) REFERENCES public.unit_type(unit_type_id);


--
-- Name: kpi_relationship fk_kr_source; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_relationship
    ADD CONSTRAINT fk_kr_source FOREIGN KEY (source_kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_relationship fk_kr_target; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_relationship
    ADD CONSTRAINT fk_kr_target FOREIGN KEY (target_kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: people_role_assignment fk_pra_functional_manager; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT fk_pra_functional_manager FOREIGN KEY (functional_manager_assignment_id) REFERENCES public.people_role_assignment(assignment_id);


--
-- Name: people_role_assignment fk_pra_reports_to; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT fk_pra_reports_to FOREIGN KEY (reports_to) REFERENCES public.people_role_assignment(assignment_id);


--
-- Name: customer_project fk_project_customer; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_project
    ADD CONSTRAINT fk_project_customer FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id) ON DELETE CASCADE;


--
-- Name: role fk_role_parent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role
    ADD CONSTRAINT fk_role_parent FOREIGN KEY (parent_role_id) REFERENCES public.role(role_id);


--
-- Name: unit fk_unit_manager; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit
    ADD CONSTRAINT fk_unit_manager FOREIGN KEY (manager_id) REFERENCES public.people(people_id);


--
-- Name: unit fk_unit_status; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit
    ADD CONSTRAINT fk_unit_status FOREIGN KEY (status_id) REFERENCES public.status(status_id);


--
-- Name: unit fk_unit_type; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit
    ADD CONSTRAINT fk_unit_type FOREIGN KEY (unit_type_id) REFERENCES public.unit_type(unit_type_id);


--
-- Name: user_auth fk_user_auth_people; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_auth
    ADD CONSTRAINT fk_user_auth_people FOREIGN KEY (people_id) REFERENCES public.people(people_id);


--
-- Name: kpi_app_assistant_conversation kpi_app_assistant_conversation_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_assistant_conversation
    ADD CONSTRAINT kpi_app_assistant_conversation_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.kpi_app_user(user_id) ON DELETE CASCADE;


--
-- Name: kpi_app_auth_event kpi_app_auth_event_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_auth_event
    ADD CONSTRAINT kpi_app_auth_event_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.kpi_app_user(user_id) ON DELETE SET NULL;


--
-- Name: kpi_app_auth_event kpi_app_auth_event_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_auth_event
    ADD CONSTRAINT kpi_app_auth_event_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.kpi_app_user(user_id) ON DELETE SET NULL;


--
-- Name: kpi_app_password_token kpi_app_password_token_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_password_token
    ADD CONSTRAINT kpi_app_password_token_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.kpi_app_user(user_id) ON DELETE CASCADE;


--
-- Name: kpi_app_session kpi_app_session_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_session
    ADD CONSTRAINT kpi_app_session_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.kpi_app_user(user_id) ON DELETE CASCADE;


--
-- Name: kpi_app_setting kpi_app_setting_updated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_setting
    ADD CONSTRAINT kpi_app_setting_updated_by_user_id_fkey FOREIGN KEY (updated_by_user_id) REFERENCES public.kpi_app_user(user_id) ON DELETE SET NULL;


--
-- Name: kpi_app_user kpi_app_user_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_app_user
    ADD CONSTRAINT kpi_app_user_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id) ON DELETE SET NULL;


--
-- Name: kpi_assistant_conversation kpi_assistant_conversation_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_assistant_conversation
    ADD CONSTRAINT kpi_assistant_conversation_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_assistant_conversation kpi_assistant_conversation_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_assistant_conversation
    ADD CONSTRAINT kpi_assistant_conversation_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id) ON DELETE SET NULL;


--
-- Name: kpi_assistant_conversation kpi_assistant_conversation_people_role_assignment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_assistant_conversation
    ADD CONSTRAINT kpi_assistant_conversation_people_role_assignment_id_fkey FOREIGN KEY (people_role_assignment_id) REFERENCES public.people_role_assignment(assignment_id);


--
-- Name: kpi_knowledge_refresh_queue kpi_knowledge_refresh_queue_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_knowledge_refresh_queue
    ADD CONSTRAINT kpi_knowledge_refresh_queue_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id) ON DELETE CASCADE;


--
-- Name: kpi kpi_owner_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi
    ADD CONSTRAINT kpi_owner_role_id_fkey FOREIGN KEY (owner_role_id) REFERENCES public.role(role_id);


--
-- Name: kpi kpi_reference_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi
    ADD CONSTRAINT kpi_reference_kpi_id_fkey FOREIGN KEY (reference_kpi_id) REFERENCES public.kpi(kpi_id) ON DELETE SET NULL;


--
-- Name: kpi_result kpi_result_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_result
    ADD CONSTRAINT kpi_result_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_result kpi_result_kpi_target_allocation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_result
    ADD CONSTRAINT kpi_result_kpi_target_allocation_id_fkey FOREIGN KEY (kpi_target_allocation_id) REFERENCES public.kpi_target_allocation(kpi_target_allocation_id);


--
-- Name: kpi_result kpi_result_recorded_by_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_result
    ADD CONSTRAINT kpi_result_recorded_by_people_id_fkey FOREIGN KEY (recorded_by_people_id) REFERENCES public.people(people_id);


--
-- Name: kpi_submission_email_dispatch_log kpi_submission_email_dispatch_log_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_email_dispatch_log
    ADD CONSTRAINT kpi_submission_email_dispatch_log_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id) ON DELETE CASCADE;


--
-- Name: kpi_submission_email_dispatch_log kpi_submission_email_dispatch_log_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_email_dispatch_log
    ADD CONSTRAINT kpi_submission_email_dispatch_log_unit_id_fkey FOREIGN KEY (unit_id) REFERENCES public.unit(unit_id) ON DELETE SET NULL;


--
-- Name: kpi_submission_reminder_log kpi_submission_reminder_log_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_submission_reminder_log
    ADD CONSTRAINT kpi_submission_reminder_log_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id) ON DELETE CASCADE;


--
-- Name: kpi_target_adjustment_proposal kpi_target_adjustment_proposal_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_adjustment_proposal
    ADD CONSTRAINT kpi_target_adjustment_proposal_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id) ON DELETE CASCADE;


--
-- Name: kpi_target_adjustment_proposal kpi_target_adjustment_proposal_kpi_target_allocation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_adjustment_proposal
    ADD CONSTRAINT kpi_target_adjustment_proposal_kpi_target_allocation_id_fkey FOREIGN KEY (kpi_target_allocation_id) REFERENCES public.kpi_target_allocation(kpi_target_allocation_id) ON DELETE CASCADE;


--
-- Name: kpi_target_allocation kpi_target_allocation_approved_by_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_approved_by_people_id_fkey FOREIGN KEY (approved_by_people_id) REFERENCES public.people(people_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customer(customer_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.market(market_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.unit(unit_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.product(product_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_product_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_product_line_id_fkey FOREIGN KEY (product_line_id) REFERENCES public.product_line(product_line_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_set_by_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_set_by_people_id_fkey FOREIGN KEY (set_by_people_id) REFERENCES public.people(people_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_unit_id_fkey FOREIGN KEY (unit_id) REFERENCES public.unit(unit_id);


--
-- Name: kpi_target_allocation kpi_target_allocation_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_target_allocation
    ADD CONSTRAINT kpi_target_allocation_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zone(zone_id);


--
-- Name: kpi_test kpi_test_created_by_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_test
    ADD CONSTRAINT kpi_test_created_by_people_id_fkey FOREIGN KEY (created_by_people_id) REFERENCES public.people(people_id);


--
-- Name: kpi_test kpi_test_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_test
    ADD CONSTRAINT kpi_test_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id) ON DELETE RESTRICT;


--
-- Name: kpi_test kpi_test_replaced_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_test
    ADD CONSTRAINT kpi_test_replaced_by_id_fkey FOREIGN KEY (replaced_by_id) REFERENCES public.kpi_test(test_id);


--
-- Name: kpi_test kpi_test_training_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_test
    ADD CONSTRAINT kpi_test_training_id_fkey FOREIGN KEY (training_id) REFERENCES public.kpi_training(training_id);


--
-- Name: kpi_training kpi_training_created_by_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training
    ADD CONSTRAINT kpi_training_created_by_people_id_fkey FOREIGN KEY (created_by_people_id) REFERENCES public.people(people_id);


--
-- Name: kpi_training kpi_training_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training
    ADD CONSTRAINT kpi_training_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id) ON DELETE RESTRICT;


--
-- Name: kpi_training kpi_training_replaced_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training
    ADD CONSTRAINT kpi_training_replaced_by_id_fkey FOREIGN KEY (replaced_by_id) REFERENCES public.kpi_training(training_id);


--
-- Name: kpi_training_result kpi_training_result_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_result
    ADD CONSTRAINT kpi_training_result_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id) ON DELETE RESTRICT;


--
-- Name: kpi_training_result kpi_training_result_kpi_target_allocation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_result
    ADD CONSTRAINT kpi_training_result_kpi_target_allocation_id_fkey FOREIGN KEY (kpi_target_allocation_id) REFERENCES public.kpi_target_allocation(kpi_target_allocation_id);


--
-- Name: kpi_training_result kpi_training_result_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_result
    ADD CONSTRAINT kpi_training_result_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id) ON DELETE RESTRICT;


--
-- Name: kpi_training_result kpi_training_result_test_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_result
    ADD CONSTRAINT kpi_training_result_test_id_fkey FOREIGN KEY (test_id) REFERENCES public.kpi_test(test_id) ON DELETE RESTRICT;


--
-- Name: kpi_training_result kpi_training_result_training_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kpi_training_result
    ADD CONSTRAINT kpi_training_result_training_id_fkey FOREIGN KEY (training_id) REFERENCES public.kpi_training(training_id) ON DELETE RESTRICT;


--
-- Name: manufacturing_strategy manufacturing_strategy_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturing_strategy
    ADD CONSTRAINT manufacturing_strategy_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.unit(unit_id);


--
-- Name: manufacturing_strategy manufacturing_strategy_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturing_strategy
    ADD CONSTRAINT manufacturing_strategy_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.product(product_id);


--
-- Name: manufacturing_strategy manufacturing_strategy_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturing_strategy
    ADD CONSTRAINT manufacturing_strategy_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zone(zone_id);


--
-- Name: people people_paid_by_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_paid_by_unit_id_fkey FOREIGN KEY (paid_by_unit_id) REFERENCES public.unit(unit_id);


--
-- Name: people_role_assignment people_role_assignment_functional_manager_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT people_role_assignment_functional_manager_role_id_fkey FOREIGN KEY (functional_manager_role_id) REFERENCES public.role(role_id);


--
-- Name: people_role_assignment people_role_assignment_people_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT people_role_assignment_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id);


--
-- Name: people_role_assignment people_role_assignment_product_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT people_role_assignment_product_line_id_fkey FOREIGN KEY (product_line_id) REFERENCES public.product_line(product_line_id);


--
-- Name: people_role_assignment people_role_assignment_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people_role_assignment
    ADD CONSTRAINT people_role_assignment_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.role(role_id);


--
-- Name: people people_work_at_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.people
    ADD CONSTRAINT people_work_at_unit_id_fkey FOREIGN KEY (work_at_unit_id) REFERENCES public.unit(unit_id);


--
-- Name: product product_product_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.product
    ADD CONSTRAINT product_product_line_id_fkey FOREIGN KEY (product_line_id) REFERENCES public.product_line(product_line_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_market_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_market_id_fkey FOREIGN KEY (market_id) REFERENCES public.market(market_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_product_line_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_product_line_id_fkey FOREIGN KEY (product_line_id) REFERENCES public.product_line(product_line_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_source_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_source_role_id_fkey FOREIGN KEY (source_role_id) REFERENCES public.role(role_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_source_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_source_unit_id_fkey FOREIGN KEY (source_unit_id) REFERENCES public.unit(unit_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_target_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_target_role_id_fkey FOREIGN KEY (target_role_id) REFERENCES public.role(role_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_target_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_target_unit_id_fkey FOREIGN KEY (target_unit_id) REFERENCES public.unit(unit_id);


--
-- Name: role_reporting_relationship role_reporting_relationship_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_reporting_relationship
    ADD CONSTRAINT role_reporting_relationship_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zone(zone_id);


--
-- Name: subject_kpi subject_kpi_kpi_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_kpi
    ADD CONSTRAINT subject_kpi_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES public.kpi(kpi_id);


--
-- Name: subject_kpi subject_kpi_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject_kpi
    ADD CONSTRAINT subject_kpi_subject_id_fkey FOREIGN KEY (subject_id) REFERENCES public.subject(subject_id);


--
-- Name: subject subject_parent_subject_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.subject
    ADD CONSTRAINT subject_parent_subject_id_fkey FOREIGN KEY (parent_subject_id) REFERENCES public.subject(subject_id);


--
-- Name: unit_factory_cost_change_log unit_factory_cost_change_log_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit_factory_cost_change_log
    ADD CONSTRAINT unit_factory_cost_change_log_unit_id_fkey FOREIGN KEY (unit_id) REFERENCES public.unit(unit_id) ON DELETE CASCADE;


--
-- Name: unit unit_parent_unit_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit
    ADD CONSTRAINT unit_parent_unit_id_fkey FOREIGN KEY (parent_unit_id) REFERENCES public.unit(unit_id);


--
-- Name: unit unit_zone_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.unit
    ADD CONSTRAINT unit_zone_id_fkey FOREIGN KEY (zone_id) REFERENCES public.zone(zone_id);


--
-- Name: hr_kpi_identity_map hr_kpi_identity_map_people_id_fkey; Type: FK CONSTRAINT; Schema: sync; Owner: -
--

ALTER TABLE ONLY sync.hr_kpi_identity_map
    ADD CONSTRAINT hr_kpi_identity_map_people_id_fkey FOREIGN KEY (people_id) REFERENCES public.people(people_id);


--
-- PostgreSQL database dump complete
--

