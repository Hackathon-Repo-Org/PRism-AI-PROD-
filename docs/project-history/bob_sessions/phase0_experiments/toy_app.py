"""Tiny toy module for Phase 0 Bob capability experiments."""


def add(a: int, b: int) -> int:
    return a + b


def classify(n: int) -> str:
    """Classify a number as negative, zero, or positive."""
    if n < 0:
        return "negative"
    elif n == 0:
        return "zero"
    else:
        return "positive"


def safe_divide(a: float, b: float) -> float:
    """Divide a by b; raises ValueError if b is zero."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
