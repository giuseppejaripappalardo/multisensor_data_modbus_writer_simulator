"""
Utility function for clamping values within a range.
"""
from typing import Union

Number = Union[int, float]


def clamp(value: Number, min_val: Number, max_val: Number) -> Number:
    """
    Clamp a value between min_val and max_val.

    Args:
        value: The value to clamp.
        min_val: The minimum allowed value.
        max_val: The maximum allowed value.

    Returns:
        The clamped value.
    """
    return max(min_val, min(value, max_val))

