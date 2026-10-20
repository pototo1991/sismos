"""Módulo de analítica interna, telemetría silenciosa y geolocalización por IP."""
import ipaddress
import json
import logging
import re
import urllib.request
from typing import Any

from psycopg import sql

from . import database as db

logger = logging.getLogger("sismos.analytics")

# Caché en memoria de geolocalización por IP para no consultar repetidamente
_GEO_CACHE: dict[str, dict[str, Any]] = {}
_MAX_GEO_CACHE = 5000


def init_analytics_db() -> None:
    """Crea la tabla de visitas y sus índices si no existen."""
    ddl = sql.SQL("""
    CREATE TABLE IF NOT EXISTS visitas (
        id SERIAL PRIMARY KEY,
        fecha_visita TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        ip_origen VARCHAR(45) NOT NULL,
        pais VARCHAR(100),
        ciudad VARCHAR(100),
        latitud NUMERIC(8, 4),
        longitud NUMERIC(8, 4),
        dispositivo VARCHAR(50),
        navegador VARCHAR(50),
        user_agent TEXT,
        ruta VARCHAR(255) DEFAULT '/'
    );

    CREATE INDEX IF NOT EXISTS idx_visitas_fecha ON visitas (fecha_visita DESC);
    CREATE INDEX IF NOT EXISTS idx_visitas_pais ON visitas (pais);
    CREATE INDEX IF NOT EXISTS idx_visitas_ip ON visitas (ip_origen);
    """)
    try:
        db.execute_write(ddl)
        logger.info("Tabla de analítica 'visitas' e índices inicializados correctamente")
    except Exception as e:
        logger.error("Error al inicializar tabla de visitas: %s", e)


def es_ip_privada(ip_str: str) -> bool:
    """Determina si una dirección IP pertenece a un rango privado, loopback o enlace local."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return True


def clasificar_user_agent(ua: str) -> tuple[str, str]:
    """Extrae el tipo de dispositivo y navegador a partir de la cabecera User-Agent."""
    if not ua:
        return "Desconocido", "Desconocido"

    ua_lower = ua.lower()

    # Dispositivo
    if "tablet" in ua_lower or "ipad" in ua_lower:
        dispositivo = "Tablet"
    elif "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        dispositivo = "Móvil"
    elif "bot" in ua_lower or "crawler" in ua_lower or "spider" in ua_lower:
        dispositivo = "Bot / Indexador"
    else:
        dispositivo = "Escritorio"

    # Navegador
    if "edg/" in ua_lower:
        navegador = "Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        navegador = "Opera"
    elif "chrome/" in ua_lower and "chromium" not in ua_lower:
        navegador = "Chrome"
    elif "firefox/" in ua_lower:
        navegador = "Firefox"
    elif "safari/" in ua_lower and "chrome" not in ua_lower:
        navegador = "Safari"
    elif "bot" in ua_lower or "spider" in ua_lower or "crawl" in ua_lower:
        navegador = "Bot"
    else:
        navegador = "Otro"

    return dispositivo, navegador


def geolocalizar_ip(ip_str: str, cf_country: str | None = None) -> dict[str, Any]:
    """Estima país, ciudad y coordenadas del proveedor de internet (ISP) de forma silenciosa."""
    if es_ip_privada(ip_str):
        return {
            "pais": cf_country or "Local / LAN",
            "ciudad": "Red Privada / Localhost",
            "lat": None,
            "lon": None,
        }

    # Revisar si ya está en caché local
    if ip_str in _GEO_CACHE:
        return _GEO_CACHE[ip_str]

    resultado: dict[str, Any] = {
        "pais": cf_country or "Desconocido",
        "ciudad": None,
        "lat": None,
        "lon": None,
    }

    # Consulta GeoIP ligera a ip-api en segundo plano
    try:
        url = f"http://ip-api.com/json/{ip_str}?fields=status,country,city,lat,lon"
        req = urllib.request.Request(url, headers={"User-Agent": "SismosWeb-Analytics/1.0"})
        with urllib.request.urlopen(req, timeout=1.8) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "success":
                    resultado["pais"] = data.get("country") or cf_country or "Desconocido"
                    resultado["ciudad"] = data.get("city")
                    resultado["lat"] = data.get("lat")
                    resultado["lon"] = data.get("lon")
    except Exception as e:
        logger.debug("No se pudo geolocalizar IP %s vía GeoIP (usando fallback Cloudflare): %s", ip_str, e)

    # Almacenar en caché acotada
    if len(_GEO_CACHE) >= _MAX_GEO_CACHE:
        _GEO_CACHE.clear()
    _GEO_CACHE[ip_str] = resultado

    return resultado


def registrar_visita_tarea(
    ip: str,
    user_agent: str,
    cf_country: str | None,
    ruta: str = "/",
) -> None:
    """Función para BackgroundTasks que persiste la visita sin retrasar al usuario."""
    try:
        geo = geolocalizar_ip(ip, cf_country)
        dispositivo, navegador = clasificar_user_agent(user_agent)

        query = sql.SQL("""
        INSERT INTO visitas (
            fecha_visita, ip_origen, pais, ciudad, latitud, longitud, dispositivo, navegador, user_agent, ruta
        ) VALUES (
            CURRENT_TIMESTAMP, %(ip)s, %(pais)s, %(ciudad)s, %(lat)s, %(lon)s, %(dispositivo)s, %(navegador)s, %(ua)s, %(ruta)s
        )
        """)

        params = {
            "ip": ip[:45],
            "pais": (geo.get("pais") or "")[:100] or None,
            "ciudad": (geo.get("ciudad") or "")[:100] or None,
            "lat": geo.get("lat"),
            "lon": geo.get("lon"),
            "dispositivo": dispositivo[:50],
            "navegador": navegador[:50],
            "ua": user_agent[:500] if user_agent else None,
            "ruta": ruta[:255],
        }

        db.execute_write(query, params)
    except Exception as e:
        logger.warning("Error al registrar visita en segundo plano: %s", e)


def obtener_resumen_visitas() -> dict[str, Any]:
    """Genera un reporte analítico agregado de visitas."""
    total_q = sql.SQL("SELECT COUNT(*) AS total FROM visitas")
    hoy_q = sql.SQL("SELECT COUNT(*) AS hoy FROM visitas WHERE fecha_visita >= CURRENT_DATE")
    ultimos_dias_q = sql.SQL("""
        SELECT TO_CHAR(fecha_visita, 'YYYY-MM-DD') AS dia, COUNT(*) AS conteo
        FROM visitas
        WHERE fecha_visita >= CURRENT_DATE - INTERVAL '6 days'
        GROUP BY 1 ORDER BY 1 ASC
    """)
    top_paises_q = sql.SQL("""
        SELECT COALESCE(pais, 'Desconocido') AS pais, COUNT(*) AS total
        FROM visitas
        GROUP BY 1 ORDER BY total DESC LIMIT 10
    """)
    top_ciudades_q = sql.SQL("""
        SELECT COALESCE(ciudad, 'No determinada') AS ciudad, COALESCE(pais, 'Desconocido') AS pais, COUNT(*) AS total
        FROM visitas
        WHERE ciudad IS NOT NULL
        GROUP BY 1, 2 ORDER BY total DESC LIMIT 10
    """)
    dispositivos_q = sql.SQL("""
        SELECT COALESCE(dispositivo, 'Desconocido') AS tipo, COUNT(*) AS total
        FROM visitas
        GROUP BY 1 ORDER BY total DESC
    """)
    ultimas_visitas_q = sql.SQL("""
        SELECT
            id,
            TO_CHAR(fecha_visita, 'YYYY-MM-DD HH24:MI:SS') AS fecha,
            ip_origen,
            pais,
            ciudad,
            latitud,
            longitud,
            dispositivo,
            navegador,
            ruta
        FROM visitas
        ORDER BY fecha_visita DESC
        LIMIT 25
    """)

    total_rows = db.fetch_all(total_q)
    hoy_rows = db.fetch_all(hoy_q)
    dias_rows = db.fetch_all(ultimos_dias_q)
    paises_rows = db.fetch_all(top_paises_q)
    ciudades_rows = db.fetch_all(top_ciudades_q)
    dispositivos_rows = db.fetch_all(dispositivos_q)
    ultimas_rows = db.fetch_all(ultimas_visitas_q)

    # Enmascarar ligeramente el último octeto de la IP para visualización limpia
    def _mask_ip(ip: str | None) -> str:
        if not ip:
            return ""
        partes = ip.split(".")
        if len(partes) == 4:
            return f"{partes[0]}.{partes[1]}.{partes[2]}.xxx"
        return ip

    ultimas_limpias = [
        {
            **r,
            "ip_mascara": _mask_ip(r.get("ip_origen")),
            "latitud": float(r["latitud"]) if r.get("latitud") is not None else None,
            "longitud": float(r["longitud"]) if r.get("longitud") is not None else None,
        }
        for r in ultimas_rows
    ]

    return {
        "total_visitas": total_rows[0]["total"] if total_rows else 0,
        "visitas_hoy": hoy_rows[0]["hoy"] if hoy_rows else 0,
        "visitas_ultimos_7_dias": dias_rows,
        "top_paises": paises_rows,
        "top_ciudades": ciudades_rows,
        "dispositivos": dispositivos_rows,
        "ultimas_visitas": ultimas_limpias,
    }
