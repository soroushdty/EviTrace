"""Minimal numpy shim for test environment where numpy is not installed.

This provides the small subset used by the tests: `zeros()` and `array()`.
It is intentionally lightweight and only meant to satisfy unit tests; it is
not a replacement for real NumPy in production.
"""
from typing import Sequence, Any


def zeros(shape: Sequence[int], dtype: Any = None):
    # Create a nested list of zeros with the requested shape. For tests this
    # is sufficient; dtype is ignored.
    if not shape:
        return 0
    if len(shape) == 1:
        return [0 for _ in range(shape[0])]
    if len(shape) == 2:
        return [[0 for _ in range(shape[1])] for _ in range(shape[0])]
    # len >= 3
    return [[[0 for _ in range(shape[2])] for _ in range(shape[1])] for _ in range(shape[0])]


def array(x):
    # Identity conversion: tests patch this function when needed; otherwise
    # return the input unchanged.
    return x

# Minimal dtype aliases used in tests
uint8 = int
