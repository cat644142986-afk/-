-- Frozen from baseline commit 9b51da4e95339896726f1895c21ca0b816a5f68e.
-- Do not derive this fixture from the current migration implementation.
CREATE TABLE ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    title TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    designer_profile TEXT NOT NULL DEFAULT 'default',
    brand_profile TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    brief_json TEXT NOT NULL DEFAULT '{}',
    intent_locks_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    parent_asset_id TEXT,
    role TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'image',
    path TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    width INTEGER,
    height INTEGER,
    sha256 TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE TABLE generations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    parent_generation_id TEXT,
    model TEXT NOT NULL DEFAULT '',
    prompt TEXT NOT NULL DEFAULT '',
    negative_prompt TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    knowledge_refs_json TEXT NOT NULL DEFAULT '[]',
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    status TEXT NOT NULL DEFAULT 'queued',
    result_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER,
    estimated_cost REAL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_generation_id) REFERENCES generations(id) ON DELETE SET NULL
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    generation_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL
);

CREATE TABLE feedback (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    generation_id TEXT,
    asset_id TEXT,
    signal TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    structured_json TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT 'session',
    created_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY(generation_id) REFERENCES generations(id) ON DELETE SET NULL,
    FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE TABLE memory_suggestions (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'general',
    rule_key TEXT NOT NULL,
    current_value_json TEXT NOT NULL DEFAULT 'null',
    proposed_value_json TEXT NOT NULL DEFAULT 'null',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE INDEX idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX idx_assets_session ON assets(session_id, created_at);
CREATE INDEX idx_generations_session ON generations(session_id, created_at);
CREATE INDEX idx_generations_task ON generations(task_id);
CREATE INDEX idx_events_session ON events(session_id, created_at);
CREATE INDEX idx_feedback_session ON feedback(session_id, created_at);
CREATE INDEX idx_memory_pending ON memory_suggestions(status, created_at);

INSERT INTO ledger_meta(key, value) VALUES('schema_version', '1');
