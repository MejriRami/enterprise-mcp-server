-- Structure only (no data) of the action-plan database used by the MCP server.
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
-- Name: update_overdue_status(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_overdue_status() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Vérifie si l'action n'est PAS fermée ET si la date d'échéance est passée (antérieure à aujourd'hui)
    IF NEW.status NOT IN ('closed', 'done', 'completed') AND NEW.due_date < current_date THEN
        -- Si les conditions sont remplies, met à jour le statut en 'overdue' (ou 'en retard')
        NEW.status = 'overdue';
    END IF;

    -- Retourne la nouvelle ligne modifiée
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: _backup_sujet_20261021; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public._backup_sujet_20261021 (
    id bigint,
    code text,
    titre text,
    description text,
    parent_sujet_id bigint,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    inserted_by text,
    source_application text,
    is_deleted boolean,
    deleted_at timestamp with time zone,
    deleted_by text
);


--
-- Name: action; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action (
    id bigint NOT NULL,
    sujet_id bigint NOT NULL,
    parent_action_id bigint,
    type text NOT NULL,
    titre text NOT NULL,
    description text,
    status text DEFAULT 'open'::text,
    priorite integer,
    responsable text,
    due_date date,
    ordre integer,
    depth integer GENERATED ALWAYS AS (
CASE
    WHEN (parent_action_id IS NULL) THEN 0
    ELSE 1
END) STORED,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_date date,
    email_responsable text,
    estimated_duration_days integer,
    importance text,
    urgency text,
    escalation_level integer,
    priority_index integer,
    last_reminder_sent_at timestamp with time zone,
    kpi text,
    demandeur text,
    email_demandeur text,
    is_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by text,
    kpi_id character varying,
    unit_id character varying,
    comment character varying,
    period_label character varying(120),
    kpi_target_allocation_id character varying,
    created_by_application character varying
);


--
-- Name: action_attachment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_attachment (
    id bigint NOT NULL,
    action_id bigint NOT NULL,
    file_name text NOT NULL,
    file_path text NOT NULL,
    uploaded_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: action_attachment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_attachment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_attachment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_attachment_id_seq OWNED BY public.action_attachment.id;


--
-- Name: action_escalation_email; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_escalation_email (
    id bigint NOT NULL,
    action_id bigint NOT NULL,
    stage integer NOT NULL,
    escalation_level integer NOT NULL,
    recipient_email text NOT NULL,
    notification_id bigint,
    sent_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: action_escalation_email_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_escalation_email_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_escalation_email_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_escalation_email_id_seq OWNED BY public.action_escalation_email.id;


--
-- Name: action_escalation_notification; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_escalation_notification (
    id bigint NOT NULL,
    action_id bigint NOT NULL,
    recipient_email text NOT NULL,
    cc_emails jsonb,
    escalation_level integer NOT NULL,
    hierarchy_source_used text NOT NULL,
    responsible_chain jsonb,
    requester_chain jsonb,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    seen_at timestamp with time zone,
    resolved_at timestamp with time zone,
    last_summary_email_sent_at timestamp with time zone,
    notification_kind text DEFAULT 'escalation'::text NOT NULL,
    CONSTRAINT ck_action_escalation_notification_status CHECK ((status = ANY (ARRAY['pending'::text, 'seen'::text, 'resolved'::text, 'dismissed'::text])))
);


--
-- Name: action_escalation_notification_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_escalation_notification_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_escalation_notification_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_escalation_notification_id_seq OWNED BY public.action_escalation_notification.id;


--
-- Name: action_event_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_event_log (
    id bigint NOT NULL,
    action_id bigint NOT NULL,
    event_type text NOT NULL,
    old_value text,
    new_value text,
    details text,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: action_event_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_event_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_event_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_event_log_id_seq OWNED BY public.action_event_log.id;


--
-- Name: action_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_id_seq OWNED BY public.action.id;


--
-- Name: action_status_comment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_status_comment (
    id bigint NOT NULL,
    action_id bigint NOT NULL,
    old_status text,
    new_status text NOT NULL,
    comment text,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: action_status_comment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.action_status_comment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: action_status_comment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.action_status_comment_id_seq OWNED BY public.action_status_comment.id;


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: app_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_user (
    id bigint NOT NULL,
    email text NOT NULL,
    full_name text,
    hashed_password text NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    is_active boolean,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_app_user_role_supported CHECK ((role = ANY (ARRAY['user'::text, 'manager'::text, 'admin'::text])))
);


--
-- Name: app_user_entra_binding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.app_user_entra_binding (
    object_id text NOT NULL,
    user_id bigint NOT NULL,
    bound_via text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_app_user_entra_binding_bound_via CHECK ((bound_via = ANY (ARRAY['configured'::text, 'email'::text, 'created'::text])))
);


--
-- Name: app_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.app_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: app_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.app_user_id_seq OWNED BY public.app_user.id;


--
-- Name: ext_agent_audit_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_audit_event (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    run_id bigint,
    detected_issue_id bigint,
    actor_type character varying NOT NULL,
    actor_identifier text,
    event_type character varying NOT NULL,
    event_outcome character varying NOT NULL,
    resource_type character varying NOT NULL,
    resource_identifier text,
    event_data jsonb NOT NULL,
    correlation_id text,
    event_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_agent_audit_event_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_audit_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_audit_event_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_audit_event_id_seq OWNED BY public.ext_agent_audit_event.id;


--
-- Name: ext_agent_definition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_definition (
    id bigint NOT NULL,
    agent_key character varying NOT NULL,
    agent_name text NOT NULL,
    business_purpose text,
    status character varying DEFAULT 'draft'::character varying NOT NULL,
    current_step integer DEFAULT 1 NOT NULL,
    specification jsonb NOT NULL,
    schedule jsonb,
    recipient_context jsonb,
    source_folder_url text,
    version integer DEFAULT 1 NOT NULL,
    created_by text,
    updated_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_agent_definition_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_definition_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_definition_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_definition_id_seq OWNED BY public.ext_agent_definition.id;


--
-- Name: ext_agent_definition_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_definition_versions (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    version_number integer NOT NULL,
    step_number integer,
    change_kind character varying NOT NULL,
    definition_json jsonb NOT NULL,
    changed_by text,
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_agent_definition_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_definition_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_definition_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_definition_versions_id_seq OWNED BY public.ext_agent_definition_versions.id;


--
-- Name: ext_agent_delivery_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_delivery_log (
    id bigint NOT NULL,
    run_id bigint NOT NULL,
    delivery_key character varying NOT NULL,
    recipient_email text NOT NULL,
    sender_identity text,
    subject text NOT NULL,
    delivery_status character varying NOT NULL,
    attempt_number integer DEFAULT 1 NOT NULL,
    scheduled_at timestamp with time zone NOT NULL,
    sent_at timestamp with time zone,
    provider_message_id text,
    digest_hash text,
    error_data jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_agent_delivery_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_delivery_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_delivery_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_delivery_log_id_seq OWNED BY public.ext_agent_delivery_log.id;


--
-- Name: ext_agent_detected_issue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_detected_issue (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    issue_key character varying NOT NULL,
    source_type character varying NOT NULL,
    source_reference text,
    owner_email text,
    importance_score numeric,
    rank_position integer,
    issue_status character varying NOT NULL,
    classification_confidence numeric,
    issue_data jsonb NOT NULL,
    first_detected_at timestamp with time zone NOT NULL,
    last_detected_at timestamp with time zone NOT NULL,
    closed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_agent_detected_issue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_detected_issue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_detected_issue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_detected_issue_id_seq OWNED BY public.ext_agent_detected_issue.id;


--
-- Name: ext_agent_issue_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_issue_history (
    id bigint NOT NULL,
    detected_issue_id bigint NOT NULL,
    run_id bigint,
    previous_status character varying,
    new_status character varying NOT NULL,
    previous_score numeric,
    new_score numeric,
    change_reason text,
    change_data jsonb,
    changed_at timestamp with time zone NOT NULL
);


--
-- Name: ext_agent_issue_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_issue_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_issue_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_issue_history_id_seq OWNED BY public.ext_agent_issue_history.id;


--
-- Name: ext_agent_issue_source; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_issue_source (
    id bigint NOT NULL,
    detected_issue_id bigint NOT NULL,
    source_type character varying NOT NULL,
    source_system character varying NOT NULL,
    source_record_id text,
    source_url text,
    source_timestamp timestamp with time zone,
    evidence_excerpt text,
    classification_reason text,
    confidence_score numeric,
    source_data jsonb,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: ext_agent_issue_source_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_issue_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_issue_source_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_issue_source_id_seq OWNED BY public.ext_agent_issue_source.id;


--
-- Name: ext_agent_review_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_review_queue (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    detected_issue_id bigint,
    run_id bigint,
    review_reason text NOT NULL,
    review_status character varying DEFAULT '''pending'''::character varying NOT NULL,
    assigned_reviewer_email text,
    sensitivity_class character varying,
    proposed_classification jsonb NOT NULL,
    review_decision jsonb,
    due_at timestamp with time zone,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_agent_review_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_review_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_review_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_review_queue_id_seq OWNED BY public.ext_agent_review_queue.id;


--
-- Name: ext_agent_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_run (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    run_key character varying NOT NULL,
    scheduled_for timestamp with time zone NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    run_status character varying NOT NULL,
    source_status jsonb NOT NULL,
    issues_detected integer,
    issues_sent integer,
    digest_reference text,
    error_data jsonb,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: ext_agent_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_run_id_seq OWNED BY public.ext_agent_run.id;


--
-- Name: ext_agent_runtime_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_runtime_config (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    environment character varying NOT NULL,
    config_version integer NOT NULL,
    config_status character varying NOT NULL,
    effective_from timestamp with time zone,
    effective_to timestamp with time zone,
    model_version character varying,
    prompt_version character varying,
    config_data jsonb NOT NULL,
    approved_by text,
    approved_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: ext_agent_runtime_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_runtime_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_runtime_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_runtime_config_id_seq OWNED BY public.ext_agent_runtime_config.id;


--
-- Name: ext_agent_test_result; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_agent_test_result (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    test_case_id character varying NOT NULL,
    test_suite character varying NOT NULL,
    environment character varying NOT NULL,
    test_status character varying NOT NULL,
    expected_result text,
    actual_result text,
    evidence_reference text,
    executed_by text,
    executed_at timestamp with time zone,
    result_data jsonb,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: ext_agent_test_result_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_agent_test_result_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_agent_test_result_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_agent_test_result_id_seq OWNED BY public.ext_agent_test_result.id;


--
-- Name: ext_ai_daily_reporting_cycle; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_ai_daily_reporting_cycle (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    run_id bigint,
    reporting_date date NOT NULL,
    cycle_type character varying DEFAULT '''daily'''::character varying NOT NULL,
    timezone character varying DEFAULT '''Africa/Tunis'''::character varying NOT NULL,
    request_scheduled_at timestamp with time zone NOT NULL,
    response_deadline_at timestamp with time zone NOT NULL,
    consolidation_scheduled_at timestamp with time zone NOT NULL,
    reminder_scheduled_at timestamp with time zone,
    report_sent_at timestamp with time zone,
    cycle_status character varying DEFAULT '''planned'''::character varying NOT NULL,
    expected_developer_count integer DEFAULT 0 NOT NULL,
    sent_count integer DEFAULT 0 NOT NULL,
    response_count integer DEFAULT 0 NOT NULL,
    on_time_response_count integer DEFAULT 0 NOT NULL,
    missing_response_count integer DEFAULT 0 NOT NULL,
    late_response_count integer DEFAULT 0 NOT NULL,
    summary_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_ai_daily_reporting_cycle_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_ai_daily_reporting_cycle_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_ai_daily_reporting_cycle_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_ai_daily_reporting_cycle_id_seq OWNED BY public.ext_ai_daily_reporting_cycle.id;


--
-- Name: ext_ai_developer_pause; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_ai_developer_pause (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    people_id bigint NOT NULL,
    developer_email text,
    pause_type character varying NOT NULL,
    pause_reason text,
    start_date date NOT NULL,
    expected_return_date date,
    is_active boolean DEFAULT true NOT NULL,
    resume_status character varying DEFAULT '''paused'''::character varying NOT NULL,
    resume_detected_at timestamp with time zone,
    source_message_id text,
    source_evidence_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_ai_developer_pause_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_ai_developer_pause_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_ai_developer_pause_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_ai_developer_pause_id_seq OWNED BY public.ext_ai_developer_pause.id;


--
-- Name: ext_ai_developer_submission; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_ai_developer_submission (
    id bigint NOT NULL,
    cycle_id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    people_id bigint NOT NULL,
    developer_name text,
    developer_email text,
    request_delivery_log_id bigint,
    request_message_id text,
    response_message_id text,
    response_received_at timestamp with time zone,
    response_status character varying DEFAULT '''expected'''::character varying NOT NULL,
    is_on_time boolean,
    is_complete boolean,
    raw_response_text text,
    parsed_response_json jsonb,
    inferred_project_count integer DEFAULT 0 NOT NULL,
    consecutive_missed_count integer DEFAULT 0 NOT NULL,
    classification_confidence numeric,
    correction_status character varying DEFAULT '''not_reviewed'''::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_ai_developer_submission_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_ai_developer_submission_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_ai_developer_submission_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_ai_developer_submission_id_seq OWNED BY public.ext_ai_developer_submission.id;


--
-- Name: ext_ai_project_update; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_ai_project_update (
    id bigint NOT NULL,
    submission_id bigint NOT NULL,
    cycle_id bigint NOT NULL,
    people_id bigint NOT NULL,
    project_key character varying NOT NULL,
    project_name text NOT NULL,
    project_status character varying NOT NULL,
    status_explanation text,
    status_trend character varying,
    completed_today text,
    next_plan text,
    blockers text,
    difficulties text,
    successes text,
    overdue_commitments text,
    management_support_recommendation text,
    source_history_days integer DEFAULT 3 NOT NULL,
    classification_confidence numeric,
    source_evidence_json jsonb,
    corrected_by text,
    corrected_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_ai_project_update_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_ai_project_update_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_ai_project_update_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_ai_project_update_id_seq OWNED BY public.ext_ai_project_update.id;


--
-- Name: ext_ai_tracking_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ext_ai_tracking_item (
    id bigint NOT NULL,
    agent_definition_id bigint NOT NULL,
    cycle_id bigint NOT NULL,
    submission_id bigint,
    project_update_id bigint,
    people_id bigint,
    project_key character varying,
    item_type character varying NOT NULL,
    item_key character varying NOT NULL,
    title text NOT NULL,
    description text,
    item_status character varying DEFAULT '''open'''::character varying NOT NULL,
    severity character varying,
    owner_email text,
    due_date date,
    first_seen_date date NOT NULL,
    last_seen_date date NOT NULL,
    age_working_days integer DEFAULT 0 NOT NULL,
    carry_forward boolean DEFAULT true NOT NULL,
    closed_at timestamp with time zone,
    source_evidence_json jsonb,
    action_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ext_ai_tracking_item_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ext_ai_tracking_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ext_ai_tracking_item_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ext_ai_tracking_item_id_seq OWNED BY public.ext_ai_tracking_item.id;


--
-- Name: sujet; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sujet (
    id bigint NOT NULL,
    code text,
    titre text NOT NULL,
    description text,
    parent_sujet_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    inserted_by text,
    source_application text,
    is_deleted boolean DEFAULT false NOT NULL,
    deleted_at timestamp with time zone,
    deleted_by text
);


--
-- Name: sujet_event_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sujet_event_log (
    id bigint NOT NULL,
    sujet_id bigint NOT NULL,
    sujet_code text,
    event_type text NOT NULL,
    old_values jsonb DEFAULT '{}'::jsonb NOT NULL,
    new_values jsonb DEFAULT '{}'::jsonb NOT NULL,
    updated_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sujet_event_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.sujet_event_log ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.sujet_event_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sujet_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sujet_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sujet_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sujet_id_seq OWNED BY public.sujet.id;


--
-- Name: action id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action ALTER COLUMN id SET DEFAULT nextval('public.action_id_seq'::regclass);


--
-- Name: action_attachment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_attachment ALTER COLUMN id SET DEFAULT nextval('public.action_attachment_id_seq'::regclass);


--
-- Name: action_escalation_email id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_email ALTER COLUMN id SET DEFAULT nextval('public.action_escalation_email_id_seq'::regclass);


--
-- Name: action_escalation_notification id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_notification ALTER COLUMN id SET DEFAULT nextval('public.action_escalation_notification_id_seq'::regclass);


--
-- Name: action_event_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_event_log ALTER COLUMN id SET DEFAULT nextval('public.action_event_log_id_seq'::regclass);


--
-- Name: action_status_comment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_status_comment ALTER COLUMN id SET DEFAULT nextval('public.action_status_comment_id_seq'::regclass);


--
-- Name: app_user id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user ALTER COLUMN id SET DEFAULT nextval('public.app_user_id_seq'::regclass);


--
-- Name: ext_agent_audit_event id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_audit_event ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_audit_event_id_seq'::regclass);


--
-- Name: ext_agent_definition id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_definition ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_definition_id_seq'::regclass);


--
-- Name: ext_agent_definition_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_definition_versions ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_definition_versions_id_seq'::regclass);


--
-- Name: ext_agent_delivery_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_delivery_log ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_delivery_log_id_seq'::regclass);


--
-- Name: ext_agent_detected_issue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_detected_issue ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_detected_issue_id_seq'::regclass);


--
-- Name: ext_agent_issue_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_issue_history ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_issue_history_id_seq'::regclass);


--
-- Name: ext_agent_issue_source id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_issue_source ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_issue_source_id_seq'::regclass);


--
-- Name: ext_agent_review_queue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_review_queue ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_review_queue_id_seq'::regclass);


--
-- Name: ext_agent_run id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_run ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_run_id_seq'::regclass);


--
-- Name: ext_agent_runtime_config id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_runtime_config ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_runtime_config_id_seq'::regclass);


--
-- Name: ext_agent_test_result id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_test_result ALTER COLUMN id SET DEFAULT nextval('public.ext_agent_test_result_id_seq'::regclass);


--
-- Name: ext_ai_daily_reporting_cycle id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_daily_reporting_cycle ALTER COLUMN id SET DEFAULT nextval('public.ext_ai_daily_reporting_cycle_id_seq'::regclass);


--
-- Name: ext_ai_developer_pause id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_developer_pause ALTER COLUMN id SET DEFAULT nextval('public.ext_ai_developer_pause_id_seq'::regclass);


--
-- Name: ext_ai_developer_submission id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_developer_submission ALTER COLUMN id SET DEFAULT nextval('public.ext_ai_developer_submission_id_seq'::regclass);


--
-- Name: ext_ai_project_update id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_project_update ALTER COLUMN id SET DEFAULT nextval('public.ext_ai_project_update_id_seq'::regclass);


--
-- Name: ext_ai_tracking_item id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_tracking_item ALTER COLUMN id SET DEFAULT nextval('public.ext_ai_tracking_item_id_seq'::regclass);


--
-- Name: sujet id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sujet ALTER COLUMN id SET DEFAULT nextval('public.sujet_id_seq'::regclass);


--
-- Name: action_attachment action_attachment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_attachment
    ADD CONSTRAINT action_attachment_pkey PRIMARY KEY (id);


--
-- Name: action_escalation_email action_escalation_email_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_email
    ADD CONSTRAINT action_escalation_email_pkey PRIMARY KEY (id);


--
-- Name: action_escalation_notification action_escalation_notification_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_notification
    ADD CONSTRAINT action_escalation_notification_pkey PRIMARY KEY (id);


--
-- Name: action_event_log action_event_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_event_log
    ADD CONSTRAINT action_event_log_pkey PRIMARY KEY (id);


--
-- Name: action action_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action
    ADD CONSTRAINT action_pkey PRIMARY KEY (id);


--
-- Name: action_status_comment action_status_comment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_status_comment
    ADD CONSTRAINT action_status_comment_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: app_user_entra_binding app_user_entra_binding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user_entra_binding
    ADD CONSTRAINT app_user_entra_binding_pkey PRIMARY KEY (object_id);


--
-- Name: app_user app_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT app_user_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_audit_event ext_agent_audit_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_audit_event
    ADD CONSTRAINT ext_agent_audit_event_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_definition ext_agent_definition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_definition
    ADD CONSTRAINT ext_agent_definition_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_definition_versions ext_agent_definition_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_definition_versions
    ADD CONSTRAINT ext_agent_definition_versions_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_delivery_log ext_agent_delivery_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_delivery_log
    ADD CONSTRAINT ext_agent_delivery_log_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_detected_issue ext_agent_detected_issue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_detected_issue
    ADD CONSTRAINT ext_agent_detected_issue_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_issue_history ext_agent_issue_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_issue_history
    ADD CONSTRAINT ext_agent_issue_history_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_issue_source ext_agent_issue_source_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_issue_source
    ADD CONSTRAINT ext_agent_issue_source_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_review_queue ext_agent_review_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_review_queue
    ADD CONSTRAINT ext_agent_review_queue_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_run ext_agent_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_run
    ADD CONSTRAINT ext_agent_run_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_runtime_config ext_agent_runtime_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_runtime_config
    ADD CONSTRAINT ext_agent_runtime_config_pkey PRIMARY KEY (id);


--
-- Name: ext_agent_test_result ext_agent_test_result_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_agent_test_result
    ADD CONSTRAINT ext_agent_test_result_pkey PRIMARY KEY (id);


--
-- Name: ext_ai_daily_reporting_cycle ext_ai_daily_reporting_cycle_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_daily_reporting_cycle
    ADD CONSTRAINT ext_ai_daily_reporting_cycle_pkey PRIMARY KEY (id);


--
-- Name: ext_ai_developer_pause ext_ai_developer_pause_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_developer_pause
    ADD CONSTRAINT ext_ai_developer_pause_pkey PRIMARY KEY (id);


--
-- Name: ext_ai_developer_submission ext_ai_developer_submission_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_developer_submission
    ADD CONSTRAINT ext_ai_developer_submission_pkey PRIMARY KEY (id);


--
-- Name: ext_ai_project_update ext_ai_project_update_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_project_update
    ADD CONSTRAINT ext_ai_project_update_pkey PRIMARY KEY (id);


--
-- Name: ext_ai_tracking_item ext_ai_tracking_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ext_ai_tracking_item
    ADD CONSTRAINT ext_ai_tracking_item_pkey PRIMARY KEY (id);


--
-- Name: sujet_event_log sujet_event_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sujet_event_log
    ADD CONSTRAINT sujet_event_log_pkey PRIMARY KEY (id);


--
-- Name: sujet sujet_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sujet
    ADD CONSTRAINT sujet_pkey PRIMARY KEY (id);


--
-- Name: action_escalation_email uq_action_escalation_email_action_stage; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_email
    ADD CONSTRAINT uq_action_escalation_email_action_stage UNIQUE (action_id, stage);


--
-- Name: app_user_entra_binding uq_app_user_entra_binding_user_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user_entra_binding
    ADD CONSTRAINT uq_app_user_entra_binding_user_id UNIQUE (user_id);


--
-- Name: sujet uq_sujet_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sujet
    ADD CONSTRAINT uq_sujet_code UNIQUE (code);


--
-- Name: idx_sujet_event_log_sujet_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sujet_event_log_sujet_created ON public.sujet_event_log USING btree (sujet_id, created_at DESC);


--
-- Name: ix_action_escalation_email_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_escalation_email_action_id ON public.action_escalation_email USING btree (action_id);


--
-- Name: ix_action_escalation_email_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_escalation_email_id ON public.action_escalation_email USING btree (id);


--
-- Name: ix_action_escalation_notification_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_escalation_notification_action_id ON public.action_escalation_notification USING btree (action_id);


--
-- Name: ix_action_escalation_notification_recipient_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_escalation_notification_recipient_email ON public.action_escalation_notification USING btree (recipient_email);


--
-- Name: ix_action_escalation_notification_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_escalation_notification_status ON public.action_escalation_notification USING btree (status);


--
-- Name: ix_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_id ON public.action USING btree (id);


--
-- Name: ix_action_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_is_deleted ON public.action USING btree (is_deleted);


--
-- Name: ix_app_user_email; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_app_user_email ON public.app_user USING btree (email);


--
-- Name: ix_app_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_app_user_id ON public.app_user USING btree (id);


--
-- Name: ix_sujet_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sujet_id ON public.sujet USING btree (id);


--
-- Name: ix_sujet_is_deleted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sujet_is_deleted ON public.sujet USING btree (is_deleted);


--
-- Name: uq_action_escalation_pending_recipient; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_action_escalation_pending_recipient ON public.action_escalation_notification USING btree (action_id, recipient_email, escalation_level) WHERE (status = 'pending'::text);


--
-- Name: action_attachment action_attachment_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_attachment
    ADD CONSTRAINT action_attachment_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.action(id) ON DELETE CASCADE;


--
-- Name: action_escalation_email action_escalation_email_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_email
    ADD CONSTRAINT action_escalation_email_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.action(id) ON DELETE CASCADE;


--
-- Name: action_escalation_email action_escalation_email_notification_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_email
    ADD CONSTRAINT action_escalation_email_notification_id_fkey FOREIGN KEY (notification_id) REFERENCES public.action_escalation_notification(id) ON DELETE SET NULL;


--
-- Name: action_escalation_notification action_escalation_notification_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_escalation_notification
    ADD CONSTRAINT action_escalation_notification_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.action(id) ON DELETE CASCADE;


--
-- Name: action_event_log action_event_log_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_event_log
    ADD CONSTRAINT action_event_log_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.action(id) ON DELETE CASCADE;


--
-- Name: action action_parent_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action
    ADD CONSTRAINT action_parent_action_id_fkey FOREIGN KEY (parent_action_id) REFERENCES public.action(id) ON DELETE CASCADE;


--
-- Name: action_status_comment action_status_comment_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_status_comment
    ADD CONSTRAINT action_status_comment_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.action(id) ON DELETE CASCADE;


--
-- Name: action action_sujet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action
    ADD CONSTRAINT action_sujet_id_fkey FOREIGN KEY (sujet_id) REFERENCES public.sujet(id) ON DELETE CASCADE;


--
-- Name: app_user_entra_binding app_user_entra_binding_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.app_user_entra_binding
    ADD CONSTRAINT app_user_entra_binding_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.app_user(id) ON DELETE CASCADE;


--
-- Name: sujet_event_log fk_sujet_event_log_sujet; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sujet_event_log
    ADD CONSTRAINT fk_sujet_event_log_sujet FOREIGN KEY (sujet_id) REFERENCES public.sujet(id);


--
-- Name: sujet fk_sujet_parent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sujet
    ADD CONSTRAINT fk_sujet_parent FOREIGN KEY (parent_sujet_id) REFERENCES public.sujet(id) ON UPDATE CASCADE ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

