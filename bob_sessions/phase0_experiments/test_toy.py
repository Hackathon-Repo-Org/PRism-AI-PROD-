"""Tests for toy_app.py — Phase 0 experiments."""
import pytest
from toy_app import add, classify, safe_divide


def test_add_two_positives():
    assert add(2, 3) == 5


def test_classify_negative():
    # covers branch: n < 0
    assert classify(-5) == "negative"

# NOTE: classify(0) -> "zero" is NOT tested
# NOTE: classify(positive) -> "positive" is NOT tested
# NOTE: safe_divide happy path is NOT tested
# NOTE: safe_divide(a, 0) -> ValueError is NOT tested
