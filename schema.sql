CREATE DATABASE IF NOT EXISTS osas_bcp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE osas_bcp;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  email VARCHAR(120) NOT NULL UNIQUE,
  full_name VARCHAR(150) NOT NULL,
  role ENUM('admin','security_head','guard','safety_officer','faculty_staff') NOT NULL DEFAULT 'faculty_staff',
  campus_id VARCHAR(30) NOT NULL UNIQUE,
  account_status ENUM('active','disabled') NOT NULL DEFAULT 'active',
  must_change_password TINYINT(1) NOT NULL DEFAULT 0,
  failed_login_attempts INT NOT NULL DEFAULT 0,
  locked_until DATETIME NULL,
  profile_image VARCHAR(500) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS buildings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  building_code VARCHAR(30) NOT NULL UNIQUE,
  building_name VARCHAR(150) NOT NULL,
  floors INT NOT NULL DEFAULT 1,
  latitude DECIMAL(10,7), longitude DECIMAL(10,7),
  active TINYINT(1) NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS campus_zones (
  id INT AUTO_INCREMENT PRIMARY KEY,
  zone_code VARCHAR(30) NOT NULL UNIQUE,
  zone_name VARCHAR(150) NOT NULL,
  building_id INT NULL,
  zone_type ENUM('gate','building','open_space','parking','assembly_area','other') NOT NULL DEFAULT 'other',
  risk_score DECIMAL(5,2) NOT NULL DEFAULT 0,
  active TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (building_id) REFERENCES buildings(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
  id INT AUTO_INCREMENT PRIMARY KEY,
  checkpoint_code VARCHAR(50) NOT NULL UNIQUE,
  checkpoint_name VARCHAR(150) NOT NULL,
  zone_id INT NOT NULL,
  scan_token VARCHAR(120) NOT NULL UNIQUE,
  latitude DECIMAL(10,7), longitude DECIMAL(10,7),
  active TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (zone_id) REFERENCES campus_zones(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS emergency_contacts (
  id INT AUTO_INCREMENT PRIMARY KEY,
  contact_name VARCHAR(150) NOT NULL,
  organization VARCHAR(150), role_title VARCHAR(100), phone VARCHAR(40) NOT NULL,
  email VARCHAR(120),
  contact_type ENUM('internal','police','fire','medical','barangay','parent') NOT NULL DEFAULT 'internal',
  active TINYINT(1) NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS drills (
  id INT AUTO_INCREMENT PRIMARY KEY,
  drill_type ENUM('fire','earthquake','lockdown','medical','other') NOT NULL,
  title VARCHAR(180) NOT NULL, scheduled_at DATETIME NOT NULL,
  lead_officer_id INT NULL,
  status ENUM('planned','in_progress','completed','cancelled') NOT NULL DEFAULT 'planned',
  participant_count INT NOT NULL DEFAULT 0, notes TEXT,
  FOREIGN KEY (lead_officer_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS evacuation_plans (
  id INT AUTO_INCREMENT PRIMARY KEY,
  building_id INT NOT NULL, floor_label VARCHAR(40) NOT NULL,
  plan_name VARCHAR(180) NOT NULL, map_file_path VARCHAR(500),
  primary_assembly_zone_id INT NULL, version_no INT NOT NULL DEFAULT 1,
  active TINYINT(1) NOT NULL DEFAULT 1, created_by INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (building_id) REFERENCES buildings(id) ON DELETE CASCADE,
  FOREIGN KEY (primary_assembly_zone_id) REFERENCES campus_zones(id) ON DELETE SET NULL,
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS incidents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  incident_code VARCHAR(30) NOT NULL UNIQUE, zone_id INT NULL, reported_by INT NULL,
  incident_type ENUM('security','safety_hazard','medical','fire','earthquake','lockdown','other') NOT NULL DEFAULT 'other',
  title VARCHAR(180) NOT NULL, description TEXT,
  priority ENUM('low','medium','high','critical') NOT NULL DEFAULT 'medium',
  status ENUM('reported','verified','responding','resolved','closed') NOT NULL DEFAULT 'reported',
  reported_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, verified_at DATETIME NULL,
  resolved_at DATETIME NULL, assigned_to INT NULL,
  FOREIGN KEY (zone_id) REFERENCES campus_zones(id) ON DELETE SET NULL,
  FOREIGN KEY (reported_by) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_incidents_zone_time (zone_id, reported_at), INDEX idx_incidents_status (status)
);

CREATE TABLE IF NOT EXISTS incident_attachments (
  id INT AUTO_INCREMENT PRIMARY KEY, incident_id INT NOT NULL,
  file_path VARCHAR(500) NOT NULL, file_name VARCHAR(255) NOT NULL,
  uploaded_by INT NULL, uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE CASCADE,
  FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS safety_inspections (
  id INT AUTO_INCREMENT PRIMARY KEY, inspection_code VARCHAR(30) NOT NULL UNIQUE,
  zone_id INT NOT NULL, inspector_id INT NULL, scheduled_date DATE NOT NULL,
  completed_at DATETIME NULL, overall_score DECIMAL(5,2),
  status ENUM('scheduled','in_progress','completed','overdue') NOT NULL DEFAULT 'scheduled', notes TEXT,
  FOREIGN KEY (zone_id) REFERENCES campus_zones(id) ON DELETE CASCADE,
  FOREIGN KEY (inspector_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS inspection_items (
  id INT AUTO_INCREMENT PRIMARY KEY, inspection_id INT NOT NULL,
  item_name VARCHAR(180) NOT NULL, category VARCHAR(100),
  result ENUM('pass','fail','na','pending') NOT NULL DEFAULT 'pending',
  finding TEXT, corrective_action TEXT,
  FOREIGN KEY (inspection_id) REFERENCES safety_inspections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS risk_assessments (
  id INT AUTO_INCREMENT PRIMARY KEY, zone_id INT NOT NULL, assessed_by INT NULL,
  hazard_title VARCHAR(180) NOT NULL, hazard_description TEXT,
  likelihood TINYINT NOT NULL, severity TINYINT NOT NULL, risk_score DECIMAL(6,2) NOT NULL,
  mitigation_plan TEXT, status ENUM('open','mitigating','accepted','closed') NOT NULL DEFAULT 'open',
  reviewed_at DATETIME NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (zone_id) REFERENCES campus_zones(id) ON DELETE CASCADE,
  FOREIGN KEY (assessed_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS emergency_roles (
  id INT AUTO_INCREMENT PRIMARY KEY, role_name VARCHAR(120) NOT NULL,
  responsibility TEXT NOT NULL, assigned_user_id INT NULL, drill_id INT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (assigned_user_id) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (drill_id) REFERENCES drills(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS first_aid_inventory (
  id INT AUTO_INCREMENT PRIMARY KEY, item_name VARCHAR(150) NOT NULL,
  cabinet_location VARCHAR(150) NOT NULL, quantity INT NOT NULL DEFAULT 0,
  reorder_level INT NOT NULL DEFAULT 1, expiry_date DATE NULL, last_checked_at DATETIME NULL,
  status ENUM('available','low_stock','expired','disposed') NOT NULL DEFAULT 'available',
  updated_by INT NULL, FOREIGN KEY (updated_by) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS patrol_teams (
  id INT AUTO_INCREMENT PRIMARY KEY, team_name VARCHAR(100) NOT NULL UNIQUE,
  team_lead_id INT NULL, active TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (team_lead_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS patrol_team_members (
  team_id INT NOT NULL, user_id INT NOT NULL, PRIMARY KEY (team_id, user_id),
  FOREIGN KEY (team_id) REFERENCES patrol_teams(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS patrol_shifts (
  id INT AUTO_INCREMENT PRIMARY KEY, team_id INT NOT NULL, shift_date DATE NOT NULL,
  start_time TIME NOT NULL, end_time TIME NOT NULL, guard_count INT NOT NULL DEFAULT 0,
  status ENUM('scheduled','active','completed','missed') NOT NULL DEFAULT 'scheduled',
  FOREIGN KEY (team_id) REFERENCES patrol_teams(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS patrol_routes (
  id INT AUTO_INCREMENT PRIMARY KEY, route_name VARCHAR(150) NOT NULL,
  generated_by INT NULL, team_id INT NULL, shift_id INT NULL,
  routing_engine VARCHAR(50) NOT NULL DEFAULT 'OSRM', route_json JSON,
  distance_meters INT NOT NULL DEFAULT 0, duration_seconds INT NOT NULL DEFAULT 0,
  risk_weight DECIMAL(6,2) NOT NULL DEFAULT 0,
  status ENUM('draft','assigned','in_progress','completed','cancelled') NOT NULL DEFAULT 'draft',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (generated_by) REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (team_id) REFERENCES patrol_teams(id) ON DELETE SET NULL,
  FOREIGN KEY (shift_id) REFERENCES patrol_shifts(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS patrol_route_checkpoints (
  route_id INT NOT NULL, checkpoint_id INT NOT NULL, sequence_no INT NOT NULL,
  priority_score DECIMAL(6,2) NOT NULL DEFAULT 0, planned_arrival TIME NULL,
  PRIMARY KEY (route_id, checkpoint_id),
  FOREIGN KEY (route_id) REFERENCES patrol_routes(id) ON DELETE CASCADE,
  FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS patrol_logs (
  id INT AUTO_INCREMENT PRIMARY KEY, route_id INT NULL, checkpoint_id INT NOT NULL,
  team_id INT NULL, guard_id INT NULL, scanned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  scan_method ENUM('qr','nfc','gps','manual') NOT NULL DEFAULT 'manual',
  result ENUM('valid','late','missed','invalid') NOT NULL DEFAULT 'valid', notes TEXT,
  FOREIGN KEY (route_id) REFERENCES patrol_routes(id) ON DELETE SET NULL,
  FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id) ON DELETE CASCADE,
  FOREIGN KEY (team_id) REFERENCES patrol_teams(id) ON DELETE SET NULL,
  FOREIGN KEY (guard_id) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_patrol_logs_checkpoint_time (checkpoint_id, scanned_at)
);

CREATE TABLE IF NOT EXISTS parent_notifications (
  id INT AUTO_INCREMENT PRIMARY KEY, incident_id INT NULL, recipient_name VARCHAR(150) NOT NULL,
  recipient_phone VARCHAR(40), recipient_email VARCHAR(120), channel ENUM('sms','email','push') NOT NULL,
  message TEXT NOT NULL, status ENUM('queued','sent','failed','cancelled') NOT NULL DEFAULT 'queued',
  sent_at DATETIME NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, title VARCHAR(150) NOT NULL,
  message TEXT NOT NULL, ntype ENUM('info','success','warning','danger') NOT NULL DEFAULT 'info',
  link VARCHAR(255), is_read TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id INT NULL, action VARCHAR(120) NOT NULL,
  entity_type VARCHAR(80), entity_id INT, old_values JSON, new_values JSON,
  ip_address VARCHAR(45), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS analytics_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, event_type VARCHAR(80) NOT NULL,
  user_id INT NULL, entity_type VARCHAR(50), entity_id INT, metadata JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS visitors (
  id INT AUTO_INCREMENT PRIMARY KEY, pass_code VARCHAR(80) NOT NULL UNIQUE,
  full_name VARCHAR(150) NOT NULL, phone VARCHAR(40), email VARCHAR(120),
  purpose VARCHAR(180) NOT NULL, host_name VARCHAR(150) NOT NULL,
  visit_date DATE NOT NULL, valid_until DATETIME NOT NULL,
  status ENUM('registered','checked_in','exited','expired','revoked') NOT NULL DEFAULT 'registered',
  entry_at DATETIME NULL, exit_at DATETIME NULL, registered_by INT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (registered_by) REFERENCES users(id) ON DELETE SET NULL,
  INDEX idx_visitors_visit_date (visit_date), INDEX idx_visitors_status (status)
);
