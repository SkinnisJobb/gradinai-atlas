"""TASK-973's module identity. The tables live in models_auth.py because both
stories' DDL had to reach the populated Postgres in ONE pass (TASK-966 §"The
schema moves ONCE"). Re-export only — no schema, no second metadata."""
from models_auth import VisibilityBase as Base, NodeVisibility, VISIBILITY_VALUES  # noqa: F401
