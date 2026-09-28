"""Extension points. Built-in validators register themselves on import (validators.py); users can add
their own with @register_validator / @register_rule BEFORE loading configs that refer to them."""
from __future__ import annotations

from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple


class ValidationOutcome(NamedTuple):
    """What a validator returns: the normalised value, an error message (or None), warnings."""
    value: Any
    error: Optional[str] = None
    warnings: Tuple[str, ...] = ()


def ok(value: Any, *warnings: str) -> ValidationOutcome:
    """Successful outcome with optional warnings."""
    return ValidationOutcome(value, None, tuple(warnings))


def fail(message: str) -> ValidationOutcome:
    """Failed outcome (value is None)."""
    return ValidationOutcome(None, message)


# validator signature: (raw_value, FieldSpec, Context) -> ValidationOutcome
FIELD_VALIDATORS: Dict[str, Callable[..., ValidationOutcome]] = {}
# custom rule signature: (document_values, lines, Context, TransformResult, rule_config) -> None
POST_RULES: Dict[str, Callable[..., None]] = {}


def register_validator(name: str):
    """Decorator: make a validator usable as  "validator": "<name>"  in config files."""
    def decorator(function):
        FIELD_VALIDATORS[name] = function
        return function
    return decorator


def register_rule(name: str):
    """Decorator: make a rule usable as  {"kind": "custom", "name": "<name>"}  in a config's "rules"."""
    def decorator(function):
        POST_RULES[name] = function
        return function
    return decorator
