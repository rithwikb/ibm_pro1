"""
validate_task.py — JSON Schema gate for AgentTaskObject.

Validates an assembled task object against task_schema.json (JSON Schema draft-07).
Called after every output path in the Coding Agent runner before passing downstream.

Raises jsonschema.ValidationError on any schema violation.
Caller must treat a raised error as a hard process error (bug in runner assembly),
not as a task-level failure — no history entry is appended for schema failures.
"""

import json
import os
import jsonschema

# Resolve schema path relative to repo root (schemas/task_schema.json).
_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "schemas", "task_schema.json"
)

with open(_SCHEMA_PATH, "r", encoding="utf-8") as _f:
    _SCHEMA = json.load(_f)

_VALIDATOR_CLASS = jsonschema.Draft7Validator
_VALIDATOR_CLASS.check_schema(_SCHEMA)
_VALIDATOR = _VALIDATOR_CLASS(_SCHEMA)


def validate_task(task_object: dict) -> None:
    """
    Validate *task_object* against task_schema.json (draft-07).

    Raises jsonschema.ValidationError if the object fails validation.
    Returns None on success.
    """
    errors = list(_VALIDATOR.iter_errors(task_object))
    if errors:
        # Surface the first (most relevant) error; the caller logs it.
        raise errors[0]
