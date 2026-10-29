"""API FastAPI de sismos + servicio del frontend estático."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg import sql

from . import ai
from . import analytics
from . import database as db
from . import regions

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("sismos.api")

FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool()
    analytics.init_analytics_db()
    yield
    db.close_pool()


app = FastAPI(
    title="Sismos Web API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if os.getenv("ENVIRONMENT") == "development" else None,
    redoc_url=None,
)

# CORS estricto: sólo orígenes explícitos (el frontend se sirve desde el mismo origen).
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET"], allow_headers=[])

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' https://unpkg.com https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: https://*.basemaps.cartocdn.com https://unpkg.com https://server.arcgisonline.com https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://*.tile.opentopomap.org https://basemap.nationalmap.gov; "
    "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Allow-list de métodos HTTP: GET, HEAD y POST (para búsqueda y endpoints de IA)
    if request.method not in ("GET", "HEAD", "POST"):
        return JSONResponse({"detail": "Método no permitido"}, status_code=405)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# TODO(security): agregar rate limiting (p.ej. slowapi o en un reverse proxy)
# y autenticación si el dashboard deja de ser público / se expone fuera de la LAN.
# TODO(security): servir detrás de un reverse proxy con HTTPS (TLS 1.2+) en knossos.


@app.get("/api/health")
def health():
    try:
        db.fetch_all(sql.SQL("SELECT 1 AS ok"))
        return {"status": "ok", "db": "ok"}
    except Exception:
        logger.exception("Health check falló")
        return JSONResponse({"status": "degraded", "db": "error"}, status_code=503)


@app.get("/api/regiones")
def get_regiones():
    """Devuelve la jerarquía de continentes y países disponibles."""
    try:
        return regions.get_geo_filters(db)
    except Exception:
        logger.exception("Error consultando estructura de regiones")
        raise HTTPException(500, "Error interno al consultar las regiones")


@app.get("/api/sismos")
def get_sismos(
    desde: datetime | None = Query(None, description="Fecha/hora UTC mínima (ISO 8601)"),
    hasta: datetime | None = Query(None, description="Fecha/hora UTC máxima (ISO 8601)"),
    mag_min: float = Query(0.0, ge=0, le=10),
    mag_max: float = Query(10.0, ge=0, le=10),
    prof_max: float = Query(1000.0, ge=0, le=1000),
    continente: str | None = Query(None, max_length=50, description="Identificador del continente"),
    pais: str | None = Query(None, max_length=100, description="Nombre del país"),
    region: str | None = Query(None, max_length=100, description="Región o localidad específica"),
    sismo_id: str | None = Query(None, max_length=50, description="Identificador único del sismo"),
    bbox: str | None = Query(None, max_length=100, description="Bounding box min_lon,min_lat,max_lon,max_lat"),
    limit: int = Query(2000, ge=1, le=10000),
):
    """Devuelve sismos filtrados como GeoJSON FeatureCollection."""
    if mag_min > mag_max:
        raise HTTPException(400, "mag_min no puede ser mayor que mag_max")

    where_clauses = [
        sql.SQL("magnitud BETWEEN %(mag_min)s AND %(mag_max)s"),
        sql.SQL("COALESCE(profundidad_km, 0) <= %(prof_max)s"),
        sql.SQL("(%(desde)s::timestamptz IS NULL OR fecha_hora_utc >= %(desde)s::timestamptz)"),
        sql.SQL("(%(hasta)s::timestamptz IS NULL OR fecha_hora_utc <= %(hasta)s::timestamptz)"),
    ]
    params: dict[str, Any] = {
        "mag_min": mag_min, "mag_max": mag_max, "prof_max": prof_max,
        "desde": desde, "hasta": hasta, "limit": limit,
    }

    if sismo_id and sismo_id.strip():
        where_clauses.append(sql.SQL("id = %(sismo_id)s"))
        params["sismo_id"] = sismo_id.strip()

    if bbox and bbox.strip():
        try:
            parts = [float(x.strip()) for x in bbox.split(",")]
            if len(parts) == 4:
                min_lon, min_lat, max_lon, max_lat = parts
                if min_lon <= max_lon:
                    where_clauses.append(sql.SQL(
                        "(COALESCE(ST_Y(geom), latitud::float8) BETWEEN %(min_lat)s AND %(max_lat)s "
                        "AND COALESCE(ST_X(geom), longitud::float8) BETWEEN %(min_lon)s AND %(max_lon)s)"
                    ))
                else:
                    where_clauses.append(sql.SQL(
                        "(COALESCE(ST_Y(geom), latitud::float8) BETWEEN %(min_lat)s AND %(max_lat)s "
                        "AND (COALESCE(ST_X(geom), longitud::float8) >= %(min_lon)s OR COALESCE(ST_X(geom), longitud::float8) <= %(max_lon)s))"
                    ))
                params.update({"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        except Exception:
            logger.warning("bbox inválido: %s", bbox)

    PAIS_MAP = {
        "japon": ["Japan", "Japan region", "Japan Earthquake"],
        "japón": ["Japan", "Japan region", "Japan Earthquake"],
        "japan": ["Japan", "Japan region", "Japan Earthquake"],
        "peru": ["Peru"],
        "perú": ["Peru"],
        "mexico": ["Mexico"],
        "méxico": ["Mexico"],
        "estados unidos": ["United States"],
        "eeuu": ["United States"],
        "usa": ["United States"],
        "united states": ["United States"],
        "italia": ["Italy"],
        "italy": ["Italy"],
        "turquia": ["Turkey"],
        "turquía": ["Turkey"],
        "turkey": ["Turkey"],
        "nueva zelanda": ["New Zealand"],
        "new zealand": ["New Zealand"],
        "haiti": ["Haiti"],
        "haití": ["Haiti"],
        "grecia": ["Greece"],
        "greece": ["Greece"],
        "filipinas": ["Philippines"],
        "philippines": ["Philippines"],
        "espana": ["Spain"],
        "españa": ["Spain"],
        "spain": ["Spain"],
    }

    if pais and pais.strip():
        clean_p = pais.strip().lower()
        if clean_p in PAIS_MAP:
            where_clauses.append(sql.SQL("pais_inferido = ANY(%(paises_alias)s)"))
            params["paises_alias"] = PAIS_MAP[clean_p]
        else:
            where_clauses.append(sql.SQL("pais_inferido ILIKE %(pais)s"))
            params["pais"] = f"%{pais.strip()}%"
    elif continente and continente.strip():
        paises_cont = regions.get_paises_para_continente(continente.strip(), db)
        if paises_cont:
            where_clauses.append(sql.SQL("pais_inferido = ANY(%(paises_cont)s)"))
            params["paises_cont"] = paises_cont
        else:
            where_clauses.append(sql.SQL("FALSE"))

    if region and region.strip():
        where_clauses.append(sql.SQL("lugar ILIKE %(region_pattern)s"))
        params["region_pattern"] = f"%{region.strip()}%"

    query = sql.SQL(
        """
        SELECT id, fecha_hora_utc, magnitud, tipo_magnitud, lugar, pais_inferido,
               profundidad_km, intensidad_mmi, alerta_tsunami, estado,
               COALESCE(ST_X(geom), longitud::float8) AS lon,
               COALESCE(ST_Y(geom), latitud::float8)  AS lat
        FROM {tabla}
        WHERE {conditions}
        ORDER BY fecha_hora_utc DESC
        LIMIT %(limit)s
        """
    ).format(
        tabla=db.SISMOS_TABLE,
        conditions=sql.SQL(" AND ").join(where_clauses),
    )

    try:
        rows = db.fetch_all(query, params)
    except Exception:
        logger.exception("Error consultando sismos")
        raise HTTPException(500, "Error interno al consultar los datos")

    def _num(v):
        return float(v) if v is not None else None

    def _clean_text(v):
        if not v or "Ã" not in v:
            return v
        try:
            return v.encode("latin1").decode("utf-8")
        except Exception:
            return v

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [_num(r["lon"]), _num(r["lat"])]},
            "properties": {
                "id": r["id"],
                "fecha_utc": r["fecha_hora_utc"].isoformat() if r["fecha_hora_utc"] else None,
                "magnitud": _num(r["magnitud"]),
                "tipo_magnitud": r["tipo_magnitud"],
                "profundidad_km": _num(r["profundidad_km"]),
                "referencia": _clean_text(r["lugar"]),
                "pais": r["pais_inferido"],
                "intensidad_mmi": _num(r["intensidad_mmi"]),
                "intensidad_estimada": ai.estimar_intensidad_mercalli({
                    "id": r["id"],
                    "magnitud": _num(r["magnitud"]),
                    "profundidad_km": _num(r["profundidad_km"]),
                    "referencia": _clean_text(r["lugar"]),
                }),
                "alerta_tsunami": bool(r["alerta_tsunami"]),
                "estado": r["estado"],
            },
        }
        for r in rows
        if r["lon"] is not None and r["lat"] is not None
    ]
    return {"type": "FeatureCollection", "features": features}


# ---- Frontend estático (mismo origen que la API) ----
if FRONTEND_DIR.is_dir():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    data_dir = FRONTEND_DIR / "data"
    if data_dir.is_dir():
        app.mount("/data", StaticFiles(directory=data_dir), name="data")

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
    def favicon():
        favicon_path = FRONTEND_DIR / "assets" / "icon_sismos.png"
        if favicon_path.is_file():
            return FileResponse(favicon_path, media_type="image/png")
        return Response(status_code=404)

    @app.get("/", include_in_schema=False)
    def index(request: Request, background_tasks: BackgroundTasks):
        cf_ip = request.headers.get("CF-Connecting-IP")
        xff = request.headers.get("X-Forwarded-For")
        if cf_ip:
            client_ip = cf_ip.strip()
        elif xff:
            client_ip = xff.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        else:
            client_ip = "127.0.0.1"

        ua = request.headers.get("User-Agent", "")
        cf_country = request.headers.get("CF-IPCountry")

        background_tasks.add_task(
            analytics.registrar_visita_tarea,
            ip=client_ip,
            user_agent=ua,
            cf_country=cf_country,
            ruta="/",
        )
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    logger.warning("Directorio de frontend no encontrado: %s", FRONTEND_DIR)


@app.get("/api/analytics/resumen", tags=["Analytics"])
def get_analytics_resumen():
    """Retorna estadísticas consolidadas de visitas, países, ciudades y dispositivos."""
    try:
        return analytics.obtener_resumen_visitas()
    except Exception as e:
        logger.error("Error al obtener resumen de analítica: %s", e)
        raise HTTPException(status_code=500, detail="Error interno al consultar analítica")


# ---- Endpoints de Inteligencia Artificial Local (Fase 6) ----
@app.get("/api/ai/status", tags=["IA"])
def get_ai_status():
    """Verifica el estado de Ollama y los modelos configurados."""
    return ai.check_ollama_status()


@app.get("/api/ai/boletin/{sismo_id}", tags=["IA"])
async def get_ai_boletin(sismo_id: str):
    """Genera o recupera de caché un boletín informativo sintético para el sismo solicitado."""
    clean_id = sismo_id.strip()
    if not clean_id or len(clean_id) > 64:
        raise HTTPException(400, "ID de sismo inválido")

    query = sql.SQL(
        """
        SELECT id, fecha_hora_utc, magnitud, tipo_magnitud, lugar, pais_inferido,
               profundidad_km, intensidad_mmi, alerta_tsunami, estado
        FROM {tabla}
        WHERE id = %(sismo_id)s
        LIMIT 1
        """
    ).format(tabla=db.SISMOS_TABLE)

    try:
        rows = db.fetch_all(query, {"sismo_id": clean_id})
    except Exception as e:
        logger.error("Error al buscar sismo %s para boletín: %s", clean_id, e)
        raise HTTPException(500, "Error interno al consultar sismo")

    if not rows:
        raise HTTPException(404, f"Sismo con ID '{clean_id}' no encontrado")

    sismo = rows[0]
    try:
        res = await asyncio.to_thread(ai.generar_boletin_sismo, sismo)
        return res
    except Exception as e:
        logger.error("Error generando boletín IA: %s", e)
        # Fallback de emergencia
        return {
            "sismo_id": clean_id,
            "boletin": ai._boletin_fallback(sismo),
            "fuente": "fallback_error",
            "modelo": None,
        }


@app.api_route("/api/ai/buscar", methods=["GET", "POST"], tags=["IA"])
async def ai_search(request: Request, q: str | None = Query(None, max_length=250)):
    """Extrae filtros estructurados de búsqueda a partir de una consulta en lenguaje natural."""
    query_text = q
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict) and "q" in body:
                query_text = str(body["q"])
        except Exception:
            pass

    if not query_text or not query_text.strip():
        raise HTTPException(400, "Debe proporcionar una consulta de búsqueda (parámetro 'q')")

    clean_text = query_text.strip()[:250]
    return await asyncio.to_thread(ai.interpretar_busqueda_nl, clean_text)


@app.get("/api/ai/intensidad/{sismo_id}", tags=["IA"])
def get_ai_intensidad(sismo_id: str):
    """Calcula la intensidad percibida probable (Escala Mercalli Modificada / ShakeMap) para un sismo."""
    clean_id = sismo_id.strip()
    if not clean_id or len(clean_id) > 64:
        raise HTTPException(400, "ID de sismo inválido")

    query = sql.SQL(
        """
        SELECT id, fecha_hora_utc, magnitud, tipo_magnitud, lugar, pais_inferido,
               profundidad_km, intensidad_mmi, alerta_tsunami
        FROM {tabla}
        WHERE id = %(sismo_id)s
        LIMIT 1
        """
    ).format(tabla=db.SISMOS_TABLE)

    try:
        rows = db.fetch_all(query, {"sismo_id": clean_id})
    except Exception as e:
        logger.error("Error al buscar sismo %s para intensidad: %s", clean_id, e)
        raise HTTPException(500, "Error interno al consultar sismo")

    if not rows:
        raise HTTPException(404, f"Sismo con ID '{clean_id}' no encontrado")

    sismo = rows[0]
    return ai.estimar_intensidad_mercalli(sismo)


@app.get("/api/ai/enjambres", tags=["IA"])
def get_ai_enjambres(dias: int = Query(7, ge=1, le=30)):
    """Detecta enjambres sísmicos activos y clasifica réplicas vinculadas a sismos principales."""
    query = sql.SQL(
        """
        SELECT id, fecha_hora_utc, magnitud, tipo_magnitud, lugar, pais_inferido,
               profundidad_km,
               COALESCE(ST_X(geom), longitud::float8) AS lon,
               COALESCE(ST_Y(geom), latitud::float8)  AS lat
        FROM {tabla}
        WHERE fecha_hora_utc >= NOW() - (%(dias)s || ' days')::interval
          AND magnitud IS NOT NULL
        ORDER BY fecha_hora_utc DESC
        LIMIT 2000
        """
    ).format(tabla=db.SISMOS_TABLE)

    try:
        rows = db.fetch_all(query, {"dias": dias})
    except Exception as e:
        logger.error("Error al consultar sismos para análisis de enjambres: %s", e)
        raise HTTPException(500, "Error interno al analizar enjambres")

    return ai.analizar_enjambres_y_replicas(rows)


@app.post("/api/ai/sismologo", tags=["IA"])
async def post_ai_sismologo(request: Request):
    """Asistente Educativo y Preventivo: responde dudas ciudadanas sobre sismología y emergencias."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Cuerpo JSON inválido")

    if not isinstance(body, dict):
        raise HTTPException(400, "Payload debe ser un objeto JSON")

    pregunta = str(body.get("pregunta", "")).strip()
    if not pregunta:
        raise HTTPException(400, "Debe enviar el campo 'pregunta'")

    clean_pregunta = pregunta[:350]
    historial = body.get("historial")
    if not isinstance(historial, list):
        historial = []

    # Validar elementos del historial
    clean_historial = []
    for item in historial[-6:]:
        if isinstance(item, dict) and "rol" in item and "contenido" in item:
            clean_historial.append({
                "rol": "user" if item["rol"] == "user" else "assistant",
                "contenido": str(item["contenido"])[:300],
            })

    return await asyncio.to_thread(ai.consultar_asistente_sismologo, clean_pregunta, clean_historial)


