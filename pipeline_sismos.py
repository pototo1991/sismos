import os
import sys
import time
import re
import logging
from logging.handlers import RotatingFileHandler
import argparse
import requests
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

# ==========================================
# CONFIGURACIÓN
# ==========================================
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
CSN_CATALOGO_URL = "https://www.sismologia.cl/sismicidad/catalogo/{year}/{month:02d}/{date_str}.html"
CSN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SismosWebBot/1.0; +https://github.com/)"
}
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline_sismos.log")

# Cargar automáticamente variables desde .env si existe
def _cargar_env():
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v

_cargar_env()

# Variables de Base de Datos (tomadas de variables de entorno o valores por defecto)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "sismos-db")
DB_USER = os.getenv("DB_USER", "sismos_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "TuPasswordSeguraAqui")
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")

# ==========================================
# SISTEMA DE LOGS
# ==========================================
def setup_logger():
    logger = logging.getLogger("sismos_pipeline")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        # Consola
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # Archivo rotativo (máx 5 MB, hasta 3 archivos de respaldo)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger

log = setup_logger()

# ==========================================
# BASE DE DATOS
# ==========================================
def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            sslmode=DB_SSLMODE
        )
    except Exception as e:
        log.critical(f"No se pudo conectar a PostgreSQL en {DB_HOST}:{DB_PORT}: {e}")
        raise

def inicializar_esquema(conn):
    ddl = """
    CREATE EXTENSION IF NOT EXISTS postgis;

    CREATE TABLE IF NOT EXISTS sismos (
        id VARCHAR(50) PRIMARY KEY,
        fecha_hora_utc TIMESTAMPTZ NOT NULL,
        magnitud NUMERIC(4, 2) NOT NULL,
        tipo_magnitud VARCHAR(15),
        lugar TEXT,
        pais_inferido VARCHAR(100),
        longitud NUMERIC(9, 5) NOT NULL,
        latitud NUMERIC(9, 5) NOT NULL,
        profundidad_km NUMERIC(7, 2),
        geom GEOMETRY(Point, 4326),
        intensidad_mmi NUMERIC(4, 2),
        alerta_tsunami INT DEFAULT 0,
        reportes_felt INT,
        estado VARCHAR(20),
        actualizado_en TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_sismos_fecha ON sismos(fecha_hora_utc DESC);
    CREATE INDEX IF NOT EXISTS idx_sismos_mag ON sismos(magnitud);
    CREATE INDEX IF NOT EXISTS idx_sismos_geom ON sismos USING GIST(geom);
    """
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()

def upsert_sismos(conn, registros):
    if not registros:
        return 0

    query = """
    INSERT INTO sismos (
        id, fecha_hora_utc, magnitud, tipo_magnitud, lugar,
        pais_inferido, longitud, latitud, profundidad_km, geom,
        intensidad_mmi, alerta_tsunami, reportes_felt, estado, actualizado_en
    ) VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        magnitud = EXCLUDED.magnitud,
        tipo_magnitud = EXCLUDED.tipo_magnitud,
        profundidad_km = EXCLUDED.profundidad_km,
        intensidad_mmi = EXCLUDED.intensidad_mmi,
        alerta_tsunami = EXCLUDED.alerta_tsunami,
        reportes_felt = EXCLUDED.reportes_felt,
        estado = EXCLUDED.estado,
        actualizado_en = NOW();
    """

    filas_con_tiempo = [r + (datetime.now(timezone.utc),) for r in registros]

    with conn.cursor() as cur:
        execute_values(
            cur,
            query,
            filas_con_tiempo,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromEWKT(%s), %s, %s, %s, %s, %s)"
        )
    conn.commit()
    return len(registros)

# ==========================================
# EXTRACCIÓN Y VALIDACIÓN USGS
# ==========================================
def fetch_usgs(start_date: str, end_date: str, min_mag: float, retries: int = 5):
    params = {
        "format": "geojson",
        "starttime": start_date,
        "endtime": end_date,
        "minmagnitude": min_mag,
        "limit": 20000,
        "orderby": "time-asc"
    }

    delay = 2
    for intento in range(1, retries + 1):
        try:
            resp = requests.get(USGS_URL, params=params, timeout=35)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 400:
                log.warning(f"Respuesta 400 del USGS (límite excedido) en ventana {start_date} a {end_date}")
                return resp.json()
            else:
                log.warning(f"HTTP {resp.status_code} desde USGS. Intento {intento}/{retries}. Esperando {delay}s...")
        except requests.exceptions.RequestException as e:
            log.warning(f"Fallo de conexión ({e}). Intento {intento}/{retries}. Esperando {delay}s...")

        time.sleep(delay)
        delay = min(delay * 2, 30)

    log.error(f"Imposible recuperar datos tras {retries} intentos: {start_date} a {end_date}")
    return None

def validar_y_normalizar(geojson_data):
    if not geojson_data or "features" not in geojson_data:
        return []

    validados = []
    descartados = 0

    for feat in geojson_data["features"]:
        props = feat.get("properties") or {}
        geometry = feat.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        feat_id = feat.get("id")

        if not feat_id or len(coords) < 2:
            descartados += 1
            continue

        lon, lat = coords[0], coords[1]
        depth = coords[2] if len(coords) > 2 else None

        # Validación geográfica
        if lon is None or lat is None or not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            descartados += 1
            continue

        # Validación de magnitud
        mag = props.get("mag")
        if mag is None:
            descartados += 1
            continue

        # Conversión de timestamp
        t_ms = props.get("time")
        if not t_ms:
            descartados += 1
            continue
        fecha_utc = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)

        # Inferencia de país / región textual
        place = props.get("place") or ""
        pais = place.split(",")[-1].strip() if "," in place else "Región no determinada"

        point_ewkt = f"SRID=4326;POINT({lon:.5f} {lat:.5f})"

        validados.append((
            feat_id,
            fecha_utc,
            float(mag),
            props.get("magType"),
            place,
            pais,
            float(lon),
            float(lat),
            float(depth) if depth is not None else None,
            point_ewkt,
            float(props["mmi"]) if props.get("mmi") is not None else None,
            int(props.get("tsunami", 0)),
            int(props["felt"]) if props.get("felt") is not None else None,
            props.get("status")
        ))

    if descartados > 0:
        log.info(f"Registros descartados por inconsistencia o falta de datos: {descartados}")

    return validados

# ==========================================
# EXTRACCIÓN Y VALIDACIÓN CSN (CHILE)
# ==========================================
def fetch_csn_dia(fecha: datetime, retries: int = 4):
    url = CSN_CATALOGO_URL.format(
        year=fecha.year,
        month=fecha.month,
        date_str=fecha.strftime("%Y%m%d")
    )
    delay = 2
    for intento in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=CSN_HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 404:
                log.info(f"Catálogo CSN aún no disponible (404) para {fecha.strftime('%Y-%m-%d')}")
                return None
            else:
                log.warning(f"HTTP {resp.status_code} desde CSN ({url}). Intento {intento}/{retries}. Esperando {delay}s...")
        except requests.exceptions.RequestException as e:
            log.warning(f"Fallo de conexión a CSN ({e}). Intento {intento}/{retries}. Esperando {delay}s...")

        time.sleep(delay)
        delay = min(delay * 2, 20)

    log.error(f"Imposible recuperar catálogo CSN tras {retries} intentos: {url}")
    return None

def parsear_y_validar_csn(html_text: str):
    if not html_text:
        return []

    tr_pattern = re.compile(r"<tr>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    td_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)

    registros = []
    descartados = 0

    for tr_match in tr_pattern.finditer(html_text):
        tds = td_pattern.findall(tr_match.group(1))
        if len(tds) < 5:
            continue

        # Columna 0: Enlace con ID y Fecha local / Referencia geográfica
        c0 = tds[0]
        id_match = re.search(r"/sismicidad/informes/[^/]+/[^/]+/(\d+)\.html", c0)
        if not id_match:
            continue
        feat_id = f"csn-{id_match.group(1)}"

        # Limpieza del texto descriptivo del lugar
        c0_clean = re.sub(r"<[^>]+>", "\n", c0).strip()
        lines = [l.strip() for l in c0_clean.split("\n") if l.strip()]
        lugar = lines[1] if len(lines) > 1 else (lines[0] if lines else "Chile")

        # Columna 1: Fecha UTC (formato estándar YYYY-MM-DD HH:MM:SS)
        utc_str = re.sub(r"<[^>]+>", "", tds[1]).strip()
        try:
            fecha_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            descartados += 1
            continue

        # Columna 2: Latitud y Longitud
        c2_clean = re.sub(r"<[^>]+>", " ", tds[2]).strip()
        coords = c2_clean.split()
        if len(coords) < 2:
            descartados += 1
            continue

        try:
            lat = float(coords[0])
            lon = float(coords[1])
        except ValueError:
            descartados += 1
            continue

        if not (-180.0 <= lon <= 180.0) or not (-90.0 <= lat <= 90.0):
            descartados += 1
            continue

        # Columna 3: Profundidad
        c3_clean = re.sub(r"<[^>]+>", "", tds[3]).strip()
        prof_match = re.search(r"([\d\.]+)", c3_clean)
        depth = float(prof_match.group(1)) if prof_match else None

        # Columna 4: Magnitud y tipo de escala (Ml, Mlv, Mw)
        c4_clean = re.sub(r"<[^>]+>", "", tds[4]).strip()
        mag_parts = c4_clean.split()
        if not mag_parts:
            descartados += 1
            continue

        try:
            mag = float(mag_parts[0])
        except ValueError:
            descartados += 1
            continue

        mag_type = mag_parts[1] if len(mag_parts) > 1 else "Ml"
        point_ewkt = f"SRID=4326;POINT({lon:.5f} {lat:.5f})"

        registros.append((
            feat_id,
            fecha_utc,
            float(mag),
            mag_type[:15],
            lugar,
            "Chile",
            float(lon),
            float(lat),
            float(depth) if depth is not None else None,
            point_ewkt,
            None,  # intensidad_mmi
            0,     # alerta_tsunami
            None,  # reportes_felt
            "publicado"
        ))

    if descartados > 0:
        log.info(f"Registros CSN descartados por inconsistencia o formato: {descartados}")

    return registros

def filtrar_duplicados_con_usgs(conn, registros_csn, ventana_segundos=150, dist_max_grados=0.7):
    """
    Filtra los sismos de CSN que ya hayan sido registrados por el USGS en la base de datos,
    comparando cercanía temporal (+/- ventana_segundos) y geográfica (dist_max_grados).
    Garantiza que solo se inserten eventos de Chile que no fueron extraídos por USGS.
    """
    if not registros_csn:
        return []

    min_fecha = min(r[1] for r in registros_csn) - timedelta(seconds=ventana_segundos)
    max_fecha = max(r[1] for r in registros_csn) + timedelta(seconds=ventana_segundos)

    query = """
    SELECT id, fecha_hora_utc, latitud, longitud, magnitud
    FROM sismos
    WHERE fecha_hora_utc BETWEEN %s AND %s
      AND id NOT LIKE 'csn-%%';
    """

    with conn.cursor() as cur:
        cur.execute(query, (min_fecha, max_fecha))
        existentes_usgs = cur.fetchall()

    if not existentes_usgs:
        return registros_csn

    filtrados = []
    omitidos = 0

    for csn_rec in registros_csn:
        c_id, c_fecha, c_mag, _, _, _, c_lon, c_lat, *_ = csn_rec
        es_duplicado = False

        for u_id, u_fecha, u_lat, u_lon, u_mag in existentes_usgs:
            delta_sec = abs((c_fecha - u_fecha).total_seconds())
            if delta_sec <= ventana_segundos:
                delta_lat = abs(float(u_lat) - float(c_lat))
                delta_lon = abs(float(u_lon) - float(c_lon))
                if delta_lat <= dist_max_grados and delta_lon <= dist_max_grados:
                    es_duplicado = True
                    omitidos += 1
                    log.debug(f"Sismo CSN {c_id} ({c_fecha}, M{c_mag}) coincide con USGS {u_id} ({u_fecha}, M{u_mag}). Omitiendo.")
                    break

        if not es_duplicado:
            filtrados.append(csn_rec)

    if omitidos > 0:
        log.info(f"Sismos CSN omitidos por coincidir con evento USGS ya registrado: {omitidos}")

    return filtrados

def rutina_csn(conn, dias_atras=3):
    """
    Extrae del CSN todos los sismos ocurridos en Chile durante los últimos N días.
    Filtra los que ya estén presentes en la base de datos (por ejemplo, vía USGS).
    """
    ahora_utc = datetime.now(timezone.utc)
    total_guardados = 0

    log.info(f"Iniciando extracción CSN Chile para los últimos {dias_atras} días...")

    for i in range(dias_atras, -1, -1):
        dia_objetivo = ahora_utc - timedelta(days=i)
        str_dia = dia_objetivo.strftime("%Y-%m-%d")

        log.info(f"Consultando catálogo CSN para el día {str_dia}...")
        html = fetch_csn_dia(dia_objetivo)
        if not html:
            continue

        registros = parsear_y_validar_csn(html)
        log.info(f" -> {len(registros)} sismos parseados de CSN para {str_dia}.")

        if registros:
            nuevos_chile = filtrar_duplicados_con_usgs(conn, registros)
            if nuevos_chile:
                guardados = upsert_sismos(conn, nuevos_chile)
                total_guardados += guardados
                log.info(f" -> Guardados/Actualizados {guardados} sismos exclusivos de CSN.")
            else:
                log.info(" -> Todos los sismos de esta fecha ya se encontraban registrados.")

        time.sleep(0.4)

    log.info(f"Extracción CSN Chile completada. Total nuevos sismos de Chile guardados: {total_guardados}")
    return total_guardados

# ==========================================
# RUTINAS DE EJECUCIÓN GENERAL
# ==========================================
def correr_tramo(conn, fecha_inicio, fecha_fin, min_mag, step_months=1):
    actual = fecha_inicio
    total_insertados = 0

    while actual < fecha_fin:
        siguiente = min(actual + relativedelta(months=step_months), fecha_fin)
        str_ini = actual.strftime("%Y-%m-%d")
        str_fin = siguiente.strftime("%Y-%m-%d")

        log.info(f"Consultando bloque {str_ini} -> {str_fin} (M >= {min_mag})...")
        raw = fetch_usgs(str_ini, str_fin, min_mag)
        registros = validar_y_normalizar(raw)

        if registros:
            guardados = upsert_sismos(conn, registros)
            total_insertados += guardados
            log.info(f" -> Guardados/Actualizados {guardados} eventos.")
        else:
            log.info(" -> 0 eventos detectados.")

        actual = siguiente
        time.sleep(0.4)

    return total_insertados

def rutina_diaria(conn, dias_atras=3, fuente="todas"):
    if fuente in ("todas", "usgs"):
        ahora = datetime.now(timezone.utc)
        desde = ahora - timedelta(days=dias_atras)
        str_ini = desde.strftime("%Y-%m-%dT%H:%M:%S")
        str_fin = ahora.strftime("%Y-%m-%dT%H:%M:%S")

        log.info(f"Iniciando actualización diaria USGS: {str_ini} a {str_fin} (M >= 4.0)...")
        raw = fetch_usgs(str_ini, str_fin, min_mag=4.0)
        registros = validar_y_normalizar(raw)

        total_usgs = upsert_sismos(conn, registros)
        log.info(f"Actualización diaria USGS finalizada con éxito. Total registros procesados: {total_usgs}")

    if fuente in ("todas", "csn"):
        rutina_csn(conn, dias_atras=dias_atras)

def main():
    parser = argparse.ArgumentParser(description="Pipeline ETL de Sismos USGS y CSN (Chile) para PostgreSQL")
    parser.add_argument("--modo", choices=["diario", "historico"], default="diario",
                        help="Modo de ejecución: 'diario' (últimos días) o 'historico' (1960 a hoy)")
    parser.add_argument("--fuente", choices=["todas", "usgs", "csn"], default="todas",
                        help="Fuente de datos: 'todas' (USGS + CSN), 'usgs' o 'csn' (default: todas)")
    parser.add_argument("--dias", type=int, default=3, help="Días hacia atrás en modo diario (default: 3)")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        inicializar_esquema(conn)

        if args.modo == "historico":
            log.info("=== INICIANDO CARGA HISTÓRICA COMPLETA ===")

            if args.fuente in ("todas", "usgs"):
                # Tramo 1: 1960 a 1979 (M >= 5.0) en bloques trimestrales (incluye Gran Terremoto de Valdivia 1960)
                log.info("Fase 1: Extrayendo 1960 a 1979 con M >= 5.0 (incluye Terremoto de Valdivia 1960)...")
                t1 = correr_tramo(conn, datetime(1960, 1, 1), datetime(1980, 1, 1), min_mag=5.0, step_months=3)

                # Tramo 2: 1980 a la fecha (M >= 4.0) en bloques mensuales
                log.info("Fase 2: Extrayendo 1980 hasta hoy con M >= 4.0...")
                t2 = correr_tramo(conn, datetime(1980, 1, 1), datetime.now(), min_mag=4.0, step_months=1)

                log.info(f"=== CARGA HISTÓRICA USGS COMPLETADA: {t1 + t2} eventos totales ===")

            if args.fuente in ("todas", "csn"):
                log.info("Extrayendo sismos CSN de los últimos días...")
                rutina_csn(conn, dias_atras=args.dias)
        else:
            rutina_diaria(conn, dias_atras=args.dias, fuente=args.fuente)

    except Exception as e:
        log.exception(f"Error fatal durante la ejecución del pipeline: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
