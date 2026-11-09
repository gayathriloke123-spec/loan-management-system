import re
import sqlite3
from pathlib import Path

from flask import (Flask, flash, g, redirect, render_template, request,
                   url_for)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "loans.db"

app = Flask(__name__)
app.secret_key = "change-this-secret-key"  # needed for flash messages


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id        TEXT PRIMARY KEY,
                name      TEXT NOT NULL,
                phone     TEXT NOT NULL,
                loan      REAL NOT NULL,
                interest  REAL NOT NULL,
                duration  INTEGER NOT NULL,
                paid      REAL NOT NULL DEFAULT 0,
                created   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


# ----------------------------------------------------------------- helpers
def calculate_emi(principal, annual_rate, months):
    """Standard reducing-balance EMI: P*r*(1+r)^n / ((1+r)^n - 1)."""
    r = annual_rate / 12 / 100
    if r == 0:
        return principal / months
    factor = (1 + r) ** months
    return principal * r * factor / (factor - 1)


def enrich(row):
    """Turn a DB row into a dict with EMI, total payable and balance."""
    c = dict(row)
    c["emi"] = round(calculate_emi(c["loan"], c["interest"], c["duration"]), 2)
    c["total"] = round(c["emi"] * c["duration"], 2)
    c["interest_amount"] = round(c["total"] - c["loan"], 2)
    c["balance"] = max(round(c["total"] - c["paid"], 2), 0)
    c["progress"] = min(round(c["paid"] / c["total"] * 100), 100) if c["total"] else 0
    c["cleared"] = c["balance"] <= 0
    return c


@app.template_filter("inr")
def inr(value):
    """Format a number as ₹ with Indian digit grouping (12,34,567.50)."""
    value = round(float(value), 2)
    whole, dec = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        whole = f"{head},{tail}"
    text = whole if dec == "00" else f"{whole}.{dec}"
    return f"{'-' if value < 0 else ''}₹{text}"


def render_dashboard(result=None):
    rows = get_db().execute(
        "SELECT * FROM customers ORDER BY created DESC, id"
    ).fetchall()
    customers = [enrich(r) for r in rows]
    stats = {
        "count": len(customers),
        "lent": sum(c["loan"] for c in customers),
        "collected": sum(c["paid"] for c in customers),
        "outstanding": sum(c["balance"] for c in customers),
    }
    return render_template("index.html", customers=customers,
                           result=result, stats=stats)


# ------------------------------------------------------------------ routes
@app.route("/")
def index():
    return render_dashboard()


@app.route("/add", methods=["POST"])
def add_customer():
    cid = request.form["id"].strip()
    name = request.form["name"].strip()
    phone = request.form["phone"].strip()

    try:
        loan = float(request.form["loan"])
        interest = float(request.form["interest"])
        duration = int(request.form["duration"])
    except ValueError:
        flash("Loan amount, interest rate and duration must be numbers.", "error")
        return redirect(url_for("index"))

    if loan <= 0 or duration <= 0 or interest < 0:
        flash("Loan and duration must be above zero, and interest cannot be negative.", "error")
        return redirect(url_for("index"))

    db = get_db()
    if db.execute("SELECT 1 FROM customers WHERE id = ?", (cid,)).fetchone():
        flash(f"Customer ID {cid} already exists. Use a different ID.", "error")
        return redirect(url_for("index"))

    db.execute(
        "INSERT INTO customers (id, name, phone, loan, interest, duration) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cid, name, phone, loan, interest, duration),
    )
    db.commit()
    flash(f"{name} was added.", "success")
    return redirect(url_for("index"))


@app.route("/emi/<customer_id>")
def emi(customer_id):
    row = get_db().execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        flash("Customer not found.", "error")
        return redirect(url_for("index"))
    return render_dashboard(result=enrich(row))


@app.route("/payment/<customer_id>", methods=["POST"])
def payment(customer_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        flash("Customer not found.", "error")
        return redirect(url_for("index"))

    try:
        amount = float(request.form["payment"])
    except ValueError:
        flash("Enter the payment amount as a number.", "error")
        return redirect(url_for("index"))

    customer = enrich(row)
    if amount <= 0:
        flash("Payment must be more than ₹0.", "error")
    elif amount > customer["balance"] + 0.001:
        flash(f"{customer['name']} owes only {inr(customer['balance'])}. "
              "Enter a smaller amount.", "error")
    else:
        db.execute("UPDATE customers SET paid = paid + ? WHERE id = ?",
                   (amount, customer_id))
        db.commit()
        flash(f"Recorded {inr(amount)} from {customer['name']}.", "success")
    return redirect(url_for("index"))


@app.route("/delete/<customer_id>", methods=["POST"])
def delete_customer(customer_id):
    db = get_db()
    db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    db.commit()
    flash("Customer deleted.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
