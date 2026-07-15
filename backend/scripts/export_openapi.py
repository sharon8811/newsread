"""Write the app's OpenAPI schema to backend/openapi.json.

The file is checked in and consumed by the frontend's `npm run types:gen`
(openapi-typescript); CI fails if either artifact is stale.
"""

import json
from pathlib import Path

from app.main import app


def main() -> None:
    schema = app.openapi()
    out = Path(__file__).resolve().parent.parent / "openapi.json"
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(schema.get('components', {}).get('schemas', {}))} schemas)")


if __name__ == "__main__":
    main()
