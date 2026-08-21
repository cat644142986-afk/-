import json
import os
import sys
import tempfile
from pathlib import Path


temp_dir = tempfile.TemporaryDirectory()
os.environ["APPDATA"] = temp_dir.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from python import server


client = TestClient(server.app)
session_a = client.post("/api/sessions", json={"mode": "single", "category": "食品饮料"}).json()
session_b = client.post("/api/sessions", json={"mode": "single", "category": "食品饮料"}).json()
first = client.post(
    f"/api/sessions/{session_a['id']}/feedback",
    json={"signal": "rejected", "reason": "阴影太重"},
).json()
second = client.post(
    f"/api/sessions/{session_b['id']}/feedback",
    json={"signal": "rejected", "reason": "这版阴影太重了"},
).json()
pending = client.get("/api/memory/suggestions").json()

assert first["synthesis"]["pending_suggestions"] == 0
assert second["synthesis"]["pending_suggestions"] == 1
assert len(pending) == 1
assert pending[0]["proposed_value"]["value"] == "lighter"

reviewed = client.post(
    f"/api/memory/suggestions/{pending[0]['id']}/review",
    json={"status": "approved"},
).json()
resynthesized = client.post("/api/memory/synthesize").json()

print(json.dumps({
    "after_first_session": first["synthesis"]["pending_suggestions"],
    "after_second_session": second["synthesis"]["pending_suggestions"],
    "label": pending[0]["proposed_value"]["label"],
    "confidence": pending[0]["confidence"],
    "evidence": len(pending[0]["evidence"]),
    "approved": reviewed["status"],
    "pending_after_approval": resynthesized["pending_suggestions"],
}, ensure_ascii=False))

temp_dir.cleanup()
