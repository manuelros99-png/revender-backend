"""
Normalización, filtros, y ranking de los 10 autos más baratos.

Precios siempre en USD usando la cotización del dólar Blue (dolarhoy.com).
"""
import re
import time
import urllib.request

ARS_USD_FALLBACK = 1435  # fallback si dolarhoy.com no responde

_blue_cache = {"rate": None, "ts": 0}
_CACHE_TTL = 3600  # 1 hora


def fetch_blue_usd_rate():
    """Cotización dólar Blue (promedio compra/venta) de dolarhoy.com."""
    now = time.time()
    if _blue_cache["rate"] and now - _blue_cache["ts"] < _CACHE_TTL:
        return _blue_cache["rate"]
    try:
        req = urllib.request.Request(
            "https://dolarhoy.com/",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        def parse_peso(s):
            # Formato argentino: "1.200" o "1,200" → 1200
            return float(s.strip().replace(".", "").replace(",", ""))

        # dolarhoy.com muestra "Compra" y "Venta" dentro de la sección Blue
        # Buscamos la sección que contenga "blue" y extraemos los dos valores numéricos
        m = re.search(
            r"blue.{0,400}?compra.{0,100}?\$?\s*([\d.,]+).{0,200}?venta.{0,100}?\$?\s*([\d.,]+)",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not m:
            # Intento alternativo: buscar dos números grandes cerca de "blue"
            m = re.search(
                r"blue[^<]{0,600}?([\d]{3,4})[^<]{0,200}?([\d]{3,4})",
                html,
                re.IGNORECASE | re.DOTALL,
            )
        if m:
            compra = parse_peso(m.group(1))
            venta = parse_peso(m.group(2))
            if compra > 100 and venta > 100:
                rate = (compra + venta) / 2
                _blue_cache["rate"] = rate
                _blue_cache["ts"] = now
                return rate
    except Exception:
        pass
    return _blue_cache["rate"] or ARS_USD_FALLBACK


def to_usd(price, currency):
    """Convierte un precio a USD usando la cotización del dólar Blue."""
    if price is None:
        return None
    if (currency or "ARS").upper() == "ARS":
        return price / fetch_blue_usd_rate()
    return float(price)


ZONA_ALIASES = {
    "caba": ["capital federal", "ciudad autónoma", "ciudad autonoma", "caba"],
    "buenos aires": ["buenos aires"],
    "córdoba": ["córdoba", "cordoba"],
    "santa fe": ["santa fe"],
    "mendoza": ["mendoza"],
    "entre ríos": ["entre ríos", "entre rios"],
    "la pampa": ["la pampa"],
    "neuquén": ["neuquén", "neuquen"],
    "río negro": ["río negro", "rio negro"],
    "tucumán": ["tucumán", "tucuman"],
    "salta": ["salta"],
    "misiones": ["misiones"],
    "chaco": ["chaco"],
    "corrientes": ["corrientes"],
    "san luis": ["san luis"],
    "san juan": ["san juan"],
    "jujuy": ["jujuy"],
    "chubut": ["chubut"],
    "santa cruz": ["santa cruz"],
    "tierra del fuego": ["tierra del fuego"],
    "la rioja": ["la rioja"],
    "catamarca": ["catamarca"],
    "santiago del estero": ["santiago del estero"],
    "formosa": ["formosa"],
}

EXCLUSION_KEYWORDS = [
    "plan de ahorro", "plan ahorro", "cuotas", "anticipo", "entrega y cuotas",
    "adjudicado", "financiado", "prenda", "prendado", "deuda", "sucesión",
    "sucesion", "chocado", "para repuestos", "solo repuestos", "motor roto",
    "sin transferir", "titular fallecido", "solo partes",
]

EXCLUSION_PATTERNS = [
    re.compile(r"(?<!\d)0\s?km\b"),
]

RISK_KEYWORDS = [
    "negociable", "urgente", "necesito vender", "no funciona", "falla",
    "humo", "pérdida de aceite", "perdida de aceite", "fuga", "ruido",
]


def normalize_listing(raw, search_params):
    """Convierte un listing crudo (de la extensión) en una estructura limpia."""
    text = " ".join(
        filter(None, [raw.get("title", ""), raw.get("rawText", "")])
    ).lower()

    exclusion_flags = [kw for kw in EXCLUSION_KEYWORDS if kw in text]
    if any(p.search(text) for p in EXCLUSION_PATTERNS):
        exclusion_flags.append("0km")
    risk_flags = [kw for kw in RISK_KEYWORDS if kw in text]

    price = raw.get("price")
    currency = (raw.get("currency") or "ARS").upper()
    price_usd = to_usd(price, currency)

    return {
        **raw,
        "currency": currency,
        "price_usd": price_usd,
        "exclusionFlags": exclusion_flags,
        "riskFlags": risk_flags,
    }


def passes_hard_filters(listing, search_params):
    """Filtros del usuario: año, km, precio máximo, zona, versión."""
    anio_min = search_params.get("anioMin")
    anio_max = search_params.get("anioMax")
    km_max = search_params.get("kmMax")
    precio_max = search_params.get("precioMax")
    zona_str = search_params.get("zona") or ""
    version = (search_params.get("version") or "").strip().lower()

    year = listing.get("year")
    km = listing.get("km")

    if anio_min and year and year < anio_min:
        return False
    if anio_max and year and year > anio_max:
        return False
    if km_max and km and km > km_max:
        return False
    if precio_max and listing.get("price_usd") and listing["price_usd"] * fetch_blue_usd_rate() > precio_max:
        return False
    if zona_str and not _passes_zona(listing.get("location") or "", zona_str):
        return False
    if version:
        title = (listing.get("title") or "").lower()
        if version not in title:
            return False
    return True


def _passes_zona(location, zona_str):
    location_lower = location.lower()
    for zona in [z.strip().lower() for z in zona_str.split(",") if z.strip()]:
        aliases = ZONA_ALIASES.get(zona, [zona])
        if any(alias in location_lower for alias in aliases):
            return True
    return False


def run_valuation(listings_raw, search_params):
    """
    Filtra, normaliza y devuelve un ranking de los 10 más baratos en USD Blue.
    Los primeros 10 van como 'oportunidades' (top del ranking), el resto como descartados.
    """
    normalized = [normalize_listing(l, search_params) for l in listings_raw]
    hard_filtered = [l for l in normalized if passes_hard_filters(l, search_params)]

    excluded = [l for l in hard_filtered if l["exclusionFlags"]]
    candidates = [l for l in hard_filtered if not l["exclusionFlags"]]

    # Ordenar por precio USD Blue (más barato primero); sin precio al final
    candidates.sort(key=lambda l: l.get("price_usd") or float("inf"))

    ranking = []
    for i, listing in enumerate(candidates):
        rank = i + 1
        price_usd = listing.get("price_usd")
        risk_txt = " · ".join(listing.get("riskFlags", []))
        is_top10 = rank <= 10
        ranking.append({
            "listing": listing,
            "market_value_usd": None,
            "diff_pct": None,
            "score": rank,
            "recommendation": "contactar rápido" if rank <= 3 else ("ver presencialmente" if rank <= 10 else "más caro"),
            "motivo": f"#{rank} más barato",
            "riesgos": risk_txt,
            "is_opportunity": is_top10,
        })

    for listing in excluded:
        ranking.append({
            "listing": listing,
            "market_value_usd": None,
            "diff_pct": None,
            "score": 0,
            "recommendation": "descartar",
            "motivo": "",
            "riesgos": "Excluido: " + ", ".join(listing["exclusionFlags"]),
            "is_opportunity": False,
        })

    opportunities = [r for r in ranking if r["is_opportunity"]]
    discarded = [r for r in ranking if not r["is_opportunity"]]
    return opportunities, discarded
