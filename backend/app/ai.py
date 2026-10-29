"""Módulo de Inteligencia Artificial Local (Ollama en Knossos / Local).

Proporciona:
1. Resumen / Boletín sísmico inteligente redactado en lenguaje natural (Llama 3.2 / Llama 3.1).
2. Extracción semántica de filtros a partir de consultas en lenguaje natural (Qwen 2.5 / JSON).
3. Degradación elegante (graceful degradation) y caché en memoria para no saturar Ollama.
"""
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("sismos.ai")

# ---- Configuración ----
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.100.16:11434").rstrip("/")
OLLAMA_MODEL_PARSER = os.getenv("OLLAMA_MODEL_PARSER", "qwen2.5:1.5b")
OLLAMA_MODEL_SUMMARY = os.getenv("OLLAMA_MODEL_SUMMARY", "llama3.2:1b")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "25.0"))

# Caché en memoria: boletines, enjambres y respuestas
_BOLETIN_CACHE: dict[str, dict[str, Any]] = {}
_MAX_CACHE_ENTRIES = 500
_SWARM_CACHE: dict[str, Any] = {"data": None, "timestamp": 0.0}
_SWARM_CACHE_TTL = 90.0  # 90 segundos de validez para análisis de enjambres


def _call_ollama_generate(model: str, prompt: str, system: str | None = None, format_json: bool = False) -> str | None:
    """Envía una petición síncrona a la API /api/generate de Ollama."""
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2 if format_json else 0.4,
            "num_predict": 180,
        },
    }
    if system:
        payload["system"] = system
    if format_json:
        payload["format"] = "json"

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("response", "").strip()
            logger.warning("Ollama respondió con status %s", resp.status)
            return None
    except urllib.error.URLError as e:
        logger.warning("No se pudo conectar a Ollama en %s: %s", url, e.reason)
        return None
    except Exception as e:
        logger.warning("Error consultando Ollama: %s", e)
        return None


def check_ollama_status() -> dict[str, Any]:
    """Verifica si Ollama está disponible y qué modelos están instalados."""
    url = f"{OLLAMA_BASE_URL}/api/tags"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                model_names = [m.get("name") for m in body.get("models", [])]
                return {
                    "online": True,
                    "base_url": OLLAMA_BASE_URL,
                    "modelos_disponibles": model_names,
                    "modelo_resumen": OLLAMA_MODEL_SUMMARY,
                    "modelo_parser": OLLAMA_MODEL_PARSER,
                }
    except Exception as e:
        logger.debug("Ollama offline: %s", e)

    return {
        "online": False,
        "base_url": OLLAMA_BASE_URL,
        "modelos_disponibles": [],
        "modelo_resumen": OLLAMA_MODEL_SUMMARY,
        "modelo_parser": OLLAMA_MODEL_PARSER,
    }


def _boletin_fallback(sismo: dict[str, Any]) -> str:
    """Genera un reporte informativo estructurado cuando Ollama no está disponible."""
    mag = sismo.get("magnitud") or 0.0
    prof = sismo.get("profundidad_km") or 0.0
    lugar = sismo.get("lugar") or "Localización no especificada"
    pais = sismo.get("pais_inferido") or ""
    alerta_tsunami = bool(sismo.get("alerta_tsunami"))

    if prof < 60:
        tipo_prof = "superficial"
    elif prof < 300:
        tipo_prof = "de profundidad intermedia"
    else:
        tipo_prof = "profundo"

    if mag >= 7.0:
        severidad = "terremoto de gran magnitud"
    elif mag >= 5.5:
        severidad = "sismo de moderada a fuerte intensidad"
    elif mag >= 4.0:
        severidad = "sismo de magnitud moderada"
    else:
        severidad = "evento sísmico menor"

    tsunami_txt = " Las características del sismo ameritan precaución." if alerta_tsunami else " No reúne condiciones para generar alerta de tsunami."

    ubicacion = f"{lugar}, {pais}".strip(", ")
    return f"Se registró un {severidad} (M {mag:.1f}) a {prof:.0f} km de profundidad ({tipo_prof}), con referencia en {ubicacion}.{tsunami_txt}"


def generar_boletin_sismo(sismo: dict[str, Any]) -> dict[str, Any]:
    """Genera un boletín informativo breve en español para un sismo específico."""
    sismo_id = str(sismo.get("id") or "")
    if sismo_id and sismo_id in _BOLETIN_CACHE:
        return {
            "sismo_id": sismo_id,
            "boletin": _BOLETIN_CACHE[sismo_id]["boletin"],
            "fuente": "cache",
            "modelo": OLLAMA_MODEL_SUMMARY,
        }

    mag = sismo.get("magnitud")
    tipo_mag = sismo.get("tipo_magnitud") or "M"
    prof = sismo.get("profundidad_km")
    lugar = sismo.get("lugar") or "desconocido"
    pais = sismo.get("pais_inferido") or ""
    fecha = str(sismo.get("fecha_hora_utc") or "")
    tsunami = bool(sismo.get("alerta_tsunami"))

    # Clasificación técnica de severidad para orientar al modelo
    if mag is not None:
        if mag < 4.0:
            escala_desc = "sismo menor / instrumental de baja energía (habitual, sin daño)"
        elif mag < 5.5:
            escala_desc = "sismo de magnitud moderada"
        elif mag < 7.0:
            escala_desc = "sismo fuerte"
        else:
            escala_desc = "terremoto mayor"
    else:
        escala_desc = "evento sísmico"

    system_prompt = (
        "Eres un sismólogo profesional y comunicador científico en español formal. "
        "Tu objetivo es redactar un boletín informativo muy conciso en un único párrafo de 2 a 3 oraciones (máximo 60 palabras). "
        "Reglas estrictas:\n"
        "- Sé riguroso y proporcional: NUNCA califiques de gran terremoto o peligrosa una magnitud baja (< 4.0).\n"
        "- Menciona la magnitud, profundidad (superficial <60 km, intermedia 60-300 km o profunda >300 km) y ubicación.\n"
        "- Confirma o descarta riesgo de tsunami según el dato provisto.\n"
        "- No uses títulos, viñetas, saludos ni despedidas. Solo el párrafo informativo directo."
    )

    user_prompt = (
        f"Datos del sismo:\n"
        f"- Magnitud: {mag} ({tipo_mag}) -> Clasificación técnica: {escala_desc}\n"
        f"- Profundidad: {prof} km\n"
        f"- Ubicación: {lugar} ({pais})\n"
        f"- Fecha/hora UTC: {fecha}\n"
        f"- Alerta de tsunami reportada: {'SÍ' if tsunami else 'NO'}\n\n"
        f"Redacta el párrafo del boletín sismológico en español:"
    )

    respuesta_ia = _call_ollama_generate(
        model=OLLAMA_MODEL_SUMMARY,
        prompt=user_prompt,
        system=system_prompt,
        format_json=False,
    )

    if respuesta_ia and len(respuesta_ia) > 20:
        # Limpieza básica
        boletin = respuesta_ia.strip('"\n ')
        fuente = "ia"
    else:
        boletin = _boletin_fallback(sismo)
        fuente = "fallback"

    # Guardar en caché si hay id
    if sismo_id:
        if len(_BOLETIN_CACHE) >= _MAX_CACHE_ENTRIES:
            # Eliminar la mitad más antigua
            keys_to_del = list(_BOLETIN_CACHE.keys())[: _MAX_CACHE_ENTRIES // 2]
            for k in keys_to_del:
                _BOLETIN_CACHE.pop(k, None)
        _BOLETIN_CACHE[sismo_id] = {
            "boletin": boletin,
            "timestamp": datetime.now(timezone.utc).timestamp(),
        }

    return {
        "sismo_id": sismo_id,
        "boletin": boletin,
        "fuente": fuente,
        "modelo": OLLAMA_MODEL_SUMMARY,
    }


MAPA_CONTINENTES: dict[str, str] = {
    "asia": "asia",
    "europa": "europa",
    "europe": "europa",
    "america-del-sur": "america-del-sur",
    "america del sur": "america-del-sur",
    "américa del sur": "america-del-sur",
    "sudamerica": "america-del-sur",
    "sudamérica": "america-del-sur",
    "suramerica": "america-del-sur",
    "suramérica": "america-del-sur",
    "america-central-caribe": "america-central-caribe",
    "america central": "america-central-caribe",
    "américa central": "america-central-caribe",
    "centroamerica": "america-central-caribe",
    "centroamérica": "america-central-caribe",
    "caribe": "america-central-caribe",
    "america-del-norte": "america-del-norte",
    "america del norte": "america-del-norte",
    "américa del norte": "america-del-norte",
    "norteamerica": "america-del-norte",
    "norteamérica": "america-del-norte",
    "africa": "africa",
    "áfrica": "africa",
    "oceania": "oceania",
    "oceanía": "oceania",
    "antartida": "antartida-oceanos",
    "antártida": "antartida-oceanos",
}


def _extraer_filtros_fallback(query: str) -> dict[str, Any]:
    """Extracción por expresiones regulares si Ollama no está disponible."""
    q = query.lower()
    filtros: dict[str, Any] = {
        "continente": None,
        "pais": None,
        "mag_min": None,
        "mag_max": None,
        "dias": None,
        "prof_min": None,
        "prof_max": None,
        "region": None,
    }

    # Continentes
    for k_cont, cid in MAPA_CONTINENTES.items():
        if re.search(r"\b" + re.escape(k_cont) + r"\b", q):
            filtros["continente"] = cid
            break

    # Países comunes
    paises_comunes = ["chile", "argentina", "peru", "perú", "japon", "japón", "mexico", "méxico", "haiti", "haití", "indonesia", "estados unidos", "italia"]
    for p in paises_comunes:
        if p in q:
            # Capitalizar
            filtros["pais"] = p.replace("peru", "Perú").replace("japon", "Japón").replace("mexico", "México").replace("haiti", "Haití").title()
            break

    # Detección de días / tiempo
    m_dias = re.search(r"(\d+)\s*(?:d[ií]as?|semanas?|horas?)", q)
    if m_dias:
        val = int(m_dias.group(1))
        if "semana" in q:
            filtros["dias"] = val * 7
        elif "hora" in q:
            filtros["dias"] = max(1, val // 24)
        else:
            filtros["dias"] = val
    elif "hoy" in q:
        filtros["dias"] = 1
    elif "semana" in q:
        filtros["dias"] = 7
    elif "mes" in q:
        filtros["dias"] = 30

    # Detección de magnitud mayor que
    m_mag = re.search(r"(?:mayor(?:es)?\s*(?:a|que)?|>\s*|mag(?:nitud)?\s*(?:de|>=|>)?)\s*(\d+(?:[.,]\d+)?)", q)
    if m_mag:
        try:
            filtros["mag_min"] = float(m_mag.group(1).replace(",", "."))
        except ValueError:
            pass

    # Detección de sismo fuerte / terremoto
    if "terremoto" in q and filtros["mag_min"] is None:
        filtros["mag_min"] = 6.0
    elif "fuerte" in q and filtros["mag_min"] is None:
        filtros["mag_min"] = 5.0

    return filtros


def interpretar_busqueda_nl(query_texto: str) -> dict[str, Any]:
    """Extrae parámetros estructurados de búsqueda a partir de una frase en lenguaje natural."""
    texto = query_texto.strip()
    if not texto:
        return {"filtros": {}, "interpretacion": "Consulta vacía", "fuente": "fallback"}

    system_prompt = (
        "Eres un analizador semántico de consultas de sismos y geología para una aplicación interactiva de mapas. "
        "Debes extraer los criterios de búsqueda solicitados por el usuario y responder ÚNICAMENTE un objeto JSON válido con los siguientes campos:\n"
        "- continente (string o null, identificador de continente: 'asia', 'europa', 'america-del-sur', 'america-del-norte', 'america-central-caribe', 'africa', 'oceania')\n"
        "- pais (string o null, nombre de país específico ej: 'Chile', 'Japón', 'Perú', 'Indonesia')\n"
        "- region (string o null, nombre de ciudad o región mencionada, ej: 'Coquimbo', 'Valparaíso')\n"
        "- mag_min (float o null, ej: 5.0)\n"
        "- mag_max (float o null)\n"
        "- dias (integer o null, cantidad de días hacia atrás a buscar, ej: 7 para una semana, 1 para hoy/últimas 24h)\n"
        "- prof_min (float o null)\n"
        "- prof_max (float o null, ej: 60 para sismos superficiales)\n"
        "- interpretacion (string breve, claro y natural en español describiendo los filtros que se aplicarán al mapa, ej: 'Sismos en Asia').\n"
        "REGLAS CRÍTICAS:\n"
        "1. Continentes como Asia, Europa, África, Oceanía, América del Sur o Norteamérica van en 'continente' (uno de los identificadores válidos), NUNCA en 'pais'.\n"
        "2. NUNCA digas 'magnitud desconocida' ni menciones campos que el usuario no especificó. Si el usuario no especificó magnitud, simplemente omite la magnitud o indica 'todas las magnitudes'."
    )

    user_prompt = f"Consulta del usuario: \"{texto}\"\nDevuelve solo el JSON."

    resp = _call_ollama_generate(
        model=OLLAMA_MODEL_PARSER,
        prompt=user_prompt,
        system=system_prompt,
        format_json=True,
    )

    filtros = None
    interpretacion = None
    fuente = "ia"

    if resp:
        try:
            # Intentar decodificar JSON
            # Buscar el bloque {...}
            match = re.search(r"\{.*\}", resp, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                cont_val = parsed.get("continente")
                pais_val = parsed.get("pais")

                # Si el modelo puso un continente en el campo pais (ej: "Asia")
                if pais_val and isinstance(pais_val, str):
                    p_lower = pais_val.strip().lower()
                    if p_lower in MAPA_CONTINENTES:
                        cont_val = MAPA_CONTINENTES[p_lower]
                        pais_val = None

                # Normalizar continente si existe
                if cont_val and isinstance(cont_val, str):
                    c_lower = cont_val.strip().lower()
                    cont_val = MAPA_CONTINENTES.get(c_lower, cont_val)

                filtros = {
                    "continente": cont_val,
                    "pais": pais_val,
                    "region": parsed.get("region"),
                    "mag_min": float(parsed["mag_min"]) if parsed.get("mag_min") is not None else None,
                    "mag_max": float(parsed["mag_max"]) if parsed.get("mag_max") is not None else None,
                    "dias": int(parsed["dias"]) if parsed.get("dias") is not None else None,
                    "prof_min": float(parsed["prof_min"]) if parsed.get("prof_min") is not None else None,
                    "prof_max": float(parsed["prof_max"]) if parsed.get("prof_max") is not None else None,
                }
                raw_interp = parsed.get("interpretacion")
                if raw_interp and isinstance(raw_interp, str):
                    # Limpiar frases donde el modelo interpreta mag_min=null como "magnitud desconocida"
                    cleaned = re.sub(
                        r"(?i)\s*(?:con|de|y)?\s*(?:una\s*)?magnitud\s+desconocida",
                        "",
                        raw_interp,
                    ).strip(" ,.-")
                    interpretacion = cleaned if cleaned else raw_interp
        except Exception as e:
            logger.warning("Fallo al parsear JSON devuelto por Ollama: %s | Resp: %s", e, resp)

    if not filtros:
        filtros = _extraer_filtros_fallback(texto)
        fuente = "fallback"
        partes = []
        if filtros.get("continente"):
            partes.append(f"Continente: {filtros['continente']}")
        if filtros.get("pais"):
            partes.append(f"País: {filtros['pais']}")
        if filtros.get("mag_min"):
            partes.append(f"Mag ≥ {filtros['mag_min']}")
        if filtros.get("dias"):
            partes.append(f"Últimos {filtros['dias']} días")
        interpretacion = f"Búsqueda por regla: {', '.join(partes)}" if partes else "Búsqueda general"

    return {
        "consulta_original": texto,
        "filtros": filtros,
        "interpretacion": interpretacion or "Criterios extraídos",
        "fuente": fuente,
        "modelo": OLLAMA_MODEL_PARSER if fuente == "ia" else None,
    }


# ==============================================================================
# 1. Estimación de Intensidad Percibida (Escala Mercalli Modificada - MMI / ShakeMap)
# ==============================================================================

# Grados Mercalli: número romano, descripción cualitativa y paleta oficial USGS ShakeMap
MMI_ESCALA = {
    1: {"grado": "I", "desc": "No sentido / Instrumental", "color": "#7bacd6"},
    2: {"grado": "II", "desc": "Débil (sentido en reposo)", "color": "#81c784"},
    3: {"grado": "III", "desc": "Leve (sentido en interiores)", "color": "#4caf50"},
    4: {"grado": "IV", "desc": "Moderado (objetos colgantes oscilan)", "color": "#2e7d32"},
    5: {"grado": "V", "desc": "Poco Fuerte (sentido por la mayoría)", "color": "#fbc02d"},
    6: {"grado": "VI", "desc": "Fuerte (muebles desplazados, alarma)", "color": "#f57c00"},
    7: {"grado": "VII", "desc": "Muy Fuerte (daños menores en estructuras)", "color": "#e64a19"},
    8: {"grado": "VIII", "desc": "Destructivo (daños considerables)", "color": "#d32f2f"},
    9: {"grado": "IX", "desc": "Ruinoso (pánico general, colapsos)", "color": "#c2185b"},
    10: {"grado": "X+", "desc": "Desastroso (destrucción masiva)", "color": "#7b1fa2"},
}


def _mmi_a_grado(valor: float) -> dict[str, Any]:
    """Convierte un valor numérico continuo de MMI en grado romano y descripción."""
    idx = max(1, min(10, int(round(valor))))
    info = MMI_ESCALA[idx]
    return {
        "valor": round(valor, 1),
        "grado": info["grado"],
        "desc": info["desc"],
        "color": info["color"],
    }


def calcular_mmi_atenuacion(magnitud: float, profundidad_km: float, distancia_km: float) -> float:
    """Calcula la intensidad Mercalli Modificada continua según modelo empírico GMPE de atenuación.

    Modelo logarítmico calibrado para ambientes de subducción y corticales:
    MMI = c1 + c2*M - c3*log10(R_hypo) - c4*R_hypo
    Donde R_hypo = sqrt(distancia^2 + max(profundidad, 5)^2).
    """
    if magnitud <= 0:
        return 1.0

    prof_efectiva = max(float(profundidad_km or 10.0), 5.0)
    dist = max(float(distancia_km or 0.0), 0.0)
    r_hypo = math.sqrt(dist * dist + prof_efectiva * prof_efectiva)

    # Coeficientes empíricos sismológicos
    c1 = 1.15
    c2 = 1.50
    c3 = 2.45
    c4 = 0.0012

    mmi = c1 + (c2 * magnitud) - (c3 * math.log10(r_hypo)) - (c4 * r_hypo)
    return max(1.0, min(10.0, mmi))


def estimar_intensidad_mercalli(sismo: dict[str, Any]) -> dict[str, Any]:
    """Estima la intensidad Mercalli probable (ShakeMap) en el epicentro y distancias clave."""
    mag = float(sismo.get("magnitud") or 0.0)
    prof = float(sismo.get("profundidad_km") or 10.0)
    lugar = sismo.get("lugar") or sismo.get("referencia") or "Epicentro"

    # Cálculo en epicentro (distancia = 0)
    mmi_epicentro_val = calcular_mmi_atenuacion(mag, prof, 0.0)
    epicentro_info = _mmi_a_grado(mmi_epicentro_val)

    # Estimaciones a distancias de referencia estándar
    distancias_ref = [
        {"distancia_km": 0, "etiqueta": "Zona epicentral"},
        {"distancia_km": 25, "etiqueta": "Cercanías inmediatas (25 km)"},
        {"distancia_km": 50, "etiqueta": "Radio intermedio (50 km)"},
        {"distancia_km": 100, "etiqueta": "Valle / Ciudades (100 km)"},
        {"distancia_km": 200, "etiqueta": "Zona periférica (200 km)"},
    ]

    puntos = []
    for d in distancias_ref:
        val = calcular_mmi_atenuacion(mag, prof, d["distancia_km"])
        info = _mmi_a_grado(val)
        puntos.append({
            "distancia_km": d["distancia_km"],
            "etiqueta": d["etiqueta"],
            "mmi": val,
            "grado": info["grado"],
            "desc": info["desc"],
            "color": info["color"],
        })

    # Radio estimado de percepción humana (MMI >= 2.5)
    radio_percepcion_km = 0
    if mmi_epicentro_val >= 2.5:
        # Búsqueda binaria simple del radio donde MMI cae a 2.5
        low, high = 0.0, 1000.0
        for _ in range(16):
            mid = (low + high) / 2.0
            if calcular_mmi_atenuacion(mag, prof, mid) >= 2.5:
                low = mid
            else:
                high = mid
        radio_percepcion_km = int(round(low))

    # Resumen amigable en lenguaje natural
    mmi_100k = puntos[3]  # a 100 km
    if mmi_epicentro_val >= 5.5:
        resumen = (
            f"Intensidad estimada: Grado {epicentro_info['grado']} ({epicentro_info['desc']} en epicentro) / "
            f"Grado {mmi_100k['grado']} ({mmi_100k['desc']} a 100 km)"
        )
    elif mmi_epicentro_val >= 3.5:
        resumen = (
            f"Intensidad estimada: Grado {epicentro_info['grado']} ({epicentro_info['desc']} en epicentro) / "
            f"Grado {mmi_100k['grado']} (Leve en valle central)"
        )
    else:
        resumen = f"Intensidad estimada: Grado {epicentro_info['grado']} ({epicentro_info['desc']}, apenas perceptible)"

    return {
        "sismo_id": str(sismo.get("id") or ""),
        "magnitud": mag,
        "profundidad_km": prof,
        "lugar": lugar,
        "mmi_epicentro_val": round(mmi_epicentro_val, 1),
        "mmi_epicentro_grado": epicentro_info["grado"],
        "mmi_epicentro_desc": epicentro_info["desc"],
        "mmi_epicentro_color": epicentro_info["color"],
        "radio_percepcion_km": radio_percepcion_km,
        "resumen_amigable": resumen,
        "curva_atenuacion": puntos,
    }


# ==============================================================================
# 2. Detección Asistida de Enjambres Sísmicos y Clasificación de Réplicas
# ==============================================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia ortodrómica en kilómetros entre dos coordenadas geográficas."""
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return R * c


def _parse_utc_timestamp(val: Any) -> float:
    """Convierte fecha ISO o timestamp a epoch en segundos."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, datetime):
        return val.timestamp()
    if isinstance(val, str):
        try:
            # Reemplazar Z por +00:00 para ISO 8601
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            pass
    return 0.0


def analizar_enjambres_y_replicas(sismos: list[dict[str, Any]]) -> dict[str, Any]:
    """Detecta enjambres sísmicos (swarms) y agrupa réplicas vinculadas a sismos principales."""
    ahora_ts = datetime.now(timezone.utc).timestamp()

    # Verificar caché en memoria
    if _SWARM_CACHE["data"] and (ahora_ts - _SWARM_CACHE["timestamp"]) < _SWARM_CACHE_TTL:
        return _SWARM_CACHE["data"]

    # Preparar eventos normalizados
    eventos = []
    for s in sismos:
        sid = str(s.get("id") or "")
        lat = s.get("lat") or (s.get("geometry", {}).get("coordinates", [None, None])[1] if "geometry" in s else None)
        lon = s.get("lon") or (s.get("geometry", {}).get("coordinates", [None, None])[0] if "geometry" in s else None)
        mag = s.get("magnitud") or (s.get("properties", {}).get("magnitud") if "properties" in s else None)
        fecha_val = s.get("fecha_utc") or s.get("fecha_hora_utc") or (s.get("properties", {}).get("fecha_utc") if "properties" in s else None)
        ref = s.get("referencia") or s.get("lugar") or (s.get("properties", {}).get("referencia") if "properties" in s else "")

        if sid and lat is not None and lon is not None and mag is not None:
            ts = _parse_utc_timestamp(fecha_val)
            eventos.append({
                "id": sid,
                "lat": float(lat),
                "lon": float(lon),
                "mag": float(mag),
                "ts": ts,
                "ref": str(ref or ""),
            })

    if not eventos:
        resultado_vacio = {
            "enjambres_activos": [],
            "enjambres_recientes": [],
            "secuencias_replicas": [],
            "alerta_banner": None,
            "mapa_roles": {},
            "total_enjambres": 0,
            "timestamp": ahora_ts,
        }
        _SWARM_CACHE["data"] = resultado_vacio
        _SWARM_CACHE["timestamp"] = ahora_ts
        return resultado_vacio

    # Ordenar cronológicamente (más antiguo al más reciente)
    eventos.sort(key=lambda x: x["ts"])

    mapa_roles: dict[str, dict[str, Any]] = {}
    secuencias_replicas: list[dict[str, Any]] = []
    ids_en_replicas: set[str] = set()

    # --- PASO 1: Identificación de Sismos Principales (Mainshocks) y Réplicas (Aftershocks) ---
    candidatos_mainshock = [e for e in eventos if e["mag"] >= 4.5]
    candidatos_mainshock.sort(key=lambda x: x["mag"], reverse=True)

    for m in candidatos_mainshock:
        if m["id"] in ids_en_replicas:
            continue

        # Radio de ruptura de Båth/Gardner-Knopoff en km
        r_rup = min(180.0, max(35.0, 10.0 ** (0.12 * m["mag"] + 0.45)))
        # Ventana de 7 días (604800 s) posteriores
        replicas_encontradas = []

        for e in eventos:
            if e["id"] == m["id"] or e["id"] in ids_en_replicas:
                continue
            delta_t = e["ts"] - m["ts"]
            if 0 < delta_t <= 604800:
                dist = _haversine_km(m["lat"], m["lon"], e["lat"], e["lon"])
                if dist <= r_rup and e["mag"] <= (m["mag"] - 0.2):
                    replicas_encontradas.append({
                        "id": e["id"],
                        "mag": e["mag"],
                        "ts": e["ts"],
                        "dist_km": round(dist, 1),
                        "delta_horas": round(delta_t / 3600.0, 1),
                        "ref": e["ref"],
                    })

        if len(replicas_encontradas) >= 2:
            secuencias_replicas.append({
                "mainshock_id": m["id"],
                "mainshock_mag": m["mag"],
                "mainshock_lat": m["lat"],
                "mainshock_lon": m["lon"],
                "mainshock_ref": m["ref"],
                "mainshock_ts": m["ts"],
                "total_replicas": len(replicas_encontradas),
                "replicas": replicas_encontradas,
            })
            mapa_roles[m["id"]] = {
                "tipo": "mainshock",
                "total_replicas": len(replicas_encontradas),
                "replicas_ids": [r["id"] for r in replicas_encontradas],
            }
            for r in replicas_encontradas:
                ids_en_replicas.add(r["id"])
                mapa_roles[r["id"]] = {
                    "tipo": "replica",
                    "mainshock_id": m["id"],
                    "mainshock_mag": m["mag"],
                    "dist_km": r["dist_km"],
                    "delta_horas": r["delta_horas"],
                }

    # --- PASO 2: Detección de Enjambres Sísmicos (Swarms) ---
    # Consideramos eventos de los últimos 4 días no asignados a réplicas
    ventana_enjambre_ts = ahora_ts - (4 * 86400)
    eventos_enjambre = [e for e in eventos if e["ts"] >= ventana_enjambre_ts and e["id"] not in ids_en_replicas]

    grupos_enjambre: list[list[dict[str, Any]]] = []
    visitados = set()

    for i, e1 in enumerate(eventos_enjambre):
        if e1["id"] in visitados:
            continue
        cluster = [e1]
        visitados.add(e1["id"])

        for j, e2 in enumerate(eventos_enjambre):
            if i != j and e2["id"] not in visitados:
                # Comprobar distancia con cualquier miembro del cluster (enlace simple <= 40 km)
                for c in cluster:
                    if _haversine_km(c["lat"], c["lon"], e2["lat"], e2["lon"]) <= 40.0:
                        cluster.append(e2)
                        visitados.add(e2["id"])
                        break

        # Criterio sismológico de enjambre: al menos 3 sismos agrupados
        if len(cluster) >= 3:
            mags = sorted([c["mag"] for c in cluster], reverse=True)
            # Un enjambre genuino no tiene un sismo dominante único (diferencia entre el 1° y 2° < 0.8)
            diferencia_top = (mags[0] - mags[1]) if len(mags) > 1 else 0.0
            if diferencia_top <= 0.8:
                grupos_enjambre.append(cluster)

    enjambres_activos = []
    enjambres_recientes = []

    for idx, grupo in enumerate(grupos_enjambre):
        c_lat = sum(g["lat"] for g in grupo) / len(grupo)
        c_lon = sum(g["lon"] for g in grupo) / len(grupo)
        max_dist = max(_haversine_km(c_lat, c_lon, g["lat"], g["lon"]) for g in grupo)
        ts_min = min(g["ts"] for g in grupo)
        ts_max = max(g["ts"] for g in grupo)
        duracion_h = max(0.5, (ts_max - ts_min) / 3600.0)
        es_activo = (ahora_ts - ts_max) <= (24 * 3600)  # Actividad en las últimas 24h

        # Referencia geográfica representativa
        ref_comun = grupo[-1]["ref"] or "Región no especificada"
        for g in grupo:
            if g["ref"]:
                ref_comun = g["ref"]
                break

        mags = [g["mag"] for g in grupo]
        enjambre_id = f"swarm-{int(ts_min)}-{idx}"

        datos_enjambre = {
            "id": enjambre_id,
            "cantidad_eventos": len(grupo),
            "centroide": {"lat": round(c_lat, 4), "lon": round(c_lon, 4)},
            "radio_km": round(max_dist, 1),
            "zona": ref_comun,
            "mag_min": min(mags),
            "mag_max": max(mags),
            "duracion_horas": round(duracion_h, 1),
            "activo": es_activo,
            "eventos_ids": [g["id"] for g in grupo],
            "ultimo_evento_ts": ts_max,
        }

        # Asignar rol a cada evento del enjambre
        for g in grupo:
            mapa_roles[g["id"]] = {
                "tipo": "enjambre",
                "enjambre_id": enjambre_id,
                "total_enjambre": len(grupo),
                "zona": ref_comun,
            }

        if es_activo:
            enjambres_activos.append(datos_enjambre)
        else:
            enjambres_recientes.append(datos_enjambre)

    # Generar alerta visual para la cabecera si hay un enjambre activo
    alerta_banner = None
    if enjambres_activos:
        # Tomar el enjambre activo con más eventos o más reciente
        ea = sorted(enjambres_activos, key=lambda x: x["cantidad_eventos"], reverse=True)[0]
        horas_txt = f"{ea['duracion_horas']:.0f} horas" if ea['duracion_horas'] >= 1 else "últimas horas"
        alerta_banner = {
            "enjambre_id": ea["id"],
            "mensaje": f"⚠️ Enjambre sísmico en desarrollo: {ea['cantidad_eventos']} eventos en las últimas {horas_txt} en {ea['zona']}",
            "lat": ea["centroide"]["lat"],
            "lon": ea["centroide"]["lon"],
            "cantidad": ea["cantidad_eventos"],
            "mag_rango": f"M {ea['mag_min']:.1f} – {ea['mag_max']:.1f}",
        }

    resultado = {
        "enjambres_activos": enjambres_activos,
        "enjambres_recientes": enjambres_recientes,
        "secuencias_replicas": secuencias_replicas,
        "alerta_banner": alerta_banner,
        "mapa_roles": mapa_roles,
        "total_enjambres": len(enjambres_activos) + len(enjambres_recientes),
        "timestamp": ahora_ts,
    }

    _SWARM_CACHE["data"] = resultado
    _SWARM_CACHE["timestamp"] = ahora_ts
    return resultado


# ==============================================================================
# 3. Asistente Educativo y Preventivo ("Pregúntale al Sismólogo")
# ==============================================================================

# Base de conocimiento experta para respuesta sismológica inmediata
BASE_CONOCIMIENTO_SISMOLOGO = {
    "mochila": (
        "🎒 **Mochila de Emergencia (Recomendaciones SENAPRED / USGS):**\n"
        "Debe estar en un lugar de fácil acceso y contener provisiones para al menos 48 horas:\n"
        "• Agua embotellada (2 litros por persona al día).\n"
        "• Alimentos no perecibles (barras de cereal, conservas con abrelatas).\n"
        "• Linterna y radio a pilas o dinamo con pilas de repuesto.\n"
        "• Botiquín de primeros auxilios y medicamentos de uso crónico.\n"
        "• Silbato para pedir auxilio y copia de llaves de la vivienda.\n"
        "• Mascarillas, papel higiénico y alcohol gel.\n"
        "• Muda ligera de ropa y mantas térmicas."
    ),
    "edificio": (
        "🏢 **Qué hacer en un edificio o departamento durante un sismo:**\n"
        "1. **Agáchate, cúbrete y afírmate:** Ubícate bajo una mesa firme o junto a un pilar estructural resistente.\n"
        "2. **Aléjate de las ventanas:** El vidrio templado puede estallar por la flexión del edificio.\n"
        "3. **NUNCA uses el ascensor:** Pueden trabarse por corte eléctrico o deformación de rieles.\n"
        "4. **No bajes corriendo las escaleras:** Es la principal causa de tropiezos y fracturas durante la sacudida.\n"
        "5. **Finalizado el sismo:** Corta gas y electricidad si sospechas daños y evacua calmadamente por las escaleras."
    ),
    "tsunami": (
        "🌊 **Identificación y Prevención de Tsunami:**\n"
        "• **Regla de oro de autoevacuación:** Si estás en la costa y el sismo es tan fuerte que te dificulta mantenerte en pie, o dura más de un minuto, **evacua de inmediato** hacia zonas seguras sobre la cota 30 (30 metros sobre el nivel del mar).\n"
        "• **Señal natural:** Si observas un recogimiento anómalo del mar o sientes un fuerte estruendo marino, no esperes alertas oficiales por sirena ni mensajes SAE: sube a zonas altas de inmediato a pie."
    ),
    "cinturon": (
        "🌎 **El Cinturón de Fuego del Pacífico y la Subducción:**\n"
        "• El Cinturón de Fuego concentra el **80% de los mayores terremotos del planeta** debido al encuentro de placas tectónicas.\n"
        "• En la costa sudamericana (Chile, Perú, Ecuador), la densa **Placa de Nazca** se introduce bajo la **Placa Sudamericana** a unos 6.5 cm al año (subducción).\n"
        "• Este avance continuo traba las placas acumulando energía elástica durante décadas o siglos, hasta liberarse bruscamente en forma de grandes terremotos."
    ),
    "mercalli": (
        "📊 **Diferencia entre Magnitud e Intensidad:**\n"
        "• **Magnitud (Mw / Richter):** Mide la **energía liberada** en el hipocentro del sismo. Es un valor único e instrumental para todo el evento (ej. Mw 6.5).\n"
        "• **Intensidad Mercalli (MMI I al XII):** Mide la **percepción humana y los daños observados** en un lugar específico. Varía según la distancia: en el epicentro puede sentirse Grado VII (Muy Fuerte) y a 200 km Grado III (Leve)."
    ),
    "enjambre": (
        "⚠️ **¿Qué es un enjambre sísmico?**\n"
        "Es una serie de temblores concentrados en una misma zona geográfica durante horas, días o semanas, **sin que exista un terremoto principal claramente más grande**.\n"
        "Suelen deberse a reactivaciones de fallas geológicas secundarias locales o a migración de fluidos hidrotermales en la corteza. La mayoría se disipa de forma gradual a medida que la tensión se libera."
    ),
    "predecir": (
        "🔮 **¿Se pueden predecir los sismos?**\n"
        "**Científicamente NO es posible predecir** el día, la hora exacta ni la magnitud de un futuro sismo en ninguna parte del mundo.\n"
        "Lo que sí existe es la **estimación del peligro sísmico** (saber dónde hay lagunas sísmicas con energía acumulada) y los sistemas de **alerta temprana**, que detectan las ondas P (primeras en viajar) para emitir alertas segundos antes de que lleguen las destructivas ondas S a las ciudades."
    ),
    "san_ramon": (
        "🏔️ **Falla San Ramón (Santiago de Chile):**\n"
        "Es una falla geológica activa de tipo inversa ubicada en la precordillera oriental de Santiago (entre los ríos Mapocho y Maipo, cruzando Vitacura, Las Condes, La Reina, Peñalolén, La Florida y Puente Alto).\n"
        "Es capaz de generar sismos corticales muy superficiales (a menos de 10-15 km de profundidad), lo que provocaría aceleraciones locales intensas en la zona oriente."
    ),
}


def _tiene_palabra(palabras: list[str], texto: str) -> bool:
    for p in palabras:
        if re.search(r"\b" + re.escape(p) + r"\b", texto, re.IGNORECASE):
            return True
    return False


def _obtener_respuesta_fallback_sismologo(pregunta: str) -> str:
    """Selecciona la mejor respuesta pedagógica según palabras clave exactas de la consulta."""
    q = pregunta.lower().strip()

    # Saludo cordial
    if _tiene_palabra(["hola", "buenos dias", "buenas tardes", "buenas noches", "saludos", "que tal", "como estas", "cómo estás"], q):
        return (
            "🧑‍🔬 **¡Hola! Soy el Dr. Hugo Sísmico**, sismólogo de guardia en SismosWeb.\n\n"
            "Estoy aquí para responder tus dudas sobre actividad sísmica, cómo prepararte ante un terremoto, "
            "explicar cómo funcionan las placas tectónicas o analizar eventos recientes. ¿En qué puedo orientarte hoy?"
        )

    if _tiene_palabra(["mochila", "kit", "provision", "provisiones", "suministro", "suministros", "emergencia"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["mochila"]
    if _tiene_palabra(["edificio", "depto", "departamento", "piso", "ascensor", "altura"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["edificio"]
    if _tiene_palabra(["tsunami", "maremoto", "mar", "ola", "olas", "costa", "playa"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["tsunami"]
    if _tiene_palabra(["cinturon", "cinturón", "fuego", "placa", "placas", "subduccion", "subducción", "nazca"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["cinturon"]
    if _tiene_palabra(["mercalli", "richter", "diferencia", "intensidad", "magnitud"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["mercalli"]
    if _tiene_palabra(["enjambre", "racimo", "swarm", "continuos", "muchos sismos"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["enjambre"]
    if _tiene_palabra(["predecir", "prediccion", "predicción", "adivinar", "animales", "luces"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["predecir"]
    if _tiene_palabra(["ramon", "ramón", "falla"], q):
        return BASE_CONOCIMIENTO_SISMOLOGO["san_ramon"]

    return (
        "🧑‍🔬 **Recomendación Sismológica General:**\n"
        "Durante cualquier movimiento sísmico significativo, recuerda mantener la calma y aplicar la norma "
        "internacional: **Agacharse, Cubrirse y Afirmarse** bajo un mueble resistente.\n\n"
        "Revisa previamente las vías de escape de tu hogar, mantén despejados los pasillos y acuerda un punto de "
        "encuentro con tu familia. Si tienes dudas sobre tsunamis, la mochila de emergencia o fallas geológicas, "
        "puedes preguntarme directamente."
    )


def consultar_asistente_sismologo(pregunta: str, historial: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Genera una respuesta educativa y de prevención ante sismos mediante Ollama o fallback experto."""
    texto = pregunta.strip()
    if not texto:
        return {
            "respuesta": "Por favor escribe tu pregunta sobre sismología, prevención o seguridad sísmica.",
            "fuente": "fallback",
            "modelo": None,
        }

    system_prompt = (
        "Eres el Dr. Hugo Sísmico, Sismólogo y Especialista en Gestión del Riesgo y Emergencias Sísmicas de SismosWeb. "
        "Tu misión es educar a la ciudadanía en español con un tono profesional, empático, claro y pedagógico. "
        "Reglas:\n"
        "1. Si el usuario te saluda o pregunta cómo estás, salúdalo con calidez y preséntate brevemente invitándolo a consultar.\n"
        "2. Basa tus recomendaciones en protocolos oficiales (SENAPRED, USGS, CSN).\n"
        "3. Aclara con firmeza científica que los sismos NO se pueden predecir en fecha y hora exacta.\n"
        "4. Sé conciso y claro: usa viñetas cortas para listas o pasos de acción prácticos.\n"
        "5. Máximo 160 palabras por respuesta."
    )

    # Construir contexto con los últimos turnos de conversación
    contexto_str = ""
    if historial:
        ultimos = historial[-4:]
        for h in ultimos:
            rol = "Usuario" if h.get("rol") == "user" else "Sismólogo"
            contexto_str += f"{rol}: {h.get('contenido', '')}\n"

    prompt_usuario = (
        f"{contexto_str}"
        f"Usuario: {texto}\n"
        f"Dr. Hugo Sísmico:"
    )

    respuesta_ia = _call_ollama_generate(
        model=OLLAMA_MODEL_SUMMARY,
        prompt=prompt_usuario,
        system=system_prompt,
        format_json=False,
    )

    if respuesta_ia and len(respuesta_ia.strip()) > 15:
        return {
            "respuesta": respuesta_ia.strip(),
            "fuente": "ia",
            "modelo": OLLAMA_MODEL_SUMMARY,
        }

    # Fallback inmediato
    return {
        "respuesta": _obtener_respuesta_fallback_sismologo(texto),
        "fuente": "fallback_experto",
        "modelo": None,
    }

