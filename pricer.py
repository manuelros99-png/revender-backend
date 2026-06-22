"""
Consulta el valor de mercado de un auto usando la API pública de MercadoLibre.
Valor de mercado = promedio del 2° y 3° auto más barato (en USD Blue).
"""
import json
import urllib.parse
import urllib.request

from valuation import fetch_blue_usd_rate, to_usd

MELI_SEARCH = "https://api.mercadolibre.com/sites/MLA/search"
MELI_AUTOS  = "MLA1744"   # categoría Autos y Camionetas Argentina

SKIP_KEYWORDS = [
    "plan de ahorro", "plan ahorro", "cuotas", "anticipo", "adjudicado",
    "0 km", "0km", "financiado", "prenda", "prendado", "para repuestos",
    "solo repuestos", "chocado", "sin transferir",
]


def _build_params(marca, modelo, version, anio_min, anio_max):
    parts = [marca, modelo]
    if version:
        parts.append(version)
    if anio_min and anio_max and str(anio_min) == str(anio_max):
        parts.append(str(anio_min))
    elif anio_min and not anio_max:
        parts.append(str(anio_min))

    return {
        "q":        " ".join(parts),
        "category": MELI_AUTOS,
        "condition": "used",
        "limit":    50,
    }


def _fetch_meli(params):
    url = MELI_SEARCH + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_market_price(marca, modelo, version=None, anio_min=None, anio_max=None):
    """
    Devuelve dict con:
      valorMercado    — promedio 2° y 3° más barato, en USD
      referencias     — los autos usados para el cálculo
      todosLosResultados — top 10 encontrados
      cotizacionBlue  — tasa usada
      totalEncontrados
      query           — texto buscado
    """
    params  = _build_params(marca, modelo, version, anio_min, anio_max)
    data    = _fetch_meli(params)
    results = data.get("results", [])
    blue    = fetch_blue_usd_rate()

    priced = []
    for item in results:
        title    = item.get("title", "")
        price    = item.get("price")
        currency = (item.get("currency_id") or "ARS").upper()

        if not price or price <= 0:
            continue
        if any(kw in title.lower() for kw in SKIP_KEYWORDS):
            continue

        # Año desde atributos
        year = None
        for attr in item.get("attributes", []):
            if attr.get("id") == "VEHICLE_YEAR":
                try:
                    year = int(attr["value_name"])
                except Exception:
                    pass

        # Filtro de año estricto
        if anio_min and year and year < int(anio_min):
            continue
        if anio_max and year and year > int(anio_max):
            continue

        price_usd = to_usd(price, currency)
        priced.append({
            "titulo":          title,
            "precio_usd":      round(price_usd),
            "precio_original": price,
            "moneda":          currency,
            "anio":            year,
            "url":             item.get("permalink", ""),
            "thumbnail":       item.get("thumbnail", ""),
        })

    priced.sort(key=lambda x: x["precio_usd"])

    if len(priced) >= 3:
        refs         = [priced[1], priced[2]]
        market_value = round((priced[1]["precio_usd"] + priced[2]["precio_usd"]) / 2)
    elif len(priced) == 2:
        refs         = [priced[0], priced[1]]
        market_value = round((priced[0]["precio_usd"] + priced[1]["precio_usd"]) / 2)
    elif len(priced) == 1:
        refs         = [priced[0]]
        market_value = priced[0]["precio_usd"]
    else:
        refs         = []
        market_value = None

    return {
        "valorMercado":      market_value,
        "referencias":       refs,
        "todosLosResultados": priced[:10],
        "cotizacionBlue":    round(blue),
        "totalEncontrados":  len(priced),
        "query":             params["q"],
    }
