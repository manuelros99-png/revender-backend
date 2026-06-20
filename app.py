"""
Backend local de Revender.

Corre 100% en la máquina del usuario (no en la nube) -- por diseño. La
extensión de Chrome (que sí corre en un navegador real, con la sesión y
red del usuario) es la única que le habla a Mercado Libre. Este backend
solo normaliza, valúa y guarda lo que la extensión ya extrajo de la
pantalla.

Cómo correrlo:
    cd backend
    pip install -r requirements.txt
    python app.py
Queda escuchando en http://localhost:5057
El dashboard se abre en http://127.0.0.1:5057/dashboard
"""
import os
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory

import storage
import valuation

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
storage.init_db()


def slugify(text):
    return (
        (text or "")
        .strip()
        .lower()
        .replace(" ", "-")
        .replace("ñ", "n")
    )


# Mapeo de nombre de provincia (como llega del form) → slug de MELI en la URL
_MELI_PCIA_SLUGS = {
    "buenos aires": "buenos-aires",
    "caba": "capital-federal",
    "capital federal": "capital-federal",
    "córdoba": "cordoba", "cordoba": "cordoba",
    "santa fe": "santa-fe",
    "mendoza": "mendoza",
    "entre ríos": "entre-rios", "entre rios": "entre-rios",
    "la pampa": "la-pampa",
    "neuquén": "neuquen", "neuquen": "neuquen",
    "río negro": "rio-negro", "rio negro": "rio-negro",
    "tucumán": "tucuman", "tucuman": "tucuman",
    "salta": "salta", "misiones": "misiones", "chaco": "chaco",
    "corrientes": "corrientes", "san luis": "san-luis", "san juan": "san-juan",
    "jujuy": "jujuy", "chubut": "chubut", "santa cruz": "santa-cruz",
    "tierra del fuego": "tierra-del-fuego", "la rioja": "la-rioja",
    "catamarca": "catamarca", "santiago del estero": "santiago-del-estero",
    "formosa": "formosa",
}


_ANTIGUEDAD_SLUGS = {
    "hoy": "_PublishedToday_YES",
    "semana": "_PublishedLastWeek_YES",
    "mes": "_PublishedLastMonth_YES",
}


def build_meli_url(params):
    marca = slugify(params.get("marca"))
    modelo = slugify(params.get("modelo"))
    version = slugify(params.get("version") or "")
    antiguedad = (params.get("antiguedad") or "").strip()

    parts = [f"https://autos.mercadolibre.com.ar/{marca}/{modelo}/dueno-directo"]

    anio_min = params.get("anioMin")
    anio_max = params.get("anioMax")
    if anio_min and anio_max:
        year_seg = str(anio_min) if anio_min == anio_max else f"{anio_min}-{anio_max}"
        parts.append(year_seg)
    elif anio_min:
        parts.append(str(anio_min))
    elif anio_max:
        parts.append(str(anio_max))

    # Slug: marca-modelo-usados
    # La versión NO va en el slug — restringe resultados de MELI innecesariamente.
    # El filtro de versión se aplica via sidebar (_SHORT*VERSION_) y el backend filtra por título.
    slug = f"{marca}-{modelo}-usados"

    if antiguedad in _ANTIGUEDAD_SLUGS:
        slug += _ANTIGUEDAD_SLUGS[antiguedad]

    # Condición "usado" (ID interno fijo de MELI Argentina)
    slug += "_ITEM*CONDITION_2230581"

    zona_lower = (params.get("zona") or "").lower()
    pcia_slug = next(
        (v for k, v in _MELI_PCIA_SLUGS.items() if k in zona_lower),
        None,
    )
    if pcia_slug:
        slug += f"_PciaId_{pcia_slug}"
    slug += "_NoIndex_True"
    parts.append(slug)

    return "/".join(parts)


def build_meli_complementary_url(params):
    """URL de búsqueda por texto en listado.mercadolibre.com.ar.
    Solo se genera cuando el año es exacto (anioMin == anioMax).
    Captura publicaciones de vendedores que no usaron los filtros de MELI correctamente.
    """
    anio_min = params.get("anioMin")
    anio_max = params.get("anioMax")
    if not (anio_min and anio_max and int(anio_min) == int(anio_max)):
        return None

    marca = slugify(params.get("marca"))
    modelo = slugify(params.get("modelo"))
    version = slugify(params.get("version") or "")

    slug = f"{marca}-{modelo}"
    if version:
        slug += f"-{version}"
    slug += f"-{anio_min}"

    return f"https://listado.mercadolibre.com.ar/{slug}"


@app.get("/")
def health():
    return jsonify(status="ok", service="gonzalito-backend")


@app.get("/dashboard")
@app.get("/dashboard/")
def dashboard_index():
    return send_from_directory(DASHBOARD_DIR, "revender.html")


@app.get("/dashboard/<path:filename>")
def dashboard_static(filename):
    return send_from_directory(DASHBOARD_DIR, filename)


@app.post("/api/searches")
def create_search():
    params = request.get_json(force=True) or {}
    if not params.get("marca") or not params.get("modelo"):
        return jsonify(error="marca y modelo son obligatorios"), 400

    search_id = "s" + uuid.uuid4().hex[:12]
    meli_url = build_meli_url(params)
    meli_url_alt = build_meli_complementary_url(params)
    storage.create_search(search_id, params, meli_url)
    return jsonify(searchId=search_id, meliUrl=meli_url, meliUrlAlt=meli_url_alt, status="pendiente")


@app.post("/api/searches/<search_id>/status")
def update_status(search_id):
    body = request.get_json(force=True) or {}
    storage.set_status(search_id, body.get("status", "error"), body.get("message"))
    return jsonify(ok=True)


@app.post("/api/searches/<search_id>/listings")
def receive_listings(search_id):
    search = storage.get_search(search_id)
    if not search:
        return jsonify(error="búsqueda no encontrada"), 404

    body = request.get_json(force=True) or {}
    listings_raw = body.get("listings", [])

    storage.set_status(search_id, "analizando")
    ids = storage.save_listings(search_id, listings_raw)

    search_params = {
        "anioMin": search["anio_min"],
        "anioMax": search["anio_max"],
        "kmMax": search["km_max"],
        "precioMax": search["precio_max"],
        "threshold": search["threshold_pct"] or 12,
        "zona": search.get("zona") or "",
        "version": search.get("version") or "",
    }

    opportunities, discarded = valuation.run_valuation(listings_raw, search_params)

    storage.clear_results(search_id)
    # Mapeamos cada resultado a su listing_id real en la DB matcheando por URL
    # (normalize_listing preserva todos los campos originales, incluido url).
    all_rows = opportunities + discarded
    db_rows = []
    for i, row in enumerate(all_rows):
        db_rows.append({
            "listing_id": ids[listings_raw.index(_find_original(row["listing"], listings_raw))] if listings_raw else None,
            "market_value_usd": row["market_value_usd"],
            "diff_pct": row["diff_pct"],
            "score": row["score"],
            "recommendation": row["recommendation"],
            "motivo": row["motivo"],
            "riesgos": row["riesgos"],
            "is_opportunity": row["is_opportunity"],
        })
    storage.save_results(search_id, db_rows)

    storage.set_status(search_id, "completada")

    return jsonify(
        status="completada",
        searchId=search_id,
        oportunidades=_serialize_rows(opportunities),
        descartadas=_serialize_rows(discarded),
    )


def _find_original(listing, listings_raw):
    for raw in listings_raw:
        if raw.get("url") == listing.get("url"):
            return raw
    return listings_raw[0] if listings_raw else None


def _serialize_rows(rows):
    out = []
    for row in rows:
        l = row["listing"]
        out.append({
            "titulo": l.get("title"),
            "anio": l.get("year"),
            "km": l.get("km"),
            "precio": round(l["price_usd"], 0) if l.get("price_usd") is not None else None,
            "moneda": "USD",
            "ubicacion": l.get("location"),
            "link": l.get("url"),
            "valorMercado": round(row["market_value_usd"], 0) if row["market_value_usd"] else None,
            "score": row["score"],
            "recomendacion": row["recommendation"],
            "esOportunidad": row["is_opportunity"],
            "motivo": row["motivo"],
            "riesgos": row["riesgos"],
        })
    return out


@app.get("/api/searches/<search_id>")
def get_search_status(search_id):
    search = storage.get_search(search_id)
    if not search:
        return jsonify(error="no encontrada"), 404
    results = storage.get_results_with_listings(search_id)
    oportunidades = [r for r in results if r["is_opportunity"]]
    descartadas = [r for r in results if not r["is_opportunity"]]
    return jsonify(
        search=search,
        status=search["status"],
        statusMessage=search["status_message"],
        oportunidades=[_row_from_db(r) for r in oportunidades],
        descartadas=[_row_from_db(r) for r in descartadas],
    )


def _row_from_db(r):
    price_usd = valuation.to_usd(r.get("price"), r.get("currency"))
    return {
        "titulo": r.get("title"),
        "anio": r.get("year"),
        "km": r.get("km"),
        "precio": round(price_usd, 0) if price_usd is not None else None,
        "moneda": "USD",
        "ubicacion": r.get("location"),
        "link": r.get("url"),
        "valorMercado": round(r["market_value_usd"], 0) if r.get("market_value_usd") else None,
        "score": r.get("score"),
        "recomendacion": r.get("recommendation"),
        "esOportunidad": bool(r.get("is_opportunity")),
        "motivo": r.get("motivo"),
        "riesgos": r.get("riesgos"),
    }


@app.get("/api/searches")
def list_searches():
    return jsonify(searches=storage.list_searches())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5057))
    # En Railway PORT viene seteado → bind en 0.0.0.0; localmente → 127.0.0.1
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=not bool(os.environ.get("PORT")))
