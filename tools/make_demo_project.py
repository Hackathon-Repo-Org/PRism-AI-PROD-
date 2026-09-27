"""
Generate a large, messy demo project ("ShopMart") for trying PRism-AI on a big folder.

    python tools/make_demo_project.py            -> demo-large-project/  (~700 KB)
    python tools/make_demo_project.py --kb 1500  -> bigger

Ten problems are planted on purpose; the answer key is written NEXT TO the folder
(demo-large-project-ANSWERS.md), so PRism-AI never reads it during a scan.
Both are git-ignored.
Everything else is filler: plausible but repetitive "legacy" code that is valid Python,
so the project's own tests still run. Same output every time (fixed random seed).
"""

from __future__ import annotations

import argparse
import pathlib
import random
import shutil
import textwrap

HERE = pathlib.Path(__file__).resolve().parent.parent   # the PRism-AI folder
OUT = HERE / "demo-large-project"
ANSWERS = HERE / "demo-large-project-ANSWERS.md"

AREAS = ["inventory", "orders", "users", "payments", "reports", "utils", "legacy",
         "notifications", "catalog", "warehouse"]
NOUNS = ["item", "order", "customer", "invoice", "stock", "coupon", "cart", "parcel",
         "voucher", "supplier", "batch", "ledger", "refund", "shipment", "review"]
VERBS = ["load", "save", "compute", "format", "validate", "merge", "sync", "export",
         "normalise", "archive", "calculate", "render", "collect", "update", "check"]

# ---------------------------------------------------------------------------
# The ten planted problems (file, content, answer-key entry)
# ---------------------------------------------------------------------------

PLANTED = {
    "shopmart/orders/discounts.py": ('''"""Discount rules for orders."""


def bulk_discount(quantity: int, unit_price: float) -> float:
    """Return the order total. Orders of 10 or more units get 10% off."""
    total = quantity * unit_price
    if quantity > 10:
        total = total * 0.9
    return round(total, 2)


def coupon_discount(total: float, coupon: str | None) -> float:
    """Apply a coupon code. SAVE5 takes 5 off, never below zero."""
    if coupon == "SAVE5":
        return max(0.0, total - 5)
    return total
''', "LOGIC_ERROR (off-by-one)", "`bulk_discount` uses `quantity > 10` but the docstring promises the discount at **10 or more**: an order of exactly 10 gets no discount."),

    "shopmart/users/auth.py": ('''"""Login for staff accounts."""
import hashlib

ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"  # TODO move somewhere safer


def hash_password(password: str) -> str:
    return hashlib.md5(password.encode()).hexdigest()


def login(username: str, password: str) -> bool:
    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        return True
    return False
''', "SECURITY", "Hard-coded admin password in source, plain `==` comparison, and passwords hashed with **MD5** (unsalted, broken for passwords)."),

    "shopmart/reports/sales.py": ('''"""Sales reports straight from the database."""
import sqlite3


def sales_for_customer(conn: sqlite3.Connection, customer_name: str) -> list:
    """All sales rows for one customer."""
    query = f"SELECT * FROM sales WHERE customer = '{customer_name}'"
    return conn.execute(query).fetchall()


def total_sales(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT SUM(amount) FROM sales").fetchone()
    return row[0] or 0.0
''', "SECURITY (SQL injection)", "`sales_for_customer` builds SQL with an f-string from `customer_name` - SQL injection. Use a parameterised query."),

    "shopmart/payments/refund.py": ('''"""Refunds."""

REFUND_WINDOW_DAYS = 14


def refund_percentage(refund_amount: float, order_total: float) -> float:
    """How much of the order is refunded, as a percentage."""
    return round(refund_amount / order_total * 100, 1)


def can_refund(days_since_purchase: int) -> bool:
    return days_since_purchase <= REFUND_WINDOW_DAYS
''', "ERROR_HANDLING / INPUT_VALIDATION", "`refund_percentage` divides by `order_total` with no check: a free (0.00) order raises ZeroDivisionError."),

    "shopmart/utils/cache.py": ('''"""A tiny in-memory cache."""


def remember(key: str, value, store: dict = {}) -> dict:
    """Store value under key and return the store."""
    store[key] = value
    return store


def forget(key: str, store: dict) -> None:
    store.pop(key, None)
''', "LOGIC_ERROR (mutable default)", "`remember` uses a mutable default argument `store={}`: every caller that omits `store` shares one dictionary, leaking data between calls."),

    "shopmart/inventory/stock.py": ('''"""Stock levels."""
import json

STOCK_FILE = "stock.json"


def load_stock(path: str = STOCK_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass


def units_left(sku: str, path: str = STOCK_FILE) -> int:
    return load_stock(path)[sku]
''', "ERROR_HANDLING", "`load_stock` swallows every error with `except Exception: pass` and returns None; `units_left` then crashes with TypeError instead of a clear error."),

    "shopmart/orders/shipping.py": ('''"""Shipping labels."""
from typing import Optional


def shipping_label(name: str, address: Optional[str] = None) -> str:
    """Printable label. Address is optional for in-store pickup."""
    return f"{name}\\n{address.upper()}"
''', "NULL_HANDLING", "`shipping_label` documents `address` as optional but calls `address.upper()` - AttributeError for in-store pickup (address=None)."),

    "shopmart/users/profile.py": ('''"""Profile pictures."""
import os

UPLOAD_DIR = "uploads"


def read_avatar(filename: str) -> bytes:
    """Return the bytes of a user's uploaded avatar."""
    with open(os.path.join(UPLOAD_DIR, filename), "rb") as f:
        return f.read()
''', "SECURITY (path traversal)", "`read_avatar` joins a user-supplied `filename` onto UPLOAD_DIR without checks: `../../secrets.txt` reads any file."),

    "docs/api.md": ('''# ShopMart API

## Refunds

Customers can request a refund within **30 days** of purchase.
`can_refund(days_since_purchase)` returns True inside that window.

## Discounts

`bulk_discount(quantity, unit_price)` - orders of 10 or more units get 10% off.
`coupon_discount(total, coupon)` - `SAVE5` takes 5 off.
''', "DOC_MISMATCH", "`docs/api.md` says the refund window is **30 days**; the code (`REFUND_WINDOW_DAYS`) uses **14**."),

    "tests/test_payments.py": ('''from shopmart.payments.refund import can_refund, refund_percentage


def test_refund_percentage_runs():
    refund_percentage(5, 20)


def test_can_refund():
    assert can_refund(1)
''', "WEAK_TEST", "`test_refund_percentage_runs` calls the function but asserts nothing, so it can never fail."),
}

REAL_TESTS = {
    "tests/test_orders.py": '''from shopmart.orders.discounts import bulk_discount, coupon_discount


def test_small_order_has_no_discount():
    assert bulk_discount(2, 10.0) == 20.0


def test_big_order_discount():
    assert bulk_discount(20, 10.0) == 180.0


def test_coupon():
    assert coupon_discount(12.0, "SAVE5") == 7.0
''',
    "tests/test_auth.py": '''from shopmart.users.auth import login


def test_admin_can_log_in():
    assert login("admin", "admin123")


def test_wrong_password_rejected():
    assert not login("admin", "nope")
''',
}


# ---------------------------------------------------------------------------
# Filler ("rubbish" legacy code)
# ---------------------------------------------------------------------------

def filler_function(rng: random.Random, n: int) -> str:
    verb, noun = rng.choice(VERBS), rng.choice(NOUNS)
    name = f"{verb}_{noun}_{n}"
    lines = [f"def {name}(records, limit=100, verbose=False):",
             f'    """{verb.capitalize()} {noun} records (legacy helper #{n})."""',
             "    result = []",
             "    count = 0"]
    for i in range(rng.randint(6, 16)):
        field = rng.choice(["id", "name", "qty", "price", "status", "created", "owner"])
        kind = rng.randint(0, 4)
        if kind == 0:
            lines += [f"    for r in records:",
                      f"        if r.get('{field}') is not None and count < limit:",
                      f"            result.append(r['{field}'])",
                      f"            count += 1"]
        elif kind == 1:
            lines += [f"    # TODO({rng.choice(['bob', 'alice', 'tmp', 'fixme'])}): clean this up",
                      f"    tmp_{i} = [x for x in result if x]",
                      f"    if verbose:",
                      f"        print('{name}', len(tmp_{i}))"]
        elif kind == 2:
            lines += [f"    # old version:",
                      f"    # for r in records: result.append(str(r.get('{field}')))",
                      f"    total_{i} = sum(1 for _ in records)"]
        elif kind == 3:
            lines += [f"    if len(result) > limit:",
                      f"        result = result[:limit]"]
        else:
            lines += [f"    mapping_{i} = {{k: v for k, v in enumerate(result)}}",
                      f"    if len(mapping_{i}) == 0:",
                      f"        pass"]
    lines.append("    return result")
    return "\n".join(lines)


def filler_module(rng: random.Random, area: str, index: int, target_bytes: int) -> str:
    parts = [f'"""{area.capitalize()} helpers, part {index} (legacy - do not touch)."""',
             "import os", "import json", "", "",
             f"CONFIG_{index} = {{'retries': {rng.randint(1, 5)}, 'timeout': {rng.randint(5, 60)}}}",
             "", ""]
    size, n = sum(len(p) for p in parts), 0
    while size < target_bytes:
        fn = filler_function(rng, index * 1000 + n)
        parts += [fn, "", ""]
        size += len(fn) + 2
        n += 1
    return "\n".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------

def build(target_kb: int) -> None:
    rng = random.Random(42)
    if OUT.exists():
        shutil.rmtree(OUT)
    written = 0

    def write(rel: str, text: str) -> None:
        nonlocal written
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written += len(text.encode("utf-8"))

    write("README.md", textwrap.dedent("""\
        # ShopMart (legacy)

        Internal shop back-office: inventory, orders, users, payments and reports.
        See `docs/api.md` for the public functions. Run the tests with `python -m pytest -q`.
        """))
    write("shopmart/__init__.py", "")
    for area in AREAS:
        write(f"shopmart/{area}/__init__.py", "")
    write("tests/__init__.py", "")
    for rel, (text, _, _) in PLANTED.items():
        write(rel, text)
    for rel, text in REAL_TESTS.items():
        write(rel, text)

    index = 0
    while written < target_kb * 1024:
        area = AREAS[index % len(AREAS)]
        write(f"shopmart/{area}/{area}_helpers_{index:03d}.py",
              filler_module(rng, area, index, rng.randint(5000, 9000)))
        index += 1

    rows = "\n".join(f"| {i} | `{rel}` | {cat} | {why} |"
                     for i, (rel, (_, cat, why)) in enumerate(PLANTED.items(), 1))
    ANSWERS.write_text(textwrap.dedent(f"""\
        # Answer key - demo-large-project

        Generated by `tools/make_demo_project.py`. **Keep this file outside the scanned folder.**

        Size: {written // 1024} KB, {sum(1 for _ in OUT.rglob('*.py'))} Python files.
        Everything not listed below is filler ("legacy" helpers): repetitive, messy, but valid.

        | # | File | Category | Planted problem |
        |---|---|---|---|
        """) + rows + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({written // 1024} KB, {index} filler modules)\nAnswer key: {ANSWERS}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--kb", type=int, default=700, help="approximate size (default 700)")
    build(ap.parse_args().kb)
