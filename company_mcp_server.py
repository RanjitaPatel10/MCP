"""ShopKart MCP Server — exposes our internal systems to any AI agent."""
import sqlite3, sys, random
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP

DB_PATH = Path(__file__).parent / "shopkart.db"
REFUND_AUTO_LIMIT = 5000          # refunds above this need a human

mcp = FastMCP("ShopKart")


# ───────────────────── database (replace with your real DB) ─────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def seed():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS customers(id TEXT PRIMARY KEY, name TEXT, tier TEXT, city TEXT, total_orders INT, complaints INT);
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, customer_id TEXT, product_id TEXT, product TEXT, amount INT, status TEXT, delay_mins INT);
        CREATE TABLE IF NOT EXISTS inventory(product_id TEXT PRIMARY KEY, name TEXT, stock INT, warehouse TEXT);
        CREATE TABLE IF NOT EXISTS refunds(id TEXT PRIMARY KEY, order_id TEXT, amount INT, reason TEXT, status TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, customer_id TEXT, issue TEXT, priority TEXT, created TEXT);
        """)
        if c.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            c.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", [
                ("CUST-001", "Priya Sharma",    "Premium",  "Bangalore", 47, 0),
                ("CUST-002", "Rahul Verma",     "Standard", "Mumbai",    12, 2),
                ("CUST-003", "Ananya Krishnan", "Gold",     "Chennai",   89, 1)])
            c.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", [
                ("ORD-001", "CUST-001", "PROD-001", "iPhone 15",       79999, "Delivered late", 45),
                ("ORD-002", "CUST-002", "PROD-002", "MacBook Air",    124999, "Delivered",       0),
                ("ORD-003", "CUST-003", "PROD-003", "AirPods",         19999, "Processing",      0),
                ("ORD-004", "CUST-001", "PROD-004", "Nike Air Max",     4999, "Delivered late", 30)])
            c.executemany("INSERT INTO inventory VALUES (?,?,?,?)", [
                ("PROD-001", "iPhone 15",    8,   "Mumbai"),
                ("PROD-002", "MacBook Air",  3,   "Delhi"),
                ("PROD-003", "AirPods",      124, "Bangalore"),
                ("PROD-004", "Nike Air Max", 12,  "Bangalore")])

seed()


# ───────────────────────────────── TOOLS ─────────────────────────────────
@mcp.tool()
def get_customer(customer_id: str) -> dict:
    """Get a customer's profile from the CRM: name, tier, city, total orders and past complaints.
    Use this first in any support conversation. customer_id looks like CUST-001."""
    with db() as c:
        row = c.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    return dict(row) if row else {"error": f"Customer {customer_id} not found"}


@mcp.tool()
def list_customer_orders(customer_id: str) -> list[dict]:
    """List all orders for a customer with status and amount. Use when the customer doesn't give an order id."""
    with db() as c:
        rows = c.execute("SELECT id, product, amount, status FROM orders WHERE customer_id=?", (customer_id,)).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def get_order(order_id: str) -> dict:
    """Get one order's product, amount (INR), status and delivery delay in minutes. order_id looks like ORD-001."""
    with db() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    return dict(row) if row else {"error": f"Order {order_id} not found"}


@mcp.tool()
def check_inventory(product_id: str) -> dict:
    """Check stock level and warehouse for a product. product_id looks like PROD-001."""
    with db() as c:
        row = c.execute("SELECT * FROM inventory WHERE product_id=?", (product_id,)).fetchone()
    return dict(row) if row else {"error": f"Product {product_id} not found"}


@mcp.tool()
def issue_refund(order_id: str, amount: int, reason: str) -> dict:
    """Refund money for an order (amount in INR). Check the order with get_order first.
    Refunds up to Rs 5,000 are processed instantly. Larger refunds are saved as
    PENDING_APPROVAL and a human must approve them."""
    with db() as c:
        order = c.execute("SELECT amount FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            return {"error": f"Order {order_id} not found"}
        if amount <= 0 or amount > order["amount"]:
            return {"error": f"Invalid amount. Must be between 1 and {order['amount']}"}
        status = "PROCESSED" if amount <= REFUND_AUTO_LIMIT else "PENDING_APPROVAL"
        ref_id = f"REF-{random.randint(10000, 99999)}"
        c.execute("INSERT INTO refunds VALUES (?,?,?,?,?,?)",
                  (ref_id, order_id, amount, reason, status, datetime.now().isoformat(timespec="seconds")))
    msg = ("Credited to original payment method in 24-48 hours." if status == "PROCESSED"
           else f"Above Rs {REFUND_AUTO_LIMIT}: sent to a human supervisor for approval.")
    return {"refund_id": ref_id, "order_id": order_id, "amount": amount, "status": status, "message": msg}


@mcp.tool()
def raise_ticket(customer_id: str, issue: str, priority: str = "medium") -> dict:
    """Create a helpdesk ticket for follow-up. priority must be low, medium or high."""
    if priority not in ("low", "medium", "high"):
        return {"error": "priority must be low, medium or high"}
    tid = f"TKT-{random.randint(10000, 99999)}"
    with db() as c:
        c.execute("INSERT INTO tickets VALUES (?,?,?,?,?)",
                  (tid, customer_id, issue, priority, datetime.now().isoformat(timespec="seconds")))
    return {"ticket_id": tid, "customer_id": customer_id, "priority": priority, "status": "OPEN"}


# ─────────────────────────────── RESOURCES ───────────────────────────────
@mcp.resource("shopkart://policy/refunds")
def refund_policy() -> str:
    """ShopKart refund policy."""
    return ("ShopKart Refund Policy\n"
            "1. Delivery late by 30+ minutes: refund delivery fee + 5% of order value (max Rs 5,000).\n"
            "2. Damaged or wrong item: full refund after pickup.\n"
            f"3. Refunds above Rs {REFUND_AUTO_LIMIT} need supervisor approval.\n"
            "4. Premium and Gold customers get priority handling.")

@mcp.resource("shopkart://stats")
def live_stats() -> str:
    """Live counts from the database."""
    with db() as c:
        r = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("customers", "orders", "refunds", "tickets")}
    return str(r)


# ──────────────────────────────── PROMPTS ────────────────────────────────
@mcp.prompt()
def support_reply(customer_name: str, issue: str) -> str:
    """Template for a polite customer support reply."""
    return (f"Write a short, warm reply to {customer_name} about: {issue}. "
            "Apologise once, say exactly what was done (refund id or ticket id), "
            "and end with a clear next step. Max 80 words.")


# ───────────────────────────────── RUN ─────────────────────────────────
if __name__ == "__main__":
    if "--http" in sys.argv:                      # remote mode (Step 20)
        mcp.settings.port = 8765
        mcp.run(transport="streamable-http")      # → http://127.0.0.1:8765/mcp
    else:
        mcp.run()                                 # local mode: stdio (default)
