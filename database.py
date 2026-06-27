import sqlite3

from flask import g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect("database.db", check_same_thread=False, timeout=25)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
    return g.db


def init_db():
    db = get_db()
    with open("schema.sql", "r") as f:
        db.executescript(f.read())

    try:
        db.execute("SELECT stake FROM direct_challenges LIMIT 1")
    except sqlite3.OperationalError:
        db.execute(
            "ALTER TABLE direct_challenges ADD COLUMN stake REAL NOT NULL DEFAULT 10"
        )

    try:
        db.execute("SELECT accepted_terms FROM users LIMIT 1")
    except sqlite3.OperationalError:
        db.execute(
            "ALTER TABLE users ADD COLUMN accepted_terms BOOLEAN NOT NULL DEFAULT 0"
        )

    db.commit()
    print("✅ Database schema loaded successfully!")
