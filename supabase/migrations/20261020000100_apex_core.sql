-- APEX core schema (migration 0001).
--
-- Design notes
--  * Everything lives in the dedicated `apex` schema. Supabase's Data API (PostgREST) only exposes
--    schemas listed in the project's API settings (public/graphql_public by default), so these tables
--    are not reachable with anon/authenticated keys even before RLS is considered.
--  * RLS is ENABLED on every table. The only policies are for the NOLOGIN role `apex_member`, which the
--    Vercel server assumes (SET LOCAL ROLE) for every member-scoped request, together with
--    `apex.actor_user_id`. Position data is therefore filtered by SQL, not by UI code.
--  * Event tables are append-only: UPDATE is rejected by trigger for every role.
--  * All instants are timestamptz (UTC). Money and quantities are exact numeric.

CREATE SCHEMA IF NOT EXISTS apex;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'apex_member') THEN
    CREATE ROLE apex_member NOLOGIN;
  END IF;
  BEGIN
    EXECUTE format('GRANT apex_member TO %I', current_user);
  EXCEPTION WHEN others THEN
    RAISE NOTICE 'could not grant apex_member to %, SET ROLE may require superuser: %', current_user, SQLERRM;
  END;
END $$;

REVOKE ALL ON SCHEMA apex FROM PUBLIC;
GRANT USAGE ON SCHEMA apex TO apex_member;

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON SCHEMA apex FROM %I', r);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------------------------------------
-- helpers
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION apex.current_actor() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('apex.actor_user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION apex.reject_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'apex: % is append-only (UPDATE rejected)', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

CREATE OR REPLACE FUNCTION apex.reject_delete_unless_retention() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF coalesce(current_setting('apex.retention_job', true), '') = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION 'apex: % rows are retained; DELETE only via the retention job', TG_TABLE_NAME USING ERRCODE = '42501';
END $$;

CREATE OR REPLACE FUNCTION apex.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------------------------
-- identity
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.app_users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  discord_user_id text UNIQUE CHECK (discord_user_id ~ '^[1-9][0-9]{14,20}$'),
  role text NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  created_at timestamptz NOT NULL DEFAULT now(),
  disabled_at timestamptz,
  consent_version text,
  consented_at timestamptz,
  dm_opt_in boolean NOT NULL DEFAULT false,
  high_priority_mentions boolean NOT NULL DEFAULT false,
  is_demo boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX app_users_single_owner ON apex.app_users ((role)) WHERE role = 'owner' AND disabled_at IS NULL;

CREATE TABLE apex.guilds (
  discord_guild_id text PRIMARY KEY CHECK (discord_guild_id ~ '^[1-9][0-9]{14,20}$'),
  allowlisted boolean NOT NULL DEFAULT false,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER guilds_touch BEFORE UPDATE ON apex.guilds FOR EACH ROW EXECUTE FUNCTION apex.touch_updated_at();

CREATE TABLE apex.guild_memberships (
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  guild_id text NOT NULL REFERENCES apex.guilds(discord_guild_id) ON DELETE CASCADE,
  verified_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  PRIMARY KEY (user_id, guild_id)
);

CREATE TABLE apex.admin_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  auth_method text NOT NULL CHECK (auth_method IN ('discord_oauth', 'local_dev')),
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  user_agent_hash text
);
CREATE INDEX admin_sessions_owner ON apex.admin_sessions (owner_user_id);

-- ---------------------------------------------------------------------------------------------
-- configuration and secrets (ciphertext only)
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.encrypted_integrations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL CHECK (provider ~ '^[a-z0-9_]{2,32}$'),
  field text NOT NULL CHECK (field ~ '^[a-z0-9_]{2,32}$'),
  ciphertext bytea NOT NULL,
  iv bytea NOT NULL CHECK (octet_length(iv) = 12),
  auth_tag bytea NOT NULL CHECK (octet_length(auth_tag) = 16),
  key_version integer NOT NULL CHECK (key_version >= 1),
  secret_version integer NOT NULL DEFAULT 1,
  hint text CHECK (hint IS NULL OR length(hint) <= 8),
  configured_by uuid REFERENCES apex.app_users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, field)
);
CREATE TRIGGER encrypted_integrations_touch BEFORE UPDATE ON apex.encrypted_integrations
  FOR EACH ROW EXECUTE FUNCTION apex.touch_updated_at();

CREATE TABLE apex.integration_status (
  provider text PRIMARY KEY CHECK (provider ~ '^[a-z0-9_]{2,32}$'),
  status text NOT NULL DEFAULT 'NOT_CONFIGURED'
    CHECK (status IN ('NOT_CONFIGURED', 'TEST_FAILED', 'CONNECTED', 'RATE_LIMITED', 'STALE', 'EXPIRED', 'DISABLED')),
  tested_at timestamptz,
  detail text CHECK (detail IS NULL OR length(detail) <= 240),
  secret_version integer NOT NULL DEFAULT 0,
  pi_ack_version integer NOT NULL DEFAULT 0,
  pi_acked_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER integration_status_touch BEFORE UPDATE ON apex.integration_status
  FOR EACH ROW EXECUTE FUNCTION apex.touch_updated_at();

CREATE TABLE apex.settings (
  key text PRIMARY KEY CHECK (key ~ '^[a-z0-9_.]{2,64}$'),
  value_json jsonb NOT NULL,
  version integer NOT NULL DEFAULT 1,
  updated_by uuid REFERENCES apex.app_users(id) ON DELETE SET NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE apex.settings_history (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key text NOT NULL,
  redacted_change jsonb NOT NULL,
  actor_id uuid,
  version integer NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER settings_history_append_only BEFORE UPDATE ON apex.settings_history
  FOR EACH ROW EXECUTE FUNCTION apex.reject_update();

CREATE TABLE apex.compliance_flags (
  flag text PRIMARY KEY CHECK (flag IN ('ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY', 'ALLOW_PERSONALIZED_GROUP_GUIDANCE',
                                        'ALLOW_COMMERCIAL_HOSTING')),
  enabled boolean NOT NULL DEFAULT false,
  acknowledged_by uuid REFERENCES apex.app_users(id) ON DELETE SET NULL,
  acknowledged_at timestamptz,
  acknowledgement_text text,
  external_review_reference text,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT enabled OR (acknowledged_by IS NOT NULL AND acknowledged_at IS NOT NULL
                         AND coalesce(length(external_review_reference), 0) >= 8))
);
INSERT INTO apex.compliance_flags (flag) VALUES
  ('ALLOW_EXTERNAL_MARKET_DATA_REDISPLAY'), ('ALLOW_PERSONALIZED_GROUP_GUIDANCE'), ('ALLOW_COMMERCIAL_HOSTING');

-- ---------------------------------------------------------------------------------------------
-- agents (Raspberry Pi) and pairing
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.agent_devices (
  id text PRIMARY KEY CHECK (id ~ '^agt_[a-z0-9]{8,32}$'),
  device_name text NOT NULL CHECK (length(device_name) BETWEEN 1 AND 48),
  status text NOT NULL DEFAULT 'pending_approval' CHECK (status IN ('pending_approval', 'active', 'revoked')),
  x25519_public_key text CHECK (x25519_public_key IS NULL OR x25519_public_key ~ '^[A-Za-z0-9_-]{43}$'),
  ed25519_public_key text CHECK (ed25519_public_key IS NULL OR ed25519_public_key ~ '^[A-Za-z0-9_-]{43}$'),
  fingerprint text,
  hmac_secret_version integer NOT NULL DEFAULT 1,
  secret_version_ack integer NOT NULL DEFAULT 0,
  worker_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz,
  approved_by uuid REFERENCES apex.app_users(id) ON DELETE SET NULL,
  last_seen_at timestamptz,
  last_clock_skew_ms integer,
  revoked_at timestamptz,
  CHECK (status <> 'revoked' OR revoked_at IS NOT NULL)
);

CREATE TABLE apex.agent_nonces (
  nonce text PRIMARY KEY CHECK (nonce ~ '^[A-Za-z0-9_-]{22,128}$'),
  agent_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
CREATE INDEX agent_nonces_expiry ON apex.agent_nonces (expires_at);

CREATE TABLE apex.pairing_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code_hash text NOT NULL UNIQUE,
  server_nonce text NOT NULL,
  created_by uuid REFERENCES apex.app_users(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  device_id text REFERENCES apex.agent_devices(id) ON DELETE SET NULL,
  failed_attempts integer NOT NULL DEFAULT 0
);

CREATE TABLE apex.secret_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id text NOT NULL REFERENCES apex.agent_devices(id) ON DELETE CASCADE,
  provider text NOT NULL,
  secret_version integer NOT NULL,
  issued_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  acked_at timestamptz
);

CREATE TABLE apex.agent_heartbeats (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agent_id text NOT NULL REFERENCES apex.agent_devices(id) ON DELETE CASCADE,
  received_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz NOT NULL,
  clock_skew_ms integer NOT NULL,
  payload_json jsonb NOT NULL
);
CREATE INDEX agent_heartbeats_recent ON apex.agent_heartbeats (agent_id, received_at DESC);

-- ---------------------------------------------------------------------------------------------
-- market data (curated; raw ticks stay on the Pi)
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.market_symbols (
  symbol text PRIMARY KEY CHECK (symbol ~ '^[A-Z][A-Z0-9.]{0,9}$'),
  name text CHECK (name IS NULL OR length(name) <= 120),
  exchange text,
  asset_class text NOT NULL DEFAULT 'us_equity',
  instrument text NOT NULL DEFAULT 'common',
  sector text,
  active boolean NOT NULL DEFAULT true,
  tradability_status text NOT NULL DEFAULT 'unknown'
    CHECK (tradability_status IN ('likely_eligible', 'user_verified', 'unknown', 'excluded')),
  tradability_checked_at timestamptz,
  eligible_reason text,
  universe_member boolean NOT NULL DEFAULT false,
  is_demo boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE apex.quote_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  symbol text NOT NULL,
  ts_exchange timestamptz,
  ts_received timestamptz NOT NULL,
  provider text NOT NULL,
  feed text NOT NULL CHECK (feed IN ('iex', 'sip', 'demo', 'none')),
  latency_class text NOT NULL CHECK (latency_class IN ('live', 'delayed', 'historical', 'replay')),
  kind text NOT NULL CHECK (kind IN ('trade', 'quote', 'bar_close')),
  price numeric(18, 6) NOT NULL CHECK (price >= 0),
  bid numeric(18, 6),
  ask numeric(18, 6),
  volume bigint,
  quality_flags text[] NOT NULL DEFAULT '{}',
  validation_level smallint NOT NULL CHECK (validation_level BETWEEN 0 AND 3),
  delayed_by_seconds integer,
  is_demo boolean NOT NULL DEFAULT false
);
CREATE INDEX quote_events_symbol_time ON apex.quote_events (symbol, ts_received DESC);

CREATE TABLE apex.latest_quotes (
  symbol text PRIMARY KEY,
  observation_json jsonb NOT NULL,
  price numeric(18, 6) NOT NULL,
  feed text NOT NULL,
  latency_class text NOT NULL,
  ts_exchange timestamptz,
  ts_received timestamptz NOT NULL,
  validation_level smallint NOT NULL,
  is_demo boolean NOT NULL DEFAULT false
);

CREATE TABLE apex.daily_bars (
  symbol text NOT NULL,
  session_date date NOT NULL,
  adjustment text NOT NULL CHECK (adjustment IN ('raw', 'split', 'dividend', 'all')),
  feed text NOT NULL CHECK (feed IN ('iex', 'sip', 'demo')),
  provider text NOT NULL,
  open numeric(18, 6) NOT NULL,
  high numeric(18, 6) NOT NULL,
  low numeric(18, 6) NOT NULL,
  close numeric(18, 6) NOT NULL,
  volume bigint NOT NULL,
  vwap numeric(18, 6),
  is_provisional boolean NOT NULL DEFAULT false,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  asof_at timestamptz NOT NULL,
  is_demo boolean NOT NULL DEFAULT false,
  PRIMARY KEY (symbol, session_date, adjustment, feed),
  CHECK (low <= high AND open BETWEEN low AND high AND close BETWEEN low AND high AND low >= 0)
);

CREATE TABLE apex.feature_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol text NOT NULL,
  asof_at timestamptz NOT NULL,
  session_date date,
  feature_version text NOT NULL,
  features_json jsonb NOT NULL,
  sources_json jsonb NOT NULL,
  integrity_hash text NOT NULL CHECK (integrity_hash ~ '^[0-9a-f]{64}$'),
  is_demo boolean NOT NULL DEFAULT false,
  UNIQUE (symbol, asof_at, feature_version)
);

CREATE TABLE apex.market_regimes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  asof_at timestamptz NOT NULL,
  regime text NOT NULL,
  state_probabilities jsonb NOT NULL,
  inputs_json jsonb NOT NULL,
  version text NOT NULL,
  is_demo boolean NOT NULL DEFAULT false,
  UNIQUE (asof_at, version, is_demo)
);

CREATE TABLE apex.scan_runs (
  id text PRIMARY KEY,
  agent_id text,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  asof_at timestamptz NOT NULL,
  universe_size integer NOT NULL DEFAULT 0,
  eligible_count integer NOT NULL DEFAULT 0,
  scanned_count integer NOT NULL DEFAULT 0,
  candidate_count integer NOT NULL DEFAULT 0,
  published_count integer NOT NULL DEFAULT 0,
  no_trade_reason text,
  metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_demo boolean NOT NULL DEFAULT false
);

-- ---------------------------------------------------------------------------------------------
-- catalysts
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.catalysts (
  id text PRIMARY KEY CHECK (id ~ '^cat_[0-9a-f]{24}$'),
  symbol text,
  cik text,
  catalyst_type text NOT NULL,
  source text NOT NULL,
  event_at timestamptz,
  published_at timestamptz,
  first_seen_at timestamptz NOT NULL,
  available_at timestamptz NOT NULL,
  canonical_url text CHECK (canonical_url IS NULL OR canonical_url ~ '^https://'),
  headline text NOT NULL CHECK (length(headline) <= 200),
  source_grade text NOT NULL,
  direction text NOT NULL CHECK (direction IN ('potentially_positive', 'potentially_negative', 'mixed', 'unknown')),
  entity_confidence numeric(4, 3) NOT NULL CHECK (entity_confidence BETWEEN 0 AND 1),
  extraction_confidence numeric(4, 3) NOT NULL CHECK (extraction_confidence BETWEEN 0 AND 1),
  impact_score numeric(6, 2),
  actionable_age text,
  cluster_id text,
  revision_of text,
  reason_codes text[] NOT NULL DEFAULT '{}',
  regulatory_flags text[] NOT NULL DEFAULT '{}',
  parsed_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_hash text NOT NULL UNIQUE,
  legal_classification text NOT NULL DEFAULT 'public_metadata_link_only',
  parser_version text NOT NULL,
  is_demo boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN apex.catalysts.available_at IS
  'Earliest instant the algorithm may use this item. Live: first_seen_at. Historical backfill: published_at plus a conservative ingestion lag. Backtests filter on available_at <= decision time.';
CREATE INDEX catalysts_symbol_available ON apex.catalysts (symbol, available_at DESC);

-- ---------------------------------------------------------------------------------------------
-- signals (immutable revisions + append-only events + head projection)
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.signal_candidates (
  id text PRIMARY KEY CHECK (id ~ '^cand_[0-9a-f]{24}$'),
  scan_run_id text REFERENCES apex.scan_runs(id) ON DELETE SET NULL,
  symbol text NOT NULL,
  created_at timestamptz NOT NULL,
  asof_at timestamptz NOT NULL,
  strategy_id text NOT NULL,
  strategy_version text NOT NULL,
  quality_score numeric(6, 2) CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 100),
  gate_status text NOT NULL CHECK (gate_status IN ('PASS', 'WATCH', 'VETO')),
  reason_codes text[] NOT NULL DEFAULT '{}',
  explanation_json jsonb NOT NULL,
  is_demo boolean NOT NULL DEFAULT false
);
CREATE INDEX signal_candidates_asof ON apex.signal_candidates (asof_at DESC);

CREATE TABLE apex.signals (
  id text NOT NULL CHECK (id ~ '^sig_[0-9a-f]{24}$'),
  revision integer NOT NULL CHECK (revision >= 1),
  symbol text NOT NULL,
  side text NOT NULL DEFAULT 'LONG' CHECK (side = 'LONG'),
  state_at_revision text NOT NULL,
  entry_min numeric(18, 6) NOT NULL CHECK (entry_min > 0),
  entry_max numeric(18, 6) NOT NULL CHECK (entry_max >= entry_min),
  stop_level numeric(18, 6) NOT NULL CHECK (stop_level > 0 AND stop_level < entry_min),
  target_primary numeric(18, 6) NOT NULL CHECK (target_primary > entry_max),
  target_secondary numeric(18, 6),
  horizon_min_sessions smallint NOT NULL CHECK (horizon_min_sessions BETWEEN 1 AND 20),
  horizon_max_sessions smallint NOT NULL CHECK (horizon_max_sessions BETWEEN horizon_min_sessions AND 20),
  entry_expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL,
  asof_at timestamptz NOT NULL,
  published_at timestamptz,
  feed text NOT NULL,
  latency_class text NOT NULL,
  strategy_id text NOT NULL,
  model_version text NOT NULL,
  policy_version text NOT NULL,
  quality_score numeric(6, 2) NOT NULL,
  confidence_status text NOT NULL CHECK (confidence_status IN ('CALIBRATED', 'EXPERIMENTAL', 'UNVALIDATED')),
  confidence_probability numeric(6, 5),
  confidence_interval jsonb,
  confidence_label text NOT NULL,
  release_mode text NOT NULL CHECK (release_mode IN ('DEMO', 'PAPER_LIVE_DATA', 'PERSONAL_RESEARCH', 'GROUP_POSITION_ALERTS')),
  immutable_snapshot jsonb NOT NULL,
  immutable_hash text NOT NULL CHECK (immutable_hash ~ '^[0-9a-f]{64}$'),
  is_demo boolean NOT NULL,
  PRIMARY KEY (id, revision),
  CHECK (is_demo = (release_mode = 'DEMO')),
  CHECK (confidence_status <> 'UNVALIDATED' OR confidence_probability IS NULL)
);
CREATE TRIGGER signals_immutable BEFORE UPDATE ON apex.signals FOR EACH ROW EXECUTE FUNCTION apex.reject_update();

CREATE TABLE apex.signal_heads (
  signal_id text PRIMARY KEY,
  symbol text NOT NULL,
  current_revision integer NOT NULL,
  state text NOT NULL CHECK (state IN ('DISCOVERED', 'EVALUATING', 'QUALIFIED', 'PUBLISHED', 'ACTIVE', 'CLOSED',
                                        'DISCARDED', 'NO_TRADE', 'EXPIRED', 'INVALIDATED')),
  outcome text,
  is_demo boolean NOT NULL,
  published_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (signal_id, current_revision) REFERENCES apex.signals(id, revision)
);
CREATE INDEX signal_heads_state ON apex.signal_heads (state, updated_at DESC);

CREATE TABLE apex.signal_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signal_id text NOT NULL REFERENCES apex.signal_heads(signal_id) ON DELETE RESTRICT,
  event_type text NOT NULL,
  event_at timestamptz NOT NULL,
  from_state text,
  to_state text NOT NULL,
  cause text NOT NULL CHECK (length(cause) <= 240),
  evidence_asof_at timestamptz,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  dedupe_key text NOT NULL UNIQUE,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX signal_events_signal ON apex.signal_events (signal_id, event_at);
CREATE TRIGGER signal_events_append_only BEFORE UPDATE ON apex.signal_events FOR EACH ROW EXECUTE FUNCTION apex.reject_update();
CREATE TRIGGER signal_events_retained BEFORE DELETE ON apex.signal_events FOR EACH ROW EXECUTE FUNCTION apex.reject_delete_unless_retention();

-- ---------------------------------------------------------------------------------------------
-- member positions (self-reported; RLS by actor)
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.position_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  account_label text NOT NULL DEFAULT 'self-reported' CHECK (length(account_label) BETWEEN 1 AND 48),
  currency text NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
  paper_or_self_reported text NOT NULL DEFAULT 'self_reported' CHECK (paper_or_self_reported IN ('paper', 'self_reported')),
  hypothetical_risk_budget numeric(18, 2),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, account_label)
);

CREATE TABLE apex.positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES apex.position_accounts(id) ON DELETE CASCADE,
  symbol text NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.]{0,9}$'),
  signal_id text,
  state text NOT NULL CHECK (state IN ('WATCHING', 'OPEN', 'PARTIALLY_CLOSED', 'CLOSED')),
  total_quantity numeric(24, 6) NOT NULL DEFAULT 0 CHECK (total_quantity >= 0),
  avg_cost numeric(24, 6) NOT NULL DEFAULT 0,
  realized_pnl numeric(24, 6) NOT NULL DEFAULT 0,
  fees_total numeric(24, 6) NOT NULL DEFAULT 0,
  opened_at timestamptz,
  closed_at timestamptz,
  off_plan boolean NOT NULL DEFAULT false,
  off_plan_reasons text[] NOT NULL DEFAULT '{}',
  event_count integer NOT NULL DEFAULT 0,
  is_demo boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX positions_user ON apex.positions (user_id, state);
CREATE INDEX positions_symbol_open ON apex.positions (symbol) WHERE state <> 'CLOSED';
CREATE TRIGGER positions_touch BEFORE UPDATE ON apex.positions FOR EACH ROW EXECUTE FUNCTION apex.touch_updated_at();

CREATE TABLE apex.position_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  position_id uuid NOT NULL REFERENCES apex.positions(id) ON DELETE CASCADE,
  seq integer NOT NULL CHECK (seq >= 1),
  event_type text NOT NULL CHECK (event_type IN ('WATCH', 'UNWATCH', 'OPEN', 'ADD', 'PARTIAL_EXIT', 'CLOSE', 'CORRECT',
                                                 'CANCEL_CORRECTION')),
  price numeric(18, 6),
  quantity numeric(24, 6),
  fees numeric(18, 6),
  event_at timestamptz,
  correction_json jsonb,
  notes text CHECK (notes IS NULL OR length(notes) <= 280),
  source text NOT NULL CHECK (source IN ('discord', 'dashboard', 'replay', 'starter_import')),
  client_idempotency_key text NOT NULL CHECK (client_idempotency_key ~ '^[A-Za-z0-9:_-]{8,128}$'),
  discord_interaction_id text,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, client_idempotency_key),
  UNIQUE (position_id, seq),
  CHECK (price IS NULL OR price > 0),
  CHECK (quantity IS NULL OR quantity > 0),
  CHECK (fees IS NULL OR fees >= 0)
);
CREATE TRIGGER position_events_append_only BEFORE UPDATE ON apex.position_events FOR EACH ROW EXECUTE FUNCTION apex.reject_update();

-- lots are a projection rebuilt from position_events on every accepted write
CREATE TABLE apex.position_lots (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  position_id uuid NOT NULL REFERENCES apex.positions(id) ON DELETE CASCADE,
  account_id uuid NOT NULL REFERENCES apex.position_accounts(id) ON DELETE CASCADE,
  signal_id text,
  symbol text NOT NULL,
  fill_at timestamptz NOT NULL,
  fill_price numeric(18, 6) NOT NULL CHECK (fill_price > 0),
  quantity_initial numeric(24, 6) NOT NULL CHECK (quantity_initial > 0),
  quantity_remaining numeric(24, 6) NOT NULL CHECK (quantity_remaining >= 0 AND quantity_remaining <= quantity_initial),
  fees numeric(18, 6) NOT NULL DEFAULT 0,
  currency text NOT NULL DEFAULT 'USD',
  source text NOT NULL,
  revision integer NOT NULL
);
CREATE INDEX position_lots_position ON apex.position_lots (position_id);

CREATE TABLE apex.position_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  position_id uuid NOT NULL REFERENCES apex.positions(id) ON DELETE CASCADE,
  symbol text NOT NULL,
  asof_at timestamptz NOT NULL,
  assessment_key text NOT NULL,
  avg_cost numeric(24, 6) NOT NULL,
  total_quantity numeric(24, 6) NOT NULL,
  unrealized_pnl numeric(24, 6),
  source_age_seconds integer,
  assessment_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (position_id, assessment_key)
);
CREATE INDEX position_snapshots_position ON apex.position_snapshots (position_id, asof_at DESC);

CREATE TABLE apex.notification_subscriptions (
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  event_type text NOT NULL CHECK (event_type IN ('POSITION_UPDATE', 'SIGNAL_UPDATE', 'NEW_SIGNAL', 'SYSTEM_ALERT')),
  channel_preference text NOT NULL DEFAULT 'dm' CHECK (channel_preference IN ('dm', 'ephemeral_only', 'none')),
  min_priority text NOT NULL DEFAULT 'NORMAL' CHECK (min_priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')),
  quiet_hours jsonb,
  enabled boolean NOT NULL DEFAULT true,
  PRIMARY KEY (user_id, event_type)
);

CREATE TABLE apex.symbol_mutes (
  user_id uuid NOT NULL REFERENCES apex.app_users(id) ON DELETE CASCADE,
  symbol text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, symbol)
);

-- ---------------------------------------------------------------------------------------------
-- transactional outbox
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.notification_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_key text NOT NULL UNIQUE CHECK (length(event_key) BETWEEN 8 AND 200),
  user_id uuid REFERENCES apex.app_users(id) ON DELETE CASCADE,
  route_json jsonb NOT NULL,
  kind text NOT NULL,
  priority text NOT NULL CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')),
  state text NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending', 'leased', 'sent', 'deferred', 'dead_letter', 'suppressed')),
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  leased_by text,
  payload_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  source_event_at timestamptz,
  sent_at timestamptz,
  discord_message_id text,
  rendered_json jsonb,  -- shared-channel render kept for the dashboard alert history (never stored for DMs)
  last_error text CHECK (last_error IS NULL OR length(last_error) <= 200),
  is_demo boolean NOT NULL DEFAULT false,
  CHECK ((route_json ->> 'type') IN ('CHANNEL', 'DM')),
  CHECK (rendered_json IS NULL OR (route_json ->> 'type') = 'CHANNEL'),
  CHECK ((route_json ->> 'type') <> 'DM' OR user_id IS NOT NULL)
);
CREATE INDEX notification_outbox_due ON apex.notification_outbox (state, next_attempt_at);

CREATE TABLE apex.delivery_attempts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  outbox_id uuid NOT NULL REFERENCES apex.notification_outbox(id) ON DELETE CASCADE,
  status text NOT NULL CHECK (status IN ('delivered', 'failed', 'rate_limited', 'dm_blocked', 'dead_letter')),
  http_status integer,
  rate_limit_until timestamptz,
  error_code text,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- operations, research and audit
-- ---------------------------------------------------------------------------------------------
CREATE TABLE apex.job_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_name text NOT NULL,
  agent_id text,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  success boolean,
  metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_error text CHECK (last_error IS NULL OR length(last_error) <= 400)
);
CREATE INDEX job_runs_recent ON apex.job_runs (job_name, started_at DESC);

CREATE TABLE apex.quota_usage (
  provider text NOT NULL,
  day date NOT NULL,
  requests integer NOT NULL DEFAULT 0,
  limit_per_day integer,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, day)
);

CREATE TABLE apex.backtest_runs (
  id text PRIMARY KEY,
  strategy_id text NOT NULL,
  config_hash text NOT NULL,
  data_hash text NOT NULL,
  code_hash text NOT NULL,
  data_source text NOT NULL CHECK (data_source IN ('historical_market_data', 'synthetic_fixture')),
  status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  started_at timestamptz NOT NULL,
  completed_at timestamptz,
  metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  bias_warnings text[] NOT NULL DEFAULT '{}',
  is_demo boolean NOT NULL DEFAULT false
);

CREATE TABLE apex.backtest_trades (
  run_id text NOT NULL REFERENCES apex.backtest_runs(id) ON DELETE CASCADE,
  seq integer NOT NULL,
  symbol text NOT NULL,
  signal_ref text,
  entry_at timestamptz,
  entry numeric(18, 6),
  exit_at timestamptz,
  exit numeric(18, 6),
  exit_reason text,
  return_net numeric(12, 6),
  r_multiple numeric(12, 6),
  mfe numeric(12, 6),
  mae numeric(12, 6),
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (run_id, seq)
);

-- Model registry (laptop-free cloud training amendment 2026-10-20).
-- A row is a signed release manifest; the compact JSON artifact (coefficients + calibrator, never a pickle)
-- lives in apex.model_artifacts. Only champion + a couple of rollback versions are retained.
CREATE TABLE apex.model_versions (
  id text PRIMARY KEY CHECK (id ~ '^mdl_[0-9a-z]{8,40}$'),
  version_uuid uuid NOT NULL UNIQUE,
  strategy_id text NOT NULL,
  model_kind text NOT NULL CHECK (model_kind IN ('logistic_competing_v1')),
  feature_schema_version text NOT NULL,
  features_hash text NOT NULL,
  label_definition jsonb NOT NULL,
  training_window jsonb NOT NULL,
  validation_window jsonb NOT NULL,
  training_data_hash text NOT NULL,
  code_sha text NOT NULL,
  calibration_metrics jsonb NOT NULL,
  evaluation_json jsonb NOT NULL,
  decision_thresholds jsonb NOT NULL,
  drift_criteria jsonb NOT NULL DEFAULT '{}'::jsonb,
  data_source text NOT NULL CHECK (data_source IN ('historical_market_data', 'synthetic_fixture')),
  artifact_size_bytes integer NOT NULL CHECK (artifact_size_bytes BETWEEN 2 AND 2000000),
  artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
  manifest_json jsonb NOT NULL,
  signature text NOT NULL,
  signer_key_version integer NOT NULL,
  storage_uri text NOT NULL,
  status text NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate', 'rejected', 'champion', 'retired', 'rolled_back')),
  promotion_report jsonb,
  rollback_parent text REFERENCES apex.model_versions(id) ON DELETE SET NULL,
  validation_expires_at timestamptz NOT NULL,
  training_run_id uuid,
  created_at timestamptz NOT NULL,
  registered_at timestamptz NOT NULL DEFAULT now(),
  promoted_at timestamptz,
  promoted_by text,
  is_demo boolean NOT NULL DEFAULT false,
  -- a model trained on synthetic fixtures can never become a non-demo champion
  CHECK (NOT (status = 'champion' AND data_source = 'synthetic_fixture' AND NOT is_demo))
);
CREATE UNIQUE INDEX model_versions_one_champion ON apex.model_versions (strategy_id, is_demo) WHERE status = 'champion';

CREATE TABLE apex.model_artifacts (
  model_id text PRIMARY KEY REFERENCES apex.model_versions(id) ON DELETE CASCADE,
  content_type text NOT NULL DEFAULT 'application/json' CHECK (content_type = 'application/json'),
  artifact bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE apex.training_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow text NOT NULL CHECK (workflow IN ('train_weekly', 'drift', 'release', 'local_replay')),
  trigger text NOT NULL CHECK (trigger IN ('schedule', 'workflow_dispatch', 'admin_dispatch', 'local')),
  github_run_id text,
  github_run_attempt integer,
  code_sha text,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'skipped_budget', 'skipped_holiday', 'cancelled')),
  outcome text CHECK (outcome IS NULL OR outcome IN ('candidate_registered', 'champion_retained', 'promoted', 'rejected',
                                                     'drift_ok', 'drift_breach', 'no_data')),
  candidate_model_id text,
  minutes_used numeric(8, 2),
  minutes_budget_month integer,
  minutes_used_month_estimate numeric(10, 2),
  report_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_demo boolean NOT NULL DEFAULT false,
  UNIQUE (workflow, github_run_id, github_run_attempt)
);
CREATE INDEX training_runs_recent ON apex.training_runs (workflow, started_at DESC);

CREATE TABLE apex.model_activations (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agent_id text NOT NULL REFERENCES apex.agent_devices(id) ON DELETE CASCADE,
  model_id text NOT NULL,
  status text NOT NULL CHECK (status IN ('activated', 'rejected', 'rolled_back', 'expired')),
  detail text CHECK (detail IS NULL OR length(detail) <= 240),
  reported_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_activations_recent ON apex.model_activations (agent_id, reported_at DESC);

CREATE TABLE apex.replay_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scenario text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_demo boolean NOT NULL DEFAULT true
);

CREATE TABLE apex.audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_type text NOT NULL CHECK (actor_type IN ('owner', 'agent', 'member', 'system')),
  actor_id text,
  entity text NOT NULL,
  entity_id text,
  action text NOT NULL,
  redacted_metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  correlation_id text,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_events_at ON apex.audit_events (at DESC);
CREATE INDEX audit_events_entity ON apex.audit_events (entity, entity_id);
CREATE TRIGGER audit_events_append_only BEFORE UPDATE ON apex.audit_events FOR EACH ROW EXECUTE FUNCTION apex.reject_update();
CREATE TRIGGER audit_events_retained BEFORE DELETE ON apex.audit_events FOR EACH ROW EXECUTE FUNCTION apex.reject_delete_unless_retention();

CREATE TABLE apex.security_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind text NOT NULL,
  agent_id text,
  route text,
  detail text CHECK (detail IS NULL OR length(detail) <= 200),
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX security_events_at ON apex.security_events (at DESC);

-- ---------------------------------------------------------------------------------------------
-- row level security
-- ---------------------------------------------------------------------------------------------
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'apex' LOOP
    EXECUTE format('ALTER TABLE apex.%I ENABLE ROW LEVEL SECURITY', t.tablename);
  END LOOP;
END $$;

-- members: own row only
GRANT SELECT ON apex.app_users TO apex_member;
GRANT UPDATE (dm_opt_in, high_priority_mentions, consent_version, consented_at) ON apex.app_users TO apex_member;
CREATE POLICY app_users_self ON apex.app_users FOR SELECT TO apex_member USING (id = apex.current_actor());
CREATE POLICY app_users_self_update ON apex.app_users FOR UPDATE TO apex_member
  USING (id = apex.current_actor()) WITH CHECK (id = apex.current_actor());

GRANT SELECT, INSERT ON apex.position_accounts TO apex_member;
CREATE POLICY position_accounts_owner ON apex.position_accounts FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

GRANT SELECT, INSERT, DELETE ON apex.positions TO apex_member;
GRANT UPDATE (state, total_quantity, avg_cost, realized_pnl, fees_total, opened_at, closed_at, off_plan, off_plan_reasons,
              event_count, updated_at) ON apex.positions TO apex_member;
CREATE POLICY positions_owner ON apex.positions FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

GRANT SELECT, INSERT, DELETE ON apex.position_events TO apex_member;
CREATE POLICY position_events_owner ON apex.position_events FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

GRANT SELECT, INSERT, DELETE ON apex.position_lots TO apex_member;
CREATE POLICY position_lots_owner ON apex.position_lots FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

GRANT SELECT, INSERT, DELETE ON apex.position_snapshots TO apex_member;
CREATE POLICY position_snapshots_owner ON apex.position_snapshots FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

GRANT SELECT, INSERT, UPDATE, DELETE ON apex.notification_subscriptions TO apex_member;
CREATE POLICY notification_subscriptions_owner ON apex.notification_subscriptions FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

GRANT SELECT, INSERT, DELETE ON apex.symbol_mutes TO apex_member;
CREATE POLICY symbol_mutes_owner ON apex.symbol_mutes FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor());

-- members may queue private notifications addressed to themselves only (e.g. confirmations)
GRANT SELECT, INSERT ON apex.notification_outbox TO apex_member;
CREATE POLICY notification_outbox_own_dm ON apex.notification_outbox FOR ALL TO apex_member
  USING (user_id = apex.current_actor()) WITH CHECK (user_id = apex.current_actor() AND (route_json ->> 'type') = 'DM');

-- shared research is readable once published; members never see internal candidates or drafts
GRANT SELECT ON apex.signal_heads, apex.signals TO apex_member;
CREATE POLICY signal_heads_published ON apex.signal_heads FOR SELECT TO apex_member
  USING (published_at IS NOT NULL);
CREATE POLICY signals_published ON apex.signals FOR SELECT TO apex_member
  USING (EXISTS (SELECT 1 FROM apex.signal_heads h WHERE h.signal_id = signals.id AND h.published_at IS NOT NULL));
GRANT SELECT ON apex.latest_quotes TO apex_member;
CREATE POLICY latest_quotes_read ON apex.latest_quotes FOR SELECT TO apex_member USING (true);

-- ---------------------------------------------------------------------------------------------
-- retention (Supabase Free: 500 MB hard limit; warn 350 MB, stop non-essential writes 450 MB)
-- ---------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION apex.apply_retention(quote_days integer DEFAULT 14, heartbeat_days integer DEFAULT 7,
                                                attempt_days integer DEFAULT 30, feature_days integer DEFAULT 365)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  q bigint; n bigint; h bigint; d bigint; f bigint;
BEGIN
  DELETE FROM apex.quote_events WHERE ts_received < now() - make_interval(days => quote_days);
  GET DIAGNOSTICS q = ROW_COUNT;
  DELETE FROM apex.agent_nonces WHERE expires_at < now();
  GET DIAGNOSTICS n = ROW_COUNT;
  DELETE FROM apex.agent_heartbeats WHERE received_at < now() - make_interval(days => heartbeat_days);
  GET DIAGNOSTICS h = ROW_COUNT;
  DELETE FROM apex.delivery_attempts WHERE occurred_at < now() - make_interval(days => attempt_days);
  GET DIAGNOSTICS d = ROW_COUNT;
  DELETE FROM apex.feature_snapshots WHERE asof_at < now() - make_interval(days => feature_days);
  GET DIAGNOSTICS f = ROW_COUNT;
  RETURN jsonb_build_object('quote_events', q, 'agent_nonces', n, 'agent_heartbeats', h, 'delivery_attempts', d, 'feature_snapshots', f);
END $$;

CREATE OR REPLACE FUNCTION apex.database_size_bytes() RETURNS bigint
LANGUAGE sql STABLE AS $$ SELECT pg_database_size(current_database()) $$;

COMMENT ON SCHEMA apex IS 'APEX canonical store. Not exposed through the Supabase Data API. Server-side access only.';
