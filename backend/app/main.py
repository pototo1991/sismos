"""API FastAPI de sismos + servicio del frontend estático."""
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
    # Allow-list de métodos HTTP: la app es de sólo lectura.
    if request.method not in ("GET", "HEAD"):
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

    if pais and pais.strip():
        where_clauses.append(sql.SQL("pais_inferido = %(pais)s"))
        params["pais"] = pais.strip()
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

