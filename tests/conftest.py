"""Small explicit schemas keep tests independent of changing claim examples."""
from datetime import date

import pytest

from data_transformer import build_schema_config
from data_transformer.models import Context


@pytest.fixture
def context_factory():
    def create_context(**overrides):
        configuration = {
            "schema_name": "sample",
            "languages": ["en", "fr"],
            "document_fields": {"label": {"validator": "text"}},
            **overrides,
        }
        return Context(build_schema_config(configuration), date(2026, 9, 28))
    return create_context
