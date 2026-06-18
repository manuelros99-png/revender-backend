"""
Normalización, motor de exclusiones, comparables, valuación y scoring.

Deliberadamente simple (sin pandas, sin ML) -- estadística básica con la
librería estándar. Esto es un MVP: la regla general es "mejor cero
resultados que una oportunidad dudosa", así que los umbrales son
conservadores y cualquier señal de riesgo penaliza fuerte en vez de
descartar en silencio.
"""
import re
import time
import urllib.request

# Tipo de cambio de respaldo: se usa solo si el fetch al Banco Nación falla.
ARS_USD_FALLBACK = 1435

_bna_cache = {"rate": None, "ts": 0}
_BNA_TTL = 3600  # 1 hora


def fetch_bna_usd_rate():
    """Cotización dólar oficial (promedio compra/venta) del Banco Nación Argentina."""
    now = time.time()
    if _bna_cache["rate"] and now - _bna_cache["ts"] < _BNA_TTL:
        return _bna_cache["rate"]
    try:
        req = urllib.request.Request(
            "https://www.bna.com.ar/Cotizaciones",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Gonzalito/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        def parse_ar(s):
            return float(s.strip().replace(".", "").replace(",", "."))

        m = re.search(
            r"D[oó]lar\s+Estadounidense[^<]*</td>\s*<td[^>]*>([\d.,]+)</td>\s*<td[^>]*>([\d.,]+)</td>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            compra, venta = parse_ar(m.group(1)), parse_ar(m.group(2))
            if compra > 100 and venta > 100:
                rate = (compra + venta) / 2
                _bna_cache["rate"] = rate
                _bna_cache["ts"] = now
                return rate
    except Exception:
        pass
    return _bna_cache["rate"] or ARS_USD_FALLBACK


def to_usd(price, currency):
    """Convierte un precio a USD usando la cotización del BNA."""
    if price is None:
        return None
    if (currency or "ARS").upper() == "ARS":
        return price / fetch_bna_usd_rate()
    return float(price)

# Mapeo de nombres que el usuario puede poner → términos que buscar en la location
# (que viene como "Ciudad, Provincia" de la API de MELI)
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

# Frases de varias palabras o sin ambigüedad numérica -- substring simple es seguro.
EXCLUSION_KEYWORDS = [
    "plan de ahorro", "plan ahorro", "cuotas", "anticipo", "entrega y cuotas",
    "adjudicado", "financiado", "prenda", "prendado", "deuda", "sucesión",
    "sucesion", "chocado", "para repuestos", "solo repuestos", "motor roto",
    "sin transferir", "titular fallecido", "solo partes",
]

# "0km"/"0 km" necesitan regex con límite de palabra: un substring naive
# matchea falsamente cualquier kilometraje que termine en 0 antes de "km"
# (ej. "49000 km" contiene "...0 km"). (?<!\d) exige que el 0 no esté
# precedido por otro dígito.
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
    """Filtros duros del usuario (año/km/precio/zona/versión) -- si no cumple, ni se compara."""
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
    if precio_max and listing.get("price_usd") and listing["price_usd"] * fetch_bna_usd_rate() > precio_max:
        return False
    if zona_str and not _passes_zona(listing.get("location") or "", zona_str):
        return False
    if version:
        title = (listing.get("title") or "").lower()
        if version not in title:
            return False
    return True


def _passes_zona(location, zona_str):
    """True si la ubicación de la publicación coincide con alguna zona solicitada."""
    location_lower = location.lower()
    for zona in [z.strip().lower() for z in zona_str.split(",") if z.strip()]:
        aliases = ZONA_ALIASES.get(zona, [zona])
        if any(alias in location_lower for alias in aliases):
            return True
    return False


def estimate_market_value(candidates, exclude_url=None):
    """
    Precio mínimo (piso de mercado) entre todos los candidatos, excluyendo
    el listing actual para que su propio precio no contamine su referencia.
    Si una publicación está notoriamente por debajo del mínimo del resto,
    eso indica una oportunidad real.
    """
    prices = [
        l["price_usd"] for l in candidates
        if l.get("price_usd") and l.get("url") != exclude_url
    ]
    if not prices:
        return None, 0
    return min(prices), len(prices)


def score_listing(listing, market_value, comparable_count, diff_pct):
    """Score 0-100. Penaliza fuerte ante riesgos o datos incompletos."""
    if market_value is None:
        return 0, "requiere revisión manual", "", "Sin suficientes comparables para estimar valor de mercado"

    score = 0
    motivos = []
    riesgos = []

    # Descuento contra comparables (peso alto)
    if diff_pct >= 20:
        score += 40
        motivos.append(f"{diff_pct:.0f}% por debajo del valor estimado de mercado")
    elif diff_pct >= 10:
        score += 30
        motivos.append(f"{diff_pct:.0f}% por debajo del valor estimado de mercado")
    elif diff_pct > 0:
        score += 10

    # Liquidez del grupo comparable (peso medio)
    if comparable_count >= 5:
        score += 15
    elif comparable_count >= 3:
        score += 8

    # Completitud de datos (peso medio, penaliza si falta algo clave)
    missing = [k for k in ("year", "km", "location") if not listing.get(k)]
    if not missing:
        score += 15
    else:
        score -= 5 * len(missing)
        riesgos.append(f"Datos incompletos: falta {', '.join(missing)}")

    # Riesgos detectados en el texto (penalización fuerte)
    if listing.get("riskFlags"):
        score -= 20 * len(listing["riskFlags"])
        riesgos.append("Palabras de riesgo detectadas en la publicación: " + ", ".join(listing["riskFlags"]))

    # Precio sospechosamente bajo (>35% bajo mercado) -> no es señal positiva ciega, va a revisión manual
    if diff_pct >= 35:
        riesgos.append("Descuento inusualmente alto (>35%) -- revisar manualmente antes de contactar, podría haber un error de publicación o un problema no declarado")
        score = min(score, 55)

    score = max(0, min(100, score))

    if riesgos:
        recommendation = "revisar documentación" if score >= 40 else "descartar"
    elif score >= 70:
        recommendation = "contactar rápido"
    elif score >= 40:
        recommendation = "revisar mecánica"
    else:
        recommendation = "descartar"

    return score, recommendation, " · ".join(motivos), " · ".join(riesgos)


def run_valuation(listings_raw, search_params):
    """
    Pipeline completo: normaliza, filtra duro, excluye, agrupa comparables,
    valúa y scorea. Devuelve (oportunidades, descartadas) en el shape que
    espera el dashboard.
    """
    threshold = search_params.get("threshold", 12)

    normalized = [normalize_listing(l, search_params) for l in listings_raw]
    hard_filtered = [l for l in normalized if passes_hard_filters(l, search_params)]

    excluded = [l for l in hard_filtered if l["exclusionFlags"]]
    candidates = [l for l in hard_filtered if not l["exclusionFlags"]]

    opportunities = []
    discarded = []

    for listing in candidates:
        # Valor de mercado = precio mínimo del resto de candidatos (leave-one-out).
        # Si esta publicación es notoriamente más barata que todas las demás, la diferencia
        # será positiva y se evaluará como oportunidad.
        market_value, comparable_count = estimate_market_value(
            candidates, exclude_url=listing.get("url")
        )
        price_usd = listing.get("price_usd")
        diff_pct = None
        if market_value and price_usd:
            diff_pct = round(((market_value - price_usd) / market_value) * 100, 1)

        score, recommendation, motivo, riesgos = score_listing(
            listing, market_value, comparable_count, diff_pct or 0
        )

        is_opportunity = bool(
            diff_pct is not None and diff_pct >= threshold and not listing["riskFlags"]
        )

        result_row = {
            "listing": listing,
            "market_value_usd": market_value,
            "diff_pct": diff_pct,
            "score": score,
            "recommendation": recommendation,
            "motivo": motivo,
            "riesgos": riesgos,
            "is_opportunity": is_opportunity,
        }

        if is_opportunity:
            opportunities.append(result_row)
        else:
            discarded.append(result_row)

    for listing in excluded:
        discarded.append({
            "listing": listing,
            "market_value_usd": None,
            "diff_pct": None,
            "score": 0,
            "recommendation": "descartar",
            "motivo": "",
            "riesgos": "Excluido automáticamente: " + ", ".join(listing["exclusionFlags"]),
            "is_opportunity": False,
        })

    opportunities.sort(key=lambda r: r["score"], reverse=True)
    return opportunities, discarded
