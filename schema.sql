-- OSAS PostgreSQL schema (Render / local Postgres)
-- Database is created by the provider or init_db.py; this file only creates tables.

CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  full_name VARCHAR(150) NOT NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'faculty_staff',
  campus_id VARCHAR(30) NOT NULL UNIQUE,
  account_status VARCHAR(20) NOT NULL DEFAULT 'active',
  must_change_password SMALLINT NOT NULL DEFAULT 0,
  failed_login_attempts INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMP NULL,
  profile_image VARCHAR(500) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS login_otp_challenges (
  id BIGSERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash CHAR(64) NOT NULL,
  purpose VARCHAR(40) NOT NULL DEFAULT 'login',
  expires_at TIMESTAMP NOT NULL,
  attempts INT NOT NULL DEFAULT 0,
  consumed_at TIMESTAMP NULL,
  requested_ip VARCHAR(45),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_login_otp_user_active
  ON login_otp_challenges (user_id, purpose, consumed_at, expires_at);

CREATE TABLE IF NOT EXISTS buildings (
  id SERIAL PRIMARY KEY,
  building_code VARCHAR(30) NOT NULL UNIQUE,
  building_name VARCHAR(150) NOT NULL,
  floors INT NOT NULL DEFAULT 1,
  latitude DECIMAL(10,7), longitude DECIMAL(10,7),
  active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS campus_zones (
  id SERIAL PRIMARY KEY,
  zone_code VARCHAR(30) NOT NULL UNIQUE,
  zone_name VARCHAR(150) NOT NULL,
  building_id INT NULL REFERENCES buildings(id) ON DELETE SET NULL,
  zone_type VARCHAR(40) NOT NULL DEFAULT 'other',
  risk_score DECIMAL(5,2) NOT NULL DEFAULT 0,
  active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS checkpoints (
  id SERIAL PRIMARY KEY,
  checkpoint_code VARCHAR(50) NOT NULL UNIQUE,
  checkpoint_name VARCHAR(150) NOT NULL,
  zone_id INT NOT NULL REFERENCES campus_zones(id) ON DELETE CASCADE,
  scan_token VARCHAR(120) NOT NULL UNIQUE,
  latitude DECIMAL(10,7), longitude DECIMAL(10,7),
  active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS emergency_contacts (
  id SERIAL PRIMARY KEY,
  contact_name VARCHAR(150) NOT NULL,
  organization VARCHAR(150), role_title VARCHAR(100), phone VARCHAR(40) NOT NULL,
  email VARCHAR(120),
  contact_type VARCHAR(40) NOT NULL DEFAULT 'internal',
  active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS drills (
  id SERIAL PRIMARY KEY,
  drill_type VARCHAR(40) NOT NULL,
  title VARCHAR(180) NOT NULL, scheduled_at TIMESTAMP NOT NULL,
  lead_officer_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  team_id INT NULL,
  checkpoint_id INT NULL REFERENCES checkpoints(id) ON DELETE SET NULL,
  inspection_id INT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'planned',
  participant_count INT NOT NULL DEFAULT 0, notes TEXT
);

CREATE TABLE IF NOT EXISTS evacuation_plans (
  id SERIAL PRIMARY KEY,
  building_id INT NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
  floor_label VARCHAR(40) NOT NULL,
  plan_name VARCHAR(180) NOT NULL, map_file_path VARCHAR(500),
  primary_assembly_zone_id INT NULL REFERENCES campus_zones(id) ON DELETE SET NULL,
  version_no INT NOT NULL DEFAULT 1,
  active SMALLINT NOT NULL DEFAULT 1, created_by INT NULL REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patrol_teams (
  id SERIAL PRIMARY KEY, team_name VARCHAR(100) NOT NULL UNIQUE,
  team_lead_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  lead_name VARCHAR(150) NULL, active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS patrol_team_members (
  team_id INT NOT NULL REFERENCES patrol_teams(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (team_id, user_id)
);

CREATE TABLE IF NOT EXISTS incidents (
  id SERIAL PRIMARY KEY,
  incident_code VARCHAR(30) NOT NULL UNIQUE,
  zone_id INT NULL REFERENCES campus_zones(id) ON DELETE SET NULL,
  checkpoint_id INT NULL REFERENCES checkpoints(id) ON DELETE SET NULL,
  team_id INT NULL REFERENCES patrol_teams(id) ON DELETE SET NULL,
  reported_by INT NULL REFERENCES users(id) ON DELETE SET NULL,
  incident_type VARCHAR(40) NOT NULL DEFAULT 'other',
  title VARCHAR(180) NOT NULL, description TEXT,
  priority VARCHAR(20) NOT NULL DEFAULT 'medium',
  status VARCHAR(20) NOT NULL DEFAULT 'reported',
  reported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, verified_at TIMESTAMP NULL,
  resolved_at TIMESTAMP NULL, assigned_to INT NULL REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_zone_time ON incidents (zone_id, reported_at);
CREATE INDEX IF NOT EXISTS idx_incidents_checkpoint ON incidents (checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_incidents_team ON incidents (team_id);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status);

CREATE TABLE IF NOT EXISTS incident_attachments (
  id SERIAL PRIMARY KEY, incident_id INT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
  file_path VARCHAR(500) NOT NULL, file_name VARCHAR(255) NOT NULL,
  uploaded_by INT NULL REFERENCES users(id) ON DELETE SET NULL,
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS safety_inspections (
  id SERIAL PRIMARY KEY, inspection_code VARCHAR(30) NOT NULL UNIQUE,
  zone_id INT NOT NULL REFERENCES campus_zones(id) ON DELETE CASCADE,
  inspector_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  checkpoint_id INT NULL REFERENCES checkpoints(id) ON DELETE SET NULL,
  team_id INT NULL REFERENCES patrol_teams(id) ON DELETE SET NULL,
  scheduled_date DATE NOT NULL,
  completed_at TIMESTAMP NULL, overall_score DECIMAL(5,2),
  status VARCHAR(20) NOT NULL DEFAULT 'scheduled', notes TEXT
);

CREATE TABLE IF NOT EXISTS inspection_items (
  id SERIAL PRIMARY KEY, inspection_id INT NOT NULL REFERENCES safety_inspections(id) ON DELETE CASCADE,
  item_name VARCHAR(180) NOT NULL, category VARCHAR(100),
  result VARCHAR(20) NOT NULL DEFAULT 'pending',
  finding TEXT, corrective_action TEXT
);

CREATE TABLE IF NOT EXISTS risk_assessments (
  id SERIAL PRIMARY KEY, zone_id INT NOT NULL REFERENCES campus_zones(id) ON DELETE CASCADE,
  assessed_by INT NULL REFERENCES users(id) ON DELETE SET NULL,
  hazard_title VARCHAR(180) NOT NULL, hazard_description TEXT,
  likelihood SMALLINT NOT NULL, severity SMALLINT NOT NULL, risk_score DECIMAL(6,2) NOT NULL,
  mitigation_plan TEXT, status VARCHAR(20) NOT NULL DEFAULT 'open',
  reviewed_at TIMESTAMP NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emergency_roles (
  id SERIAL PRIMARY KEY, role_name VARCHAR(120) NOT NULL,
  responsibility TEXT NOT NULL, assigned_user_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  drill_id INT NULL REFERENCES drills(id) ON DELETE SET NULL,
  active SMALLINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS first_aid_inventory (
  id SERIAL PRIMARY KEY, item_name VARCHAR(150) NOT NULL,
  cabinet_location VARCHAR(150) NOT NULL, quantity INT NOT NULL DEFAULT 0,
  reorder_level INT NOT NULL DEFAULT 1, expiry_date DATE NULL, last_checked_at TIMESTAMP NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'available',
  updated_by INT NULL REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS patrol_shifts (
  id SERIAL PRIMARY KEY, team_id INT NOT NULL REFERENCES patrol_teams(id) ON DELETE CASCADE,
  shift_date DATE NOT NULL,
  start_time TIME NOT NULL, end_time TIME NOT NULL, guard_count INT NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'scheduled'
);

CREATE TABLE IF NOT EXISTS patrol_routes (
  id SERIAL PRIMARY KEY, route_name VARCHAR(150) NOT NULL,
  generated_by INT NULL REFERENCES users(id) ON DELETE SET NULL,
  team_id INT NULL REFERENCES patrol_teams(id) ON DELETE SET NULL,
  shift_id INT NULL REFERENCES patrol_shifts(id) ON DELETE SET NULL,
  routing_engine VARCHAR(50) NOT NULL DEFAULT 'OSRM', route_json JSONB,
  distance_meters INT NOT NULL DEFAULT 0, duration_seconds INT NOT NULL DEFAULT 0,
  risk_weight DECIMAL(6,2) NOT NULL DEFAULT 0,
  status VARCHAR(40) NOT NULL DEFAULT 'draft',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS patrol_route_checkpoints (
  route_id INT NOT NULL REFERENCES patrol_routes(id) ON DELETE CASCADE,
  checkpoint_id INT NOT NULL REFERENCES checkpoints(id) ON DELETE CASCADE,
  sequence_no INT NOT NULL,
  priority_score DECIMAL(6,2) NOT NULL DEFAULT 0, planned_arrival TIME NULL,
  PRIMARY KEY (route_id, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS patrol_logs (
  id SERIAL PRIMARY KEY, route_id INT NULL REFERENCES patrol_routes(id) ON DELETE SET NULL,
  checkpoint_id INT NOT NULL REFERENCES checkpoints(id) ON DELETE CASCADE,
  team_id INT NULL REFERENCES patrol_teams(id) ON DELETE SET NULL,
  guard_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  scanned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  scan_method VARCHAR(20) NOT NULL DEFAULT 'manual',
  result VARCHAR(20) NOT NULL DEFAULT 'valid', notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_patrol_logs_checkpoint_time ON patrol_logs (checkpoint_id, scanned_at);

CREATE TABLE IF NOT EXISTS parent_notifications (
  id SERIAL PRIMARY KEY, incident_id INT NULL REFERENCES incidents(id) ON DELETE SET NULL,
  recipient_name VARCHAR(150) NOT NULL,
  recipient_phone VARCHAR(40), recipient_email VARCHAR(120), channel VARCHAR(20) NOT NULL,
  message TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'queued',
  sent_at TIMESTAMP NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(150) NOT NULL,
  message TEXT NOT NULL, ntype VARCHAR(20) NOT NULL DEFAULT 'info',
  link VARCHAR(255), is_read SMALLINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGSERIAL PRIMARY KEY, user_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  action VARCHAR(120) NOT NULL,
  entity_type VARCHAR(80), entity_id INT, old_values JSONB, new_values JSONB,
  ip_address VARCHAR(45), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics_log (
  id BIGSERIAL PRIMARY KEY, event_type VARCHAR(80) NOT NULL,
  user_id INT NULL REFERENCES users(id) ON DELETE SET NULL,
  entity_type VARCHAR(50), entity_id INT, metadata JSONB,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS visitors (
  id SERIAL PRIMARY KEY, pass_code VARCHAR(80) NOT NULL UNIQUE,
  full_name VARCHAR(150) NOT NULL, phone VARCHAR(40), email VARCHAR(120),
  purpose VARCHAR(180) NOT NULL, host_name VARCHAR(150) NOT NULL,
  visit_date DATE NOT NULL, valid_until TIMESTAMP NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'registered',
  entry_at TIMESTAMP NULL, exit_at TIMESTAMP NULL, registered_by INT NULL REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_visitors_visit_date ON visitors (visit_date);
CREATE INDEX IF NOT EXISTS idx_visitors_status ON visitors (status);
