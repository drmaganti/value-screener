"""Strict JSON persistence without mutating calculation inputs."""
import json
import math
import os
from pathlib import Path
import tempfile


def sanitize_non_finite(value):
    """Copy JSON containers, replacing NaN and either infinity with null.

    Preserve finite numbers (including zero), booleans, strings and missing
    values. Tuples follow JSON's normal array representation. Sanitizing only
    at persistence keeps historical snapshots and outcome calculations intact.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_non_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_non_finite(item) for item in value]
    return value


def save_json_strict(path, payload):
    """Serialize fully before atomically replacing a log in the same directory."""
    text = json.dumps(
        sanitize_non_finite(payload), indent=2, default=str, allow_nan=False
    )
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            temporary = fh.name
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)
