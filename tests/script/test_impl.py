import pytest

from sonolus.script.internal.impl import validate_value


def test_validate_value_unhashable_gives_unsupported_error():
    # An unhashable, unsupported value (e.g. a list) must produce the intended
    # "Unsupported value" TypeError, not "unhashable type: 'list'" from a set-membership test.
    with pytest.raises(TypeError, match="Unsupported value"):
        validate_value([1, 2, 3])
