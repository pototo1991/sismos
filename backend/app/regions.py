"""Mapeo de continentes y regiones para clasificación y filtrado de sismos."""
import re
import time
from typing import Any
from psycopg import sql

# Códigos de 2 letras de estados/territorios para coincidencia exacta
STATE_CODES = {
    'AK', 'AL', 'AR', 'AZ', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA', 'HI', 'IA', 'ID',
    'IL', 'IN', 'KS', 'KY', 'LA', 'MA', 'MD', 'ME', 'MI', 'MN', 'MO', 'MS', 'MT',
    'NC', 'ND', 'NE', 'NH', 'NJ', 'NM', 'NV', 'NY', 'OH', 'OK', 'OR', 'PA', 'RI',
    'SC', 'SD', 'TN', 'TX', 'UT', 'VA', 'VT', 'WA', 'WI', 'WV', 'WY', 'MX'
}

# Configuración de continentes con centros y niveles de zoom iniciales para el mapa.
CONTINENTES_CONFIG: dict[str, dict[str, Any]] = {
    "america-del-sur": {
        "nombre": "América del Sur",
        "center": [-20.0, -65.0],
        "zoom": 4,
        "keywords": [
            "Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Ecuador", "Falkland",
            "French Guiana", "Guyana", "Paraguay", "Peru", "Suriname", "Uruguay", "Venezuela",
        ],
    },
    "america-central-caribe": {
        "nombre": "América Central y Caribe",
        "center": [15.0, -75.0],
        "zoom": 5,
        "keywords": [
            "Belize", "Costa Rica", "El Salvador", "Guatemala", "Honduras", "Nicaragua", "Panama",
            "Anguilla", "Antigua and Barbuda", "Aruba", "Bahamas", "Barbados", "Bonaire",
            "British Virgin Islands", "Cayman Islands", "Cuba", "Curacao", "Dominica",
            "Dominican Republic", "Grenada", "Guadeloupe", "Haiti", "Jamaica", "Martinique",
            "Montserrat", "Puerto Rico", "Saint Barthelemy", "Saint Kitts and Nevis",
            "Saint Lucia", "Saint Martin", "Saint Vincent", "Sint Maarten", "Trinidad and Tobago",
            "Turks and Caicos", "U.S. Virgin Islands", "Saint Eustatius and Saba", "Bermuda",
        ],
    },
    "america-del-norte": {
        "nombre": "América del Norte",
        "center": [42.0, -100.0],
        "zoom": 3,
        "keywords": [
            "Canada", "Greenland", "Mexico", "Saint Pierre and Miquelon", "United States",
            "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
            "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
            "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
            "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
            "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio",
            "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
            "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
            "Wisconsin", "Wyoming", "AK", "AZ", "CA", "CO", "FL", "HI", "ID", "IL", "IN", "KS",
            "KY", "LA", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ",
            "NM", "NV", "NY", "OH", "OK", "OR", "PA", "SC", "SD", "TN", "TX", "UT", "VA", "VT",
            "WA", "WI", "WV", "WY", "MX", "Revilla Gigedo",
        ],
    },
    "europa": {
        "nombre": "Europa",
        "center": [50.0, 15.0],
        "zoom": 4,
        "keywords": [
            "Albania", "Andorra", "Austria", "Belarus", "Belgium", "Bosnia and Herzegovina",
            "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland",
            "France", "Germany", "Greece", "Hungary", "Iceland", "Ireland", "Italy", "Jersey",
            "Kosovo", "Latvia", "Liechtenstein", "Lithuania", "Luxembourg", "Malta", "Moldova",
            "Monaco", "Montenegro", "Netherlands", "North Macedonia", "Norway", "Poland",
            "Portugal", "Romania", "San Marino", "Serbia", "Slovakia", "Slovenia", "Spain",
            "Svalbard and Jan Mayen", "Sweden", "Switzerland", "Ukraine", "United Kingdom", "Vatican",
        ],
    },
    "asia": {
        "nombre": "Asia",
        "center": [32.0, 95.0],
        "zoom": 3,
        "keywords": [
            "Afghanistan", "Armenia", "Azerbaijan", "Bahrain", "Bangladesh", "Bhutan", "Brunei",
            "Burma (Myanmar)", "Burma", "Cambodia", "China", "Georgia", "Hong Kong", "India",
            "Indonesia", "Iran", "Iraq", "Israel", "Japan", "Jordan", "Kazakhstan", "Kuwait",
            "Kyrgyzstan", "Laos", "Lebanon", "Macau", "Malaysia", "Maldives", "Mongolia", "Myanmar",
            "Nepal", "North Korea", "Oman", "Pakistan", "Palestinian Territory", "Philippines",
            "Qatar", "Russia", "Saudi Arabia", "Singapore", "South Korea", "Sri Lanka", "Syria",
            "Taiwan", "Tajikistan", "Thailand", "Timor Leste", "Turkey", "Turkmenistan",
            "United Arab Emirates", "Uzbekistan", "Vietnam", "Yemen", "Kahramanmaras earthquake sequence",
        ],
    },
    "africa": {
        "nombre": "África",
        "center": [2.0, 20.0],
        "zoom": 3,
        "keywords": [
            "Algeria", "Angola", "Benin", "Botswana", "Burkina Faso", "Burundi", "Cabo Verde",
            "Cameroon", "Central African Republic", "Chad", "Comoros", "Congo-Uganda", "Congo",
            "Cote d'Ivoire", "Democratic Republic of the Congo", "Dem. Republic of the Congo-Tanzania",
            "Djibouti", "Egypt", "Equatorial Guinea", "Eritrea", "Eswatini", "Ethiopia", "Gabon",
            "Gambia", "Ghana", "Guinea", "Kenya", "Lesotho", "Liberia", "Libya", "Madagascar",
            "Malawi", "Mali", "Mauritania", "Mauritius", "Mayotte", "Morocco", "Mozambique",
            "Namibia", "Niger", "Nigeria", "Republic of the Congo", "Reunion", "Rwanda",
            "Saint Helena", "Sao Tome and Principe", "Senegal", "Seychelles", "Sierra Leone",
            "Somalia", "South Africa", "South Sudan", "Sudan", "Tanzania", "Togo", "Tunisia",
            "Uganda", "Zambia", "Zimbabwe",
        ],
    },
    "oceania": {
        "nombre": "Oceanía",
        "center": [-22.0, 150.0],
        "zoom": 3,
        "keywords": [
            "American Samoa", "Australia", "Cook Islands", "Fiji", "French Polynesia", "Guam",
            "Kiribati region", "Marshall Islands", "Micronesia", "Federated States of Micronesia",
            "Nauru", "New Caledonia", "New Zealand", "Niue", "Norfolk Island",
            "Northern Mariana Islands", "Palau", "Papua New Guinea", "Pitcairn", "Samoa",
            "Solomon Islands", "Tokelau", "Tonga", "Tuvalu", "Vanuatu", "Wallis and Futuna",
        ],
    },
    "antartida-oceanos": {
        "nombre": "Antártida y Océanos",
        "center": [0.0, 0.0],
        "zoom": 2,
        "keywords": [
            "Antarctica", "French Southern Territories", "Región no determinada", "earthquake",
        ],
    },
}


def classify_country(country_name: str | None) -> str:
    """Clasifica un nombre de país inferido en un identificador de continente."""
    if not country_name:
        return "antartida-oceanos"
    c_clean = country_name.strip()
    for cont_id, data in CONTINENTES_CONFIG.items():
        for kw in data["keywords"]:
            if kw in STATE_CODES:
                if c_clean.upper() == kw or c_clean.upper().startswith(kw + " "):
                    return cont_id
            else:
                pattern = r"\b" + re.escape(kw) + r"\b"
                if re.search(pattern, c_clean, re.IGNORECASE):
                    return cont_id
    return "antartida-oceanos"


_cache_geo: dict[str, Any] | None = None
_cache_time: float = 0.0
_CACHE_TTL_SECONDS: float = 3600.0  # 1 hora


REGIONES_POR_PAIS: dict[str, list[dict[str, str]]] = {
    "Chile": [
        {"id": "Arica", "nombre": "Arica y Parinacota"},
        {"id": "Tarapacá", "nombre": "Tarapacá / Iquique"},
        {"id": "Antofagasta", "nombre": "Antofagasta"},
        {"id": "Calama", "nombre": "Calama"},
        {"id": "Atacama", "nombre": "Atacama / Copiapó"},
        {"id": "Vallenar", "nombre": "Vallenar"},
        {"id": "Coquimbo", "nombre": "Coquimbo / La Serena"},
        {"id": "Valparaíso", "nombre": "Valparaíso / Viña del Mar"},
        {"id": "Santiago", "nombre": "Metropolitana / Santiago"},
        {"id": "O'Higgins", "nombre": "O'Higgins / Rancagua"},
        {"id": "Maule", "nombre": "Maule / Talca"},
        {"id": "Ñuble", "nombre": "Ñuble / Chillán"},
        {"id": "Bio-Bio", "nombre": "Biobío / Concepción"},
        {"id": "Araucanía", "nombre": "Araucanía / Temuco"},
        {"id": "Valdivia", "nombre": "Los Ríos / Valdivia"},
        {"id": "Puerto Montt", "nombre": "Los Lagos / Puerto Montt"},
        {"id": "Aysén", "nombre": "Aysén"},
        {"id": "Magallanes", "nombre": "Magallanes / Punta Arenas"},
    ],
}


def get_geo_filters(db_module) -> dict[str, Any]:
    """Retorna la estructura jerárquica de continentes y países con caché."""
    global _cache_geo, _cache_time
    now = time.time()
    if _cache_geo is not None and (now - _cache_time) < _CACHE_TTL_SECONDS:
        return _cache_geo

    query = sql.SQL(
        """
        SELECT pais_inferido, COUNT(*) AS count
        FROM {tabla}
        WHERE pais_inferido IS NOT NULL AND TRIM(pais_inferido) != ''
        GROUP BY pais_inferido
        ORDER BY pais_inferido ASC
        """
    ).format(tabla=db_module.SISMOS_TABLE)

    rows = db_module.fetch_all(query)

    continentes = {
        cid: {
            "id": cid,
            "nombre": cdata["nombre"],
            "center": cdata["center"],
            "zoom": cdata["zoom"],
            "paises": [],
        }
        for cid, cdata in CONTINENTES_CONFIG.items()
    }

    for r in rows:
        p = r["pais_inferido"]
        c = r["count"]
        cid = classify_country(p)
        continentes[cid]["paises"].append({
            "id": p,
            "nombre": p,
            "eventos": c,
        })

    result = {
        "continentes": list(continentes.values()),
        "subregiones": REGIONES_POR_PAIS,
    }
    _cache_geo = result
    _cache_time = now
    return result


def get_paises_para_continente(continente_id: str, db_module) -> list[str]:
    """Retorna la lista de nombres de países correspondientes a un continente."""
    geo = get_geo_filters(db_module)
    for c in geo["continentes"]:
        if c["id"] == continente_id:
            return [p["id"] for p in c["paises"]]
    return []
