"""JSON helpers: non-finite floats become null so API responses stay valid JSON."""
from __future__ import annotations

import json
import math
from typing import Any


def clean(o: Any) -> Any:
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            return clean(o.item())
        except Exception:
            return str(o)
    return o


def dumps(o: Any) -> str:
    return json.dumps(clean(json.loads(json.dumps(o, default=str))), allow_nan=False)
