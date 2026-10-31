# Roadmap de Desarrollo · Sismos Web

Este documento define la visión estratégica, fases y tareas priorizadas para la evolución de **Sismos Web**, transformándolo de un visor estático a una plataforma de monitoreo sísmico interactiva, analítica y en tiempo real.

---

## 🗺️ Visión del Proyecto
Proporcionar una plataforma web rápida, moderna, segura y accesible para la visualización, monitoreo y análisis de eventos sísmicos a nivel regional y global, optimizada tanto para escritorio como para dispositivos móviles.

---

## 📌 Fases del Roadmap

### 🚀 Fase 1: Responsividad Total y Paneles Colapsables
> **Objetivo:** Garantizar una experiencia fluida tanto en computadoras de escritorio como en tablets y smartphones.
- [x] **Diseño de layout flexible:** El mapa ocupa el 100% del espacio útil sin desbordamientos de página.
- [x] **Paneles colapsables en escritorio:** Posibilidad de ocultar los paneles laterales de Filtros y Estadísticas para maximizar el área del mapa con transiciones suaves.
- [x] **Modo Drawer / Offcanvas en móviles:** En pantallas medianas y pequeñas ($\le 1024\text{px}$), los filtros y estadísticas se despliegan como paneles laterales táctiles (*slide-over drawers*) con backdrop translúcido oscuro y botón de cierre.
- [x] **Optimización móvil para smartphones ($\le 680\text{px}$):** Barra superior compacta tipo *icon-only* sin desbordamiento horizontal, soporte para viewport dinámico (`100dvh`) y áreas seguras de notch/home-bar (`env(safe-area-inset)`), inputs a $16\text{px}$ para evitar auto-zoom molesto en Safari iOS, y cierre táctil instantáneo al interactuar con el mapa.
- [x] **Botones de acción rápida en topbar:** Acceso directo a "⚙️ Filtros" y "📊 Resumen" en la barra superior con retroalimentación visual de estado activo sincronizada en tiempo real.
- [x] **Cierre inteligente en móvil:** Al enviar el formulario de filtros, restablecerlos o tocar un sismo de la lista, el panel se cierra automáticamente para mostrar el mapa.
- [x] **Redibujado automático de Leaflet:** Llamadas a `map.invalidateSize()` coordinadas con las transiciones CSS y eventos de redimensionamiento de ventana.

---

### ⏱️ Fase 2: Inmediatez y Monitoreo "En Vivo"
> **Objetivo:** Resolver la necesidad inmediata de información cuando ocurre un temblor.
- [x] **Interruptor y ciclo de auto-refresco:** Control de actualización automática sincronizado por defecto con el ciclo de la base de datos (cada 3 min), con selector a 5 min, 2 min, 1 min o Pausa, y cuenta regresiva visual en vivo (MM:SS).
- [x] **Botón de recarga instantánea:** Botón con animación de giro para forzar la actualización manual en cualquier momento.
- [x] **Tarjeta de "Último sismo relevante":** Componente destacado en la parte superior del panel de resumen con indicador pulsante de evento reciente, magnitud destacada con código de color, profundidad, referencia geográfica y botón para volar y centrar el mapa en ese sismo.
- [x] **Tiempos relativos humanizados:** Formato en español (*"Hace X min"*, *"Hace X h"*, *"Ayer"*) en el último evento, en la lista de sismos y en los popups, con actualización automática periódica cada 30 segundos.
- [x] **Alertas sonoras y notificaciones visuales:** Síntesis armónica nativa vía Web Audio API (campanilla suave) conmutable mediante botón 🔔/🔕 (persistido en `localStorage`), más notificaciones tipo *Toast* flotantes para sismos nuevos significativos ($\ge 4.5$).
- [x] **Pipeline ETL Multi-Fuente (USGS + CSN Chile):** Ingesta automática combinada de eventos globales (USGS) y sismicidad local chilena (Centro Sismológico Nacional - `sismologia.cl`), capturando micro-sismos ($M < 4.0$) omitidos internacionalmente.
- [x] **Deduplicación Espacio-Temporal Inteligente:** Filtro en memoria y base de datos ($\pm 150\,\text{s}$ y $\le 0.7^\circ$) para evitar duplicados entre ambas redes y garantizar datos limpios en la cartografía.
- [x] **Automatización de Ingesta (n8n / Cron):** Modos de ejecución `--modo diario` e `--modo historico` con selector de `--fuente` (`todas`, `usgs`, `csn`) y registro continuo en `pipeline_sismos.log`.

---

### 🌋 Fase 3: Capas Geológicas y Cartografía Avanzada
> **Objetivo:** Aportar contexto sismológico y geológico real sobre el origen de los terremotos.
- [x] **Límites de placas tectónicas:** Capa GeoJSON vectorial superpuesta (`placas.geojson`, modelo PB2002) servida localmente con nombres de placas en español (Nazca, Sudamericana, Cinturón del Pacífico, Cocos, etc.), estilo fosforescente reactivo y popups interactivos.
- [x] **Clustering regional (Agrupación):** Integración de `Leaflet.markercluster` con diseño adaptado al modo oscuro para condensar miles de eventos en áreas densas y navegar sin saturar el rendimiento.
- [x] **Mapa de calor (Heatmap de densidad):** Integración de `Leaflet.heat` con gradiente dinámico según severidad sísmica (verde $\rightarrow$ amarillo $\rightarrow$ naranja $\rightarrow$ rojo).
- [x] **Selector rápido de modos de visualización:** Controles de pestaña en el panel de filtros (`[Puntos] [Agrupados] [Calor]`) más interruptor para ocultar o mostrar las placas tectónicas.
- [x] **Selector de capas base enriquecido:** Soporte integrado para `Modo Oscuro`, `Relieve Topográfico (Esri Shaded Relief/Topo)`, `Satélite` y `Calles (OSM)`.
- [x] **Visualización mundial sin repetición de continentes:** Configuración de teselas con `noWrap: true`, límites globales estrictos (`maxBounds`, `maxBoundsViscosity: 1.0`, `worldCopyJump: false`) y zoom mínimo controlado (`minZoom: 2`), garantizando que el planisferio se renderice una sola vez sin duplicaciones horizontales.

---

### 📊 Fase 4: Analítica Interactiva, Filtrado Avanzado y UX
> **Objetivo:** Facilitar la interpretación de datos, búsqueda precisa y experiencia de usuario optimizada.
- [x] **Sincronización Bidireccional Lista $\leftrightarrow$ Mapa:** Al hacer clic en un sismo de la lista, el mapa vuela al epicentro, abre el popup y resalta el elemento; y al hacer clic en un marcador del mapa, se resalta y desplaza automáticamente el sismo correspondiente en la lista.
- [x] **Panel de filtros modular con pestañas:** Separación entre *"🎯 Criterios"* (ubicación, fechas, severidad) y *"🗺️ Capas & Leyenda"* (modos de visualización, capas base, simbología).
- [x] **Selectores desplegables con búsqueda predictiva:** Componentes interactivos tipo *custom-select* con caja de búsqueda en tiempo real para países y regiones/localidades.
- [x] **Filtro espacial por área visible (Bounding Box):** Botón flotante animado `🔍 Buscar en esta área` que detecta el movimiento del mapa y consulta sismos acotados al visor actual.
- [x] **Histograma interactivo de magnitudes:** Barras animadas con proporciones porcentuales y conteo de sismos por escala (<3.0, 3.0-4.9, 5.0-6.4, ≥6.5) que permiten filtrar la vista al hacer clic sobre cada rango.
- [x] **Exportación directa de datos:** Botones para generar y descargar instantáneamente archivos estructurados en formato **CSV** y **GeoJSON** con los datos filtrados en pantalla.
- [x] **URLs compartibles y directas:** Generación de enlaces con parámetros en la URL (`?sismo=id`, `?continente=...`, `?mag_min=...`, `?lat=...&lon=...&zoom=...`) y botón de copiar enlace al portapapeles con confirmación toast.
- [x] **Identidad visual y soporte Favicon:** Incorporación del icono oficial de la aplicación (`icon_sismos.png` en `frontend/assets/`), enlace `rel="icon"` / `apple-touch-icon` en el frontend y endpoint `/favicon.ico` con soporte `GET` y `HEAD` en FastAPI.
- [x] **Normalización de caracteres y tildes:** Decodificación automática de caracteres especiales y diacríticos (UTF-8 / Latin-1) de referencias geográficas tanto en el pipeline ETL (`pipeline_sismos.py`) como en la API backend (`main.py`).

---

### 📱 Fase 5: Aplicación Web Progresiva (PWA) y Notificaciones (Pendiente / En pausa)
> **Objetivo:** Accesibilidad directa como aplicación nativa móvil.
> *(Fase pausada por ahora para enfocarse en la experiencia web actual)*
- [ ] **Soporte PWA:** `manifest.json` y Service Worker para instalar como app en Android / iOS / Desktop.
- [ ] **Modo offline básico:** Cache de tiles y datos recientes.
- [ ] **Web Push Notifications:** Notificaciones push de sismos destacados según filtros de preferencia del usuario.

---

### 🤖 Fase 6: Inteligencia Artificial Local (Ollama en Knossos)
> **Objetivo:** Incorporar capacidades de IA generativa, extracción semántica y análisis sismológico local aprovechando el contenedor de Ollama existente en el servidor `knossos` (con los modelos de alta precisión `qwen2.5:3b` y `llama3.2:3b`), garantizando costo $0, privacidad total de datos, Grounding RAG científico y alta coherencia semántica.

- [x] **Infraestructura y Conexión Docker (FastAPI $\leftrightarrow$ Ollama):**
  - Conectar el contenedor `sismos-web` con el servicio `ollama:11434` en `knossos` mediante red Docker compartida o variable `OLLAMA_BASE_URL`.
  - Configurar variables de entorno en el backend:
    - `OLLAMA_BASE_URL=http://192.168.100.16:11434` (configurable a localhost o knossos)
    - `OLLAMA_MODEL_PARSER=qwen2.5:3b` (especializado en estructuración de datos y JSON estricto)
    - `OLLAMA_MODEL_SUMMARY=llama3.2:3b` (especializado en síntesis, redacción en lenguaje natural y asistencia con RAG)
  - Cliente optimizado con tolerancia a fallos y timeouts configurables.
  - **Operación desacoplada (*Graceful Degradation*):** Si Ollama está ocupado, reiniciándose o no disponible, la plataforma web continúa funcionando al 100% en modo estándar sin mostrar errores al usuario, con fallback heurístico automático.

- [x] **Buscador en Lenguaje Natural con Extracción JSON (`qwen2.5:1.5b`):**
  - Botón interactivo `✨ Búsqueda IA` en la barra superior con barra flotante y chips con consultas de ejemplo.
  - Endpoint `/api/ai/buscar` que utiliza `qwen2.5:1.5b` con *system prompt* estricto y formato JSON para extraer criterios de búsqueda (`pais`, `mag_min`, `dias`, `prof_max`).
  - Aplicación automática instantánea de los filtros resultantes en el mapa, sliders, selector de países y recarga dinámica con toast informativo.

- [x] **Generador de Boletines Informativos Breves (`llama3.2:1b`):**
  - Botón interactivo con micro-animación `✨ Resumen IA` en la tarjeta de "Último Evento Sísmico" y dentro del popup de cada marcador en el mapa.
  - Endpoint `/api/ai/boletin/{sismo_id}` que sintetiza un reporte conciso en español formal:
    - Evaluación rigurosa de magnitud y profundidad (superficial vs. intermedia/profunda).
    - Localización geográfica precisa.
    - Confirmación o descarte de riesgo de tsunami.
  - Caché en memoria de alto rendimiento para responder en 0 ms ante consultas repetidas del mismo sismo.

- [x] **Detección Asistida de Enjambres Sísmicos y Clasificación de Réplicas:**
  - Algoritmo espacio-temporal (Haversine + ventana temporal) para detectar patrones anómalos (acumulación de múltiples sismos en un radio reducido en pocas horas).
  - Alerta visual interactiva en la cabecera cuando se detecta actividad sísmica en racimo (*«⚠️ Enjambre sísmico en desarrollo: 19 eventos detectados en las últimas horas en Mina Collahuasi»*), con botón para centrar en el mapa.
  - Agrupación automática y marcado de réplicas vinculadas a un sismo principal (*mainshock*) según radio de ruptura sismológico de Båth/Gardner-Knopoff.
  - Endpoint `/api/ai/enjambres` con caché en memoria.

- [x] **Estimación de Intensidad Percibida (*ShakeMap IA* / Escala Mercalli):**
  - Cálculo de la intensidad sentida probable (MMI Grados I a X+) en el epicentro y localidades cercanas según la magnitud de momento, profundidad hipocentral y modelo empírico GMPE de atenuación logarítmica.
  - Presentación amigable y colorimétrica en el detalle del sismo y en la tarjeta de último evento: *«Intensidad estimada: Grado IV (Moderado en zona epicentral) / Grado II (Leve a 100 km)»*.
  - Endpoint `/api/ai/intensidad/{sismo_id}` e inclusión directa en las propiedades GeoJSON de cada evento.

- [x] **Asistente Educativo y Preventivo ("Pregúntale al Sismólogo"):**
  - Módulo interactivo ("Dr. Hugo Sísmico") para resolver inquietudes ciudadanas sobre medidas de seguridad, mochila de emergencia, qué hacer en edificios altos, peligro de tsunami, Cinturón de Fuego, comportamiento de placas tectónicas y diferencias entre Magnitud y Mercalli.
  - Botón interactivo en cabecera `🧑‍🔬 Pregúntale al Sismólogo`, ventana modal/drawer con diseño conversacional y chips con consultas frecuentes.
  - Endpoint `POST /api/ai/sismologo` integrado con Ollama local y base de conocimiento experta para respuesta garantizada en modo fallback.

---

### 📈 Fase 7: Métricas de Tráfico, Geolocalización por IP y Analítica Interna (Modo Silencioso)
> **Objetivo:** Registrar el volumen real de visitas, concurrencia temporal y procedencia geográfica estimada (país, ciudad, coordenadas aproximadas del ISP y tipo de dispositivo) en PostgreSQL de forma 100% transparente y en segundo plano, sin requerir permisos invasivos ni alterar la experiencia del usuario.

- [x] **Estructura y Persistencia en PostgreSQL (Tabla `visitas`):**
  - Creación de tabla optimizada `visitas`:
    - `id SERIAL PRIMARY KEY`
    - `fecha_visita TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP`
    - `ip_origen VARCHAR(45)` (con enmascaramiento automático de IP para visualización)
    - `pais VARCHAR(100)`, `ciudad VARCHAR(100)`
    - `latitud NUMERIC(8, 4)`, `longitud NUMERIC(8, 4)` (coordenadas estimadas del nodo ISP)
    - `dispositivo VARCHAR(50)`, `navegador VARCHAR(50)`, `user_agent TEXT`
    - `ruta VARCHAR(255)`
  - Índices sobre `fecha_visita DESC`, `pais` e `ip_origen` para reportes analíticos ultrarrápidos.

- [x] **Middleware Asíncrono no Bloqueante (FastAPI):**
  - Captura inteligente únicamente en visitas a la página principal (`GET /`), descartando automáticamente recursos estáticos (`/css/`, `/js/`, `/assets/`, `/data/`) y endpoints de pooling frecuente para no saturar la BD.
  - Extracción de IP real y país desde cabeceras de proxy Cloudflare (`CF-Connecting-IP`, `X-Forwarded-For`, `CF-IPCountry`).
  - Registro diferido mediante `BackgroundTasks` de FastAPI garantizando 0 ms de sobrecarga en la respuesta al visitante.

- [x] **Módulo de Geolocalización IP (GeoIP en Segundo Plano):**
  - Estimación silenciosa de ciudad y coordenadas aproximadas de red (ISP) con caché en memoria para no repetir consultas a la misma IP.
  - Operación 100% transparente sin invocar `navigator.geolocation` ni mostrar popups de solicitud de permisos al usuario.

- [x] **Endpoint y Reporte Analítico para Administración (`/api/analytics/resumen`):**
  - Endpoint de consulta interna con métricas clave: total de visitas históricas, visitas del día, gráfico de visitas de los últimos 7 días, ranking de países/ciudades y desglose por dispositivo y navegador.

