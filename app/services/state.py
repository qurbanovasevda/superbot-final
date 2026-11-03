import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.services.catalog_service import DomainError


class StateStore:
    """Persist mock bookings and per-session carts without modifying JSON sources."""

    def __init__(self, path: Path, catalog_service):
        self.path = path
        self.service = catalog_service
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS cart (
                    session TEXT, product TEXT, quantity INTEGER,
                    PRIMARY KEY(session, product));
                CREATE TABLE IF NOT EXISTS bookings (
                    id TEXT PRIMARY KEY, session TEXT, domain TEXT, record TEXT,
                    quantity INTEGER, request_key TEXT, payload TEXT,
                    UNIQUE(session, request_key));
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_session(self):
        session = str(uuid4())
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES (?)", (session,))
        return {"session_id": session}

    def require_session(self, db, session):
        if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
            raise DomainError("Session not found.", 404)

    def cart(self, session):
        with self.connection() as db:
            self.require_session(db, session)
            rows = db.execute("SELECT product,quantity FROM cart WHERE session=? ORDER BY product", (session,)).fetchall()
        items = []
        total = 0
        for row in rows:
            product = self.service.detail("products", row["product"])
            subtotal = product.price_azn * row["quantity"]
            total += subtotal
            items.append({"product": product, "quantity": row["quantity"], "subtotal_azn": str(subtotal)})
        return {"items": items, "total_azn": str(total), "currency": "AZN"}

    def set_cart(self, session, product_id, quantity):
        product = self.service.detail("products", product_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.require_session(db, session)
            if quantity > product.stock:
                raise DomainError("Requested quantity exceeds product stock.", 409)
            if quantity == 0:
                db.execute("DELETE FROM cart WHERE session=? AND product=?", (session, product_id))
            else:
                db.execute("INSERT INTO cart VALUES (?,?,?) ON CONFLICT(session,product) DO UPDATE SET quantity=excluded.quantity", (session, product_id, quantity))
        return self.cart(session)

    def reserve(self, session, domain, record_id, quantity, request_key):
        record = self.service.detail(domain, record_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.require_session(db, session)
            previous = db.execute("SELECT * FROM bookings WHERE session=? AND request_key=?", (session, request_key)).fetchone()
            if previous:
                if (previous["domain"], previous["record"], previous["quantity"]) != (domain, record_id, quantity):
                    raise DomainError("Request key was already used for another reservation.", 409)
                return json.loads(previous["payload"])
            used = db.execute("SELECT COALESCE(SUM(quantity),0) FROM bookings WHERE domain=? AND record=?", (domain, record_id)).fetchone()[0]
            if quantity > record.seats_available - used:
                raise DomainError("Not enough seats available.", 409)
            result = {"reservation_id": str(uuid4()), "domain": domain, "record_id": record_id,
                      "quantity": quantity, "total_azn": str(record.price_azn * quantity), "status": "mock_confirmed"}
            db.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?)", (result["reservation_id"], session, domain, record_id, quantity, request_key, json.dumps(result)))
            return result

    def availability(self, domain, record_id):
        record = self.service.detail(domain, record_id)
        with self.connection() as db:
            used = db.execute("SELECT COALESCE(SUM(quantity),0) FROM bookings WHERE domain=? AND record=?", (domain, record_id)).fetchone()[0]
        return {"record_id": record_id, "seats_available": record.seats_available - used}

    def reservations(self, session):
        with self.connection() as db:
            self.require_session(db, session)
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM bookings WHERE session=? ORDER BY rowid", (session,))]
