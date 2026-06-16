"""
Persistencia: SQLite en local, PostgreSQL en Railway (via DATABASE_URL).
La lógica de la app no cambia — solo cambia el driver de conexión.
"""
import os, json, time

# Railway expone postgres:// pero psycopg2 necesita postgresql://
_DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)
USE_PG = bool(_DATABASE_URL)

_AUTO_ID = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"

if not USE_PG:
    import sqlite3
    _DB_PATH = os.path.join(os.path.dirname(__file__), "gonzalito.db")

_SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS searches (
        id TEXT PRIMARY KEY,
        marca TEXT, modelo TEXT, version TEXT,
        anio_min INTEGER, anio_max INTEGER,
        km_max INTEGER, precio_max REAL, zona TEXT,
        threshold_pct REAL, status TEXT, status_message TEXT,
        meli_url TEXT, created_at REAL
    )""",
    f"""CREATE TABLE IF NOT EXISTS listings (
        id {_AUTO_ID},
        search_id TEXT, title TEXT, price REAL, currency TEXT,
        year INTEGER, km INTEGER, location TEXT, url TEXT,
        image_url TEXT, seller_type TEXT, raw_text TEXT,
        attributes_json TEXT, exclusion_flags_json TEXT,
        risk_flags_json TEXT, created_at REAL
    )""",
    f"""CREATE TABLE IF NOT EXISTS results (
        id {_AUTO_ID},
        search_id TEXT, listing_id INTEGER,
        market_value_usd REAL, diff_pct REAL, score REAL,
        recommendation TEXT, motivo TEXT, riesgos TEXT,
        is_opportunity INTEGER, created_at REAL
    )""",
]


def get_conn():
    if USE_PG:
        import psycopg2, psycopg2.extras
        return psycopg2.connect(_DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _exec(conn, sql, params=()):
    """Ejecuta una query parametrizada. Convierte ? a %s para PostgreSQL."""
    if USE_PG:
        sql = sql.replace("?", "%s")
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur


def _one(cur):
    row = cur.fetchone()
    return dict(row) if row else None


def _all(cur):
    return [dict(r) for r in cur.fetchall()]


def init_db():
    conn = get_conn()
    for stmt in _SCHEMA:
        _exec(conn, stmt)
    conn.commit()
    conn.close()


def create_search(search_id, params, meli_url):
    conn = get_conn()
    _exec(conn,
        """INSERT INTO searches
           (id,marca,modelo,version,anio_min,anio_max,km_max,precio_max,zona,
            threshold_pct,status,status_message,meli_url,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (search_id, params.get("marca"), params.get("modelo"), params.get("version"),
         params.get("anioMin"), params.get("anioMax"), params.get("kmMax"), params.get("precioMax"),
         params.get("zona"), params.get("threshold", 12),
         "pendiente", None, meli_url, time.time()),
    )
    conn.commit()
    conn.close()


def set_status(search_id, status, message=None):
    conn = get_conn()
    _exec(conn, "UPDATE searches SET status=?, status_message=? WHERE id=?",
          (status, message, search_id))
    conn.commit()
    conn.close()


def get_search(search_id):
    conn = get_conn()
    row = _one(_exec(conn, "SELECT * FROM searches WHERE id=?", (search_id,)))
    conn.close()
    return row


def list_searches():
    conn = get_conn()
    rows = _all(_exec(conn, "SELECT * FROM searches ORDER BY created_at DESC"))
    conn.close()
    return rows


def save_listings(search_id, listings):
    conn = get_conn()
    ids = []
    for l in listings:
        params = (
            search_id, l.get("title"), l.get("price"), l.get("currency"),
            l.get("year"), l.get("km"), l.get("location"), l.get("url"),
            l.get("imageUrl"), l.get("sellerType", "unknown"), l.get("rawText"),
            json.dumps(l.get("attributes", {})),
            json.dumps(l.get("exclusionFlags", [])),
            json.dumps(l.get("riskFlags", [])),
            time.time(),
        )
        sql = """INSERT INTO listings
                 (search_id,title,price,currency,year,km,location,url,image_url,
                  seller_type,raw_text,attributes_json,exclusion_flags_json,
                  risk_flags_json,created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        if USE_PG:
            cur = _exec(conn, sql + " RETURNING id", params)
            ids.append(_one(cur)["id"])
        else:
            cur = _exec(conn, sql, params)
            ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return ids


def clear_results(search_id):
    conn = get_conn()
    _exec(conn, "DELETE FROM results WHERE search_id=?", (search_id,))
    conn.commit()
    conn.close()


def save_results(search_id, results):
    conn = get_conn()
    for r in results:
        _exec(conn,
            """INSERT INTO results
               (search_id,listing_id,market_value_usd,diff_pct,score,
                recommendation,motivo,riesgos,is_opportunity,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (search_id, r.get("listing_id"), r.get("market_value_usd"), r.get("diff_pct"),
             r.get("score"), r.get("recommendation"), r.get("motivo"), r.get("riesgos"),
             1 if r.get("is_opportunity") else 0, time.time()),
        )
    conn.commit()
    conn.close()


def get_results_with_listings(search_id):
    conn = get_conn()
    rows = _all(_exec(conn,
        """SELECT results.*, listings.title, listings.price, listings.currency,
                  listings.year, listings.km, listings.location, listings.url,
                  listings.image_url
           FROM results JOIN listings ON results.listing_id = listings.id
           WHERE results.search_id=?""",
        (search_id,),
    ))
    conn.close()
    return rows
