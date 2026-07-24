import hashlib
import json
from pathlib import Path


def test_canonical_history_capture_hash_fixtures():
    path = Path(__file__).parents[2] / "shared" / "history-capture-v1-fixtures.json"
    fixtures = json.loads(path.read_text())

    for case in fixtures["cases"]:
        digest = hashlib.sha256(
            fixtures["hash_prefix"].encode() + case["canonical_json"].encode()
        ).hexdigest()
        assert digest == case["sha256"], case["name"]
