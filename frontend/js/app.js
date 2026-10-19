'use strict';

(() => {
  const $ = (id) => document.getElementById(id);

  // ---- Configuración inicial del mapa ----
  const INITIAL_CENTER = [-33.45, -70.66];
  const INITIAL_ZOOM = 5;
  const WORLD_BOUNDS = L.latLngBounds(L.latLng(-85.05112878, -180), L.latLng(85.05112878, 180));

  const map = L.map('map', {
    zoomControl: true,
    worldCopyJump: false,
    minZoom: 2,
    maxBounds: WORLD_BOUNDS,
    maxBoundsViscosity: 1.0,
  }).setView(INITIAL_CENTER, INITIAL_ZOOM);

  const darkBase = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ', maxZoom: 16, noWrap: true, bounds: WORLD_BOUNDS,
  });
  const darkLabels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}', {
    attribution: '', maxZoom: 16, noWrap: true, bounds: WORLD_BOUNDS,
  });
  const dark = L.layerGroup([darkBase, darkLabels]);

  const topo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Sources: GEBCO, USGS, NOAA', maxZoom: 18, noWrap: true, bounds: WORLD_BOUNDS,
  });

  const satelite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri', maxZoom: 18, noWrap: true, bounds: WORLD_BOUNDS,
  });

  const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors', maxZoom: 19, noWrap: true, bounds: WORLD_BOUNDS,
  });

  const baseLayers = {
    '🏔️ Relieve Topográfico': topo,
    '🌙 Modo Oscuro': dark,
    '🛰️ Satélite': satelite,
    '🗺️ Calles (OSM)': osm,
  };

  const savedBaseLayer = localStorage.getItem('sismos_base_layer');
  if (savedBaseLayer && baseLayers[savedBaseLayer]) {
    baseLayers[savedBaseLayer].addTo(map);
  } else {
    topo.addTo(map);
  }

  map.on('baselayerchange', (e) => {
    localStorage.setItem('sismos_base_layer', e.name);
  });

  // ---- Capas de sismos (Puntos, Clusters y Mapa de Calor) ----
  let currentViewMode = 'puntos';
  const sismosLayer = L.layerGroup().addTo(map);

  const sismosCluster = L.markerClusterGroup({
    showCoverageOnHover: false,
    maxClusterRadius: 42,
    spiderfyOnMaxZoom: true,
    iconCreateFunction: (cluster) => {
      const count = cluster.getChildCount();
      let c = 'small';
      if (count > 50) c = 'large';
      else if (count > 15) c = 'medium';
      return L.divIcon({
        html: `<div><span>${count}</span></div>`,
        className: `custom-cluster custom-cluster-${c}`,
        iconSize: [38, 38],
      });
    },
  });

  let heatLayer = null;

  // ---- Capa Geológica de Placas Tectónicas ----
  const NOMBRES_PLACAS = {
    'AF': 'Placa Africana',
    'AN': 'Placa Antártica',
    'AR': 'Placa Arábiga',
    'AU': 'Placa Australiana',
    'CA': 'Placa del Caribe',
    'CO': 'Placa de Cocos',
    'EU': 'Placa Euroasiática',
    'IN': 'Placa Índica',
    'JF': 'Placa Juan de Fuca',
    'NA': 'Placa Norteamericana',
    'NZ': 'Placa de Nazca',
    'PA': 'Placa del Pacífico',
    'PH': 'Placa Filipina',
    'SA': 'Placa Sudamericana',
    'SC': 'Placa de Scotia',
  };

  const placasLayer = L.geoJSON(null, {
    style: {
      color: '#ff7a29',
      weight: 2,
      opacity: 0.85,
      dashArray: '5, 5',
    },
    onEachFeature: (feature, layer) => {
      const p = feature.properties || {};
      const nomA = NOMBRES_PLACAS[p.PlateA] || p.PlateA || '';
      const nomB = NOMBRES_PLACAS[p.PlateB] || p.PlateB || '';
      const titulo = (nomA && nomB) ? `${nomA} ↔ ${nomB}` : (p.Name || 'Límite Tectónico');

      const box = el('div');
      box.appendChild(el('div', 'popup-plate-title', `🌋 ${titulo}`));
      box.appendChild(el('div', 'popup-plate-sub', `Frontera tectónica (${p.Name || ''})`));
      if (p.Source) {
        box.appendChild(el('div', 'popup-row', `Fuente: ${p.Source}`));
      }
      layer.bindPopup(box);

      layer.on('mouseover', () => {
        layer.setStyle({ weight: 3.5, color: '#ffb366', opacity: 1 });
      });
      layer.on('mouseout', () => {
        placasLayer.resetStyle(layer);
      });
    },
  }).addTo(map);

  async function cargarPlacasTectonicas() {
    try {
      const res = await fetch('/data/placas.geojson');
      if (!res.ok) return;
      const data = await res.json();
      placasLayer.addData(data);
    } catch (e) {
      console.warn('No se pudo cargar la capa de placas tectónicas:', e);
    }
  }

  // Selector de capas base y geológicas
  L.control.layers(
    baseLayers,
    {
      '🌋 Placas Tectónicas': placasLayer,
    },
    { collapsed: true }
  ).addTo(map);

  // ---- Utilidades y formateo seguro ----
  const colorFor = (m) => (m >= 6.5 ? '#f0325a' : m >= 5 ? '#f97a3a' : m >= 3 ? '#f7c13c' : '#26d9a0');
  const radiusFor = (m) => Math.max(4, (m || 0) * 3);
  const fmtFecha = (iso) => (iso ? new Date(iso).toLocaleString('es-CL', { timeZone: 'UTC' }) + ' UTC' : '—');
  const fmtNum = (n, d = 1) => (typeof n === 'number' && Number.isFinite(n) ? n.toFixed(d) : '—');

  function fixMojibake(str) {
    if (!str || typeof str !== 'string' || !str.includes('Ã')) return str;
    try {
      const bytes = Uint8Array.from(str, (c) => c.charCodeAt(0));
      return new TextDecoder('utf-8').decode(bytes);
    } catch {
      return str;
    }
  }

  function timeAgo(dateString) {
    if (!dateString) return '—';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return '—';
    const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);

    if (diffSec < 0) return 'Ahora mismo';
    if (diffSec < 60) return `Hace ${Math.max(1, diffSec)} s`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `Hace ${diffMin} min`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `Hace ${diffHours} h`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return 'Ayer';
    if (diffDays < 7) return `Hace ${diffDays} d`;

    // Incluir día, mes y año para evitar ambigüedades en eventos históricos
    return date.toLocaleDateString('es-CL', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // ---- Audio y Alertas ----
  let audioCtx = null;
  let audioAlertsEnabled = localStorage.getItem('sismos_audio_alert') === 'true';

  function updateAudioButtonUI() {
    const soundIcon = $('sound-icon');
    const soundToggle = $('btn-sound-toggle');
    if (soundIcon) soundIcon.textContent = audioAlertsEnabled ? '🔔' : '🔕';
    if (soundToggle) {
      soundToggle.classList.toggle('active', audioAlertsEnabled);
      soundToggle.setAttribute('aria-pressed', String(audioAlertsEnabled));
      soundToggle.title = audioAlertsEnabled
        ? 'Alertas sonoras activas (sismos ≥ 4.5)'
        : 'Alertas sonoras desactivadas';
    }
  }

  function playNotificationSound() {
    if (!audioAlertsEnabled) return;
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      if (!audioCtx) audioCtx = new AudioContextClass();
      if (audioCtx.state === 'suspended') audioCtx.resume();

      const t = audioCtx.currentTime;
      const playNote = (freq, startTime, duration) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, startTime);
        gain.gain.setValueAtTime(0.001, startTime);
        gain.gain.exponentialRampToValueAtTime(0.2, startTime + 0.04);
        gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.start(startTime);
        osc.stop(startTime + duration);
      };
      playNote(587.33, t, 0.35);        // Re5
      playNote(880.00, t + 0.12, 0.55); // La5
    } catch (err) {
      console.warn('Audio no permitido por el navegador:', err);
    }
  }

  function showToast({ title, subtitle, lat, lon, marker }) {
    const container = $('toast-container');
    if (!container) return;

    const toast = el('div', 'toast');
    const body = el('div', 'toast-body');
    body.appendChild(el('span', 'toast-icon', '🔔'));
    const text = el('div', 'toast-text');
    text.appendChild(el('strong', null, title));
    text.appendChild(el('span', null, subtitle));
    body.appendChild(text);

    const actions = el('div', 'toast-actions');
    const btnVer = el('button', 'toast-btn', 'Ver');
    btnVer.type = 'button';
    btnVer.addEventListener('click', () => {
      map.flyTo([lat, lon], 8, { duration: 1.2 });
      if (marker) setTimeout(() => marker.openPopup(), 1250);
      toast.remove();
    });

    const btnClose = el('button', 'toast-close', '✕');
    btnClose.type = 'button';
    btnClose.addEventListener('click', () => toast.remove());

    actions.append(btnVer, btnClose);
    toast.append(body, actions);
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.4s';
      setTimeout(() => toast.remove(), 400);
    }, 8000);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Popup construido con DOM (sin innerHTML) para evitar XSS con datos de la BD.
  function buildPopup(p) {
    const box = el('div');
    box.appendChild(el('div', 'popup-mag', `M ${fmtNum(p.magnitud)} ${p.tipo_magnitud || ''}`.trim()));
    box.appendChild(el('div', null, p.referencia || 'Sin referencia'));
    if (p.pais) box.appendChild(el('div', 'popup-row', p.pais));
    box.appendChild(el('div', 'popup-row', `${fmtFecha(p.fecha_utc)} (${timeAgo(p.fecha_utc)})`));
    box.appendChild(el('div', 'popup-row', `Profundidad: ${fmtNum(p.profundidad_km)} km`));
    if (p.intensidad_mmi !== null) box.appendChild(el('div', 'popup-row', `Intensidad MMI: ${fmtNum(p.intensidad_mmi)}`));
    if (p.alerta_tsunami) box.appendChild(el('div', 'popup-row', '⚠️ Alerta de tsunami'));
    return box;
  }

  function setStatus(text, cls) {
    const s = $('status-indicator');
    s.textContent = text;
    s.className = `status ${cls || ''}`;
  }

  function toIsoUtc(value) {
    if (!value) return null;
    const d = new Date(value);
    return !isNaN(d.getTime()) ? d.toISOString() : null;
  }

  function setFechaDesdeHoy() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, '0');
    const d = String(now.getDate()).padStart(2, '0');
    const fDesde = $('f-desde');
    if (fDesde) fDesde.value = `${y}-${m}-${d}T00:00`;
  }

  // ---- Controlador de Combobox con Mini Buscador ----
  function createSearchableSelect({
    wrapId, btnId, labelId, dropdownId, searchInputId, listId, hiddenInputId,
    defaultText, placeholderText, allowCustomSearch = false, onSelect,
  }) {
    const wrap = $(wrapId);
    const btn = $(btnId);
    const label = $(labelId);
    const dropdown = $(dropdownId);
    const searchInput = $(searchInputId);
    const list = $(listId);
    const hiddenInput = $(hiddenInputId);

    let items = []; // Array of { id, nombre, count }
    let selectedId = '';

    function open() {
      document.querySelectorAll('.custom-select-dropdown').forEach((d) => {
        if (d !== dropdown) {
          d.hidden = true;
          d.classList.remove('open');
          d.previousElementSibling?.classList.remove('active');
        }
      });
      dropdown.hidden = false;
      dropdown.classList.add('open');
      btn.classList.add('active');
      btn.setAttribute('aria-expanded', 'true');
      searchInput.value = '';
      if (placeholderText) searchInput.placeholder = placeholderText;
      filterItems('');
      searchInput.focus();
    }

    function close() {
      dropdown.hidden = true;
      dropdown.classList.remove('open');
      btn.classList.remove('active');
      btn.setAttribute('aria-expanded', 'false');
    }

    function toggle() {
      if (dropdown.classList.contains('open')) close();
      else open();
    }

    function filterItems(query) {
      list.replaceChildren();
      const q = (query || '').toLowerCase().trim();
      const filtered = items.filter((it) => {
        const nom = (it.nombre || '').toLowerCase();
        const idStr = (it.id || '').toLowerCase();
        return nom.includes(q) || idStr.includes(q);
      });

      if (filtered.length === 0) {
        if (allowCustomSearch && q) {
          const customLi = el('li', 'custom-select-item', `Buscar "${query}"`);
          customLi.addEventListener('click', () => {
            select(query.trim(), `"${query.trim()}"`);
          });
          list.appendChild(customLi);
        } else {
          const empty = el('li', 'custom-select-empty', 'Sin coincidencias');
          list.appendChild(empty);
        }
        return;
      }

      filtered.forEach((it) => {
        const li = el('li', 'custom-select-item');
        if (it.id === selectedId) li.classList.add('selected');
        li.appendChild(el('span', null, it.nombre));
        if (it.count !== undefined && it.count !== null) {
          li.appendChild(el('span', 'item-count', it.count.toLocaleString('es-CL')));
        }
        li.addEventListener('click', () => {
          select(it.id, it.nombre);
        });
        list.appendChild(li);
      });
    }

    function select(id, displayName, trigger = true) {
      selectedId = id || '';
      hiddenInput.value = selectedId;
      label.textContent = selectedId ? (displayName || selectedId) : defaultText;
      close();
      if (trigger && typeof onSelect === 'function') {
        onSelect(selectedId, displayName);
      }
    }

    function setItems(newItems, newSelectedId = '', newPlaceholder = null) {
      items = newItems || [];
      selectedId = newSelectedId || '';
      hiddenInput.value = selectedId;
      if (newPlaceholder) {
        placeholderText = newPlaceholder;
        searchInput.placeholder = newPlaceholder;
      }
      const found = items.find((i) => i.id === selectedId);
      label.textContent = found ? found.nombre : defaultText;
    }

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggle();
    });

    searchInput.addEventListener('input', (e) => {
      filterItems(e.target.value);
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        close();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const first = list.querySelector('.custom-select-item');
        if (first) {
          first.click();
        } else if (allowCustomSearch && searchInput.value.trim()) {
          select(searchInput.value.trim(), `"${searchInput.value.trim()}"`);
        }
      }
    });

    document.addEventListener('click', (e) => {
      if (!wrap.contains(e.target)) {
        close();
      }
    });

    return {
      setItems,
      select,
      getValue: () => hiddenInput.value,
      reset: () => select('', defaultText, false),
    };
  }

  // ---- Instancias de Combobox para País y Región ----
  let regionesData = [];
  let subregionesData = {};

  const paisSelect = createSearchableSelect({
    wrapId: 'wrap-f-pais',
    btnId: 'btn-f-pais',
    labelId: 'label-f-pais',
    dropdownId: 'dropdown-f-pais',
    searchInputId: 'search-f-pais',
    listId: 'list-f-pais',
    hiddenInputId: 'f-pais',
    defaultText: 'Todos los países',
    placeholderText: 'Escribir país (ej. Chile)...',
    allowCustomSearch: false,
    onSelect: (paisId) => {
      actualizarRegionesParaPais(paisId);
      cargar({ autoFit: true });
    },
  });

  const regionSelect = createSearchableSelect({
    wrapId: 'wrap-f-region',
    btnId: 'btn-f-region',
    labelId: 'label-f-region',
    dropdownId: 'dropdown-f-region',
    searchInputId: 'search-f-region',
    listId: 'list-f-region',
    hiddenInputId: 'f-region',
    defaultText: 'Todas las regiones',
    placeholderText: 'Escribir región (ej. Coquimbo)...',
    allowCustomSearch: true,
    onSelect: () => {
      cargar({ autoFit: true });
    },
  });

  function actualizarRegionesParaPais(paisId) {
    regionSelect.reset();

    if (paisId === 'Chile') {
      const sub = subregionesData['Chile'] || [];
      const items = [
        { id: '', nombre: 'Todas las regiones de Chile' },
        ...sub.map((r) => ({ id: r.id, nombre: r.nombre })),
      ];
      regionSelect.setItems(items, '', 'Buscar en Chile (ej. Coquimbo, Iquique)...');
    } else if (paisId) {
      const items = [
        { id: '', nombre: `Todas las regiones de ${paisId}` },
      ];
      regionSelect.setItems(items, '', `Buscar localidad en ${paisId}...`);
    } else {
      const items = [
        { id: '', nombre: 'Todas las regiones (Global)' },
      ];
      regionSelect.setItems(items, '', 'Escribir región o localidad...');
    }
  }

  function actualizarPaisesParaContinente(continenteId) {
    const items = [
      { id: '', nombre: 'Todos los países' },
    ];

    if (continenteId) {
      const cont = regionesData.find((c) => c.id === continenteId);
      if (cont && Array.isArray(cont.paises)) {
        cont.paises.forEach((p) => {
          items.push({ id: p.id, nombre: p.nombre, count: p.eventos });
        });
      }
    } else {
      const todos = [];
      regionesData.forEach((c) => {
        if (Array.isArray(c.paises)) {
          c.paises.forEach((p) => todos.push(p));
        }
      });
      todos.sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'));
      todos.forEach((p) => {
        items.push({ id: p.id, nombre: p.nombre, count: p.eventos });
      });
    }

    paisSelect.setItems(items, '');
    actualizarRegionesParaPais('');
  }

  async function cargarRegiones() {
    try {
      const res = await fetch('/api/regiones', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      regionesData = Array.isArray(data.continentes) ? data.continentes : [];
      subregionesData = data.subregiones || {};

      const selectCont = $('f-continente');
      const optDefault = el('option', null, 'Todos los continentes');
      optDefault.value = '';
      const opts = [optDefault];

      regionesData.forEach((c) => {
        const total = c.paises.reduce((acc, p) => acc + (p.eventos || 0), 0);
        const opt = el('option', null, `${c.nombre} (${total.toLocaleString('es-CL')})`);
        opt.value = c.id;
        opts.push(opt);
      });

      selectCont.replaceChildren(...opts);
      actualizarPaisesParaContinente('');
    } catch (e) {
      console.error('Error cargando regiones');
    }
  }

  // ---- Alternar Modo de Visualización (Puntos, Agrupados, Calor) ----
  function setViewMode(mode) {
    currentViewMode = mode;
    document.querySelectorAll('.btn-mode').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.mode === mode);
    });

    if (map.hasLayer(sismosLayer)) map.removeLayer(sismosLayer);
    if (map.hasLayer(sismosCluster)) map.removeLayer(sismosCluster);
    if (heatLayer && map.hasLayer(heatLayer)) map.removeLayer(heatLayer);

    if (mode === 'cluster') {
      map.addLayer(sismosCluster);
    } else if (mode === 'calor') {
      if (heatLayer) map.addLayer(heatLayer);
    } else {
      map.addLayer(sismosLayer);
    }
  }

  // ---- Render de sismos en mapa y estadísticas ----
  function render(fc, autoFit = false, opts = {}) {
    sismosLayer.clearLayers();
    sismosCluster.clearLayers();
    if (heatLayer && map.hasLayer(heatLayer)) {
      map.removeLayer(heatLayer);
    }

    const list = $('event-list');
    list.replaceChildren();

    const feats = Array.isArray(fc.features) ? fc.features : [];
    let sumMag = 0, sumProf = 0, maxMag = null;
    let c1 = 0, c2 = 0, c3 = 0, c4 = 0;
    const coordsList = [];
    const heatPoints = [];
    const markerMap = new Map();
    currentGeoJson = fc;

    feats.forEach((f) => {
      const [lon, lat] = f.geometry.coordinates;
      const p = f.properties;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

      coordsList.push([lat, lon]);
      heatPoints.push([lat, lon, Math.max(0.2, (p.magnitud || 3) / 8)]);

      const mag = p.magnitud || 0;
      if (mag < 3.0) c1++;
      else if (mag < 5.0) c2++;
      else if (mag < 6.5) c3++;
      else c4++;

      const marker = L.circleMarker([lat, lon], {
        radius: radiusFor(p.magnitud), color: colorFor(p.magnitud),
        fillColor: colorFor(p.magnitud), fillOpacity: 0.55, weight: 1,
      }).bindPopup(() => buildPopup(p));
      marker.on('click', () => highlightEventInList(p.id));
      sismosLayer.addLayer(marker);

      const clusterMarker = L.circleMarker([lat, lon], {
        radius: radiusFor(p.magnitud), color: colorFor(p.magnitud),
        fillColor: colorFor(p.magnitud), fillOpacity: 0.55, weight: 1,
      }).bindPopup(() => buildPopup(p));
      clusterMarker.on('click', () => highlightEventInList(p.id));
      sismosCluster.addLayer(clusterMarker);

      if (p.id) markerMap.set(p.id, marker);

      sumMag += p.magnitud || 0;
      sumProf += p.profundidad_km || 0;
      if (maxMag === null || p.magnitud > maxMag) maxMag = p.magnitud;
    });

    // Actualizar histograma de magnitudes
    const maxHisto = Math.max(1, c1, c2, c3, c4);
    $('hbar-1').style.width = `${(c1 / maxHisto) * 100}%`;
    $('hcnt-1').textContent = c1.toLocaleString('es-CL');
    $('hbar-2').style.width = `${(c2 / maxHisto) * 100}%`;
    $('hcnt-2').textContent = c2.toLocaleString('es-CL');
    $('hbar-3').style.width = `${(c3 / maxHisto) * 100}%`;
    $('hcnt-3').textContent = c3.toLocaleString('es-CL');
    $('hbar-4').style.width = `${(c4 / maxHisto) * 100}%`;
    $('hcnt-4').textContent = c4.toLocaleString('es-CL');

    if (window.L && L.heatLayer && heatPoints.length > 0) {
      heatLayer = L.heatLayer(heatPoints, {
        radius: 24, blur: 16, maxZoom: 10, max: 0.85,
        gradient: { 0.2: '#26d9a0', 0.4: '#f7c13c', 0.7: '#f97a3a', 1.0: '#f0325a' },
      });
    }

    // Aplicar la capa activa según el modo actual
    if (currentViewMode === 'cluster') {
      if (!map.hasLayer(sismosCluster)) map.addLayer(sismosCluster);
    } else if (currentViewMode === 'calor') {
      if (heatLayer && !map.hasLayer(heatLayer)) map.addLayer(heatLayer);
    } else {
      if (!map.hasLayer(sismosLayer)) map.addLayer(sismosLayer);
    }

    // Detectar sismos nuevos para alerta sonora y toast SOLO en auto-refresco y si ocurrió recientemente (< 24h)
    if (opts.isAuto && hasInitialLoaded) {
      const now = Date.now();
      const newSignificant = feats.filter((f) => {
        if (knownEventIds.has(f.properties.id)) return false;
        if ((f.properties.magnitud || 0) < 4.5) return false;
        const ageHours = (now - new Date(f.properties.fecha_utc).getTime()) / (1000 * 3600);
        return ageHours <= 24;
      });
      if (newSignificant.length > 0) {
        const topNew = newSignificant[0];
        const [tlon, tlat] = topNew.geometry.coordinates;
        const tp = topNew.properties;
        showToast({
          title: `¡Nuevo sismo M ${fmtNum(tp.magnitud)}!`,
          subtitle: `${tp.referencia || 'Sin referencia'} (${timeAgo(tp.fecha_utc)})`,
          lat: tlat,
          lon: tlon,
          marker: markerMap.get(tp.id),
        });
        playNotificationSound();
      }
    }
    knownEventIds = new Set(feats.map((f) => f.properties.id));
    hasInitialLoaded = true;

    // Actualizar tarjeta de Último Sismo Registrado
    const latestFeat = feats[0];
    latestFeatRef = latestFeat || null;
    const latestCard = $('latest-card');
    if (latestFeat && latestCard) {
      latestCard.style.display = 'flex';
      const lp = latestFeat.properties;
      const [llon, llat] = latestFeat.geometry.coordinates;

      $('latest-time-ago').textContent = timeAgo(lp.fecha_utc);
      $('latest-time-ago').title = fmtFecha(lp.fecha_utc);
      $('latest-mag-val').textContent = fmtNum(lp.magnitud);
      $('latest-mag-unit').textContent = lp.tipo_magnitud || '';
      const magPill = $('latest-mag-pill');
      if (magPill) magPill.style.background = colorFor(lp.magnitud);
      $('latest-ref').textContent = lp.referencia || 'Sin referencia';
      $('latest-country').textContent = lp.pais || '';
      $('latest-depth').textContent = `Prof: ${fmtNum(lp.profundidad_km)} km`;

      const locateLatest = () => {
        map.flyTo([llat, llon], 8, { duration: 1.2 });
        const m = markerMap.get(lp.id);
        if (m) setTimeout(() => m.openPopup(), 1250);
        if (isMobile()) closeMobilePanels();
      };
      $('btn-goto-latest').onclick = locateLatest;
      latestCard.onclick = (e) => {
        if (e.target.id !== 'btn-goto-latest') locateLatest();
      };
    } else if (latestCard) {
      latestCard.style.display = 'none';
    }

    feats.slice(0, 60).forEach((f) => {
      const p = f.properties;
      const [lon, lat] = f.geometry.coordinates;
      const li = el('li', 'event-item');
      li.dataset.id = p.id || '';
      const badge = el('span', 'event-badge', fmtNum(p.magnitud));
      badge.style.background = colorFor(p.magnitud);
      const info = el('div', 'event-info');
      info.appendChild(el('strong', null, p.referencia || 'Sin referencia'));
      const timeSpan = el('span', 'event-time', timeAgo(p.fecha_utc));
      timeSpan.title = fmtFecha(p.fecha_utc);
      timeSpan.dataset.utc = p.fecha_utc || '';
      info.appendChild(timeSpan);
      li.append(badge, info);
      li.addEventListener('click', () => {
        document.querySelectorAll('.event-item').forEach((it) => it.classList.remove('active'));
        li.classList.add('active');
        map.flyTo([lat, lon], 8, { duration: 1.2 });
        const m = markerMap.get(p.id);
        if (m) setTimeout(() => m.openPopup(), 1250);
        if (isMobile()) closeMobilePanels();
      });
      list.appendChild(li);
    });

    const n = feats.length;
    $('s-total').textContent = n.toLocaleString('es-CL');
    $('s-max').textContent = n ? fmtNum(maxMag) : '–';
    $('s-avg').textContent = n ? fmtNum(sumMag / n) : '–';
    $('s-prof').textContent = n ? `${fmtNum(sumProf / n, 0)} km` : '–';
    const tabBadge = $('tab-badge-count');
    if (tabBadge) tabBadge.textContent = n.toLocaleString('es-CL');

    if (autoFit && coordsList.length > 0) {
      map.fitBounds(L.latLngBounds(coordsList), { maxZoom: 7, padding: [30, 30] });
    }
  }

  function highlightEventInList(id) {
    if (!id) return;
    if (layoutEl.classList.contains('stats-collapsed')) {
      layoutEl.classList.remove('stats-collapsed');
      btnToggleStats?.classList.add('active');
      btnToggleStats?.setAttribute('aria-expanded', 'true');
      invalidateMapDelayed();
    }
    switchStatsTab('lista');
    document.querySelectorAll('.event-item').forEach((it) => {
      if (it.dataset.id === id) {
        it.classList.add('active');
        it.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      } else {
        it.classList.remove('active');
      }
    });
  }

  let currentGeoJson = null;

  let knownEventIds = new Set();
  let hasInitialLoaded = false;
  let latestFeatRef = null;

  // ---- Carga de datos ----
  async function cargar(opts = {}) {
    const params = new URLSearchParams({
      mag_min: $('f-mag-min').value,
      prof_max: $('f-prof-max').value,
      limit: $('f-limit').value,
    });

    if (opts.sismoId) params.set('sismo_id', opts.sismoId);
    if (opts.bbox) params.set('bbox', opts.bbox);

    const contVal = $('f-continente').value;
    const paisVal = paisSelect.getValue();
    const regionVal = regionSelect.getValue();

    if (contVal) params.set('continente', contVal);
    if (paisVal) params.set('pais', paisVal);
    if (regionVal) params.set('region', regionVal);

    const desde = toIsoUtc($('f-desde').value);
    const hasta = toIsoUtc($('f-hasta').value);
    if (desde) params.set('desde', desde);
    if (hasta) params.set('hasta', hasta);

    if (!opts.isAuto) setStatus('Cargando…');
    try {
      const res = await fetch(`/api/sismos?${params}`, { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (Array.isArray(data.features)) {
        data.features.forEach((f) => {
          if (f && f.properties && f.properties.referencia) {
            f.properties.referencia = fixMojibake(f.properties.referencia);
          }
        });
      }
      render(data, opts.autoFit, opts);
      setStatus(`Actualizado ${new Date().toLocaleTimeString('es-CL')}`, 'ok');

      if (opts.sismoId && data.features && data.features.length > 0) {
        const [lon, lat] = data.features[0].geometry.coordinates;
        map.flyTo([lat, lon], 8, { duration: 1.2 });
      }
    } catch (e) {
      console.error('Error cargando sismos');
      setStatus('Error al cargar datos', 'err');
    }
  }

  // ---- Pestañas del Panel de Monitoreo ----
  const tabBtnResumen = $('tab-btn-resumen');
  const tabBtnLista = $('tab-btn-lista');
  const paneResumen = $('pane-resumen');
  const paneLista = $('pane-lista');

  function switchStatsTab(tabName) {
    if (tabName === 'lista') {
      tabBtnLista?.classList.add('active');
      tabBtnLista?.setAttribute('aria-selected', 'true');
      tabBtnResumen?.classList.remove('active');
      tabBtnResumen?.setAttribute('aria-selected', 'false');
      paneLista?.classList.add('active');
      paneResumen?.classList.remove('active');
    } else {
      tabBtnResumen?.classList.add('active');
      tabBtnResumen?.setAttribute('aria-selected', 'true');
      tabBtnLista?.classList.remove('active');
      tabBtnLista?.setAttribute('aria-selected', 'false');
      paneResumen?.classList.add('active');
      paneLista?.classList.remove('active');
    }
  }

  tabBtnResumen?.addEventListener('click', () => switchStatsTab('resumen'));
  tabBtnLista?.addEventListener('click', () => switchStatsTab('lista'));

  // ---- Pestañas del Panel de Filtros ----
  const tabBtnFiltrosCriterios = $('tab-btn-filtros-criterios');
  const tabBtnFiltrosCapas = $('tab-btn-filtros-capas');
  const paneFiltrosCriterios = $('pane-filtros-criterios');
  const paneFiltrosCapas = $('pane-filtros-capas');

  function switchFiltrosTab(tabName) {
    if (tabName === 'capas') {
      tabBtnFiltrosCapas?.classList.add('active');
      tabBtnFiltrosCapas?.setAttribute('aria-selected', 'true');
      tabBtnFiltrosCriterios?.classList.remove('active');
      tabBtnFiltrosCriterios?.setAttribute('aria-selected', 'false');
      paneFiltrosCapas?.classList.add('active');
      paneFiltrosCriterios?.classList.remove('active');
    } else {
      tabBtnFiltrosCriterios?.classList.add('active');
      tabBtnFiltrosCriterios?.setAttribute('aria-selected', 'true');
      tabBtnFiltrosCapas?.classList.remove('active');
      tabBtnFiltrosCapas?.setAttribute('aria-selected', 'false');
      paneFiltrosCriterios?.classList.add('active');
      paneFiltrosCapas?.classList.remove('active');
    }
  }

  tabBtnFiltrosCriterios?.addEventListener('click', () => switchFiltrosTab('criterios'));
  tabBtnFiltrosCapas?.addEventListener('click', () => switchFiltrosTab('capas'));

  // Inicialización explícita de pestañas al cargar
  switchFiltrosTab('criterios');
  switchStatsTab('resumen');

  // ---- Manejo de Paneles Colapsables y Responsivos ----
  const layoutEl = document.querySelector('.layout');
  const panelFiltros = $('panel-filtros');
  const panelStats = $('panel-stats');
  const btnToggleFiltros = $('btn-toggle-filtros');
  const btnToggleStats = $('btn-toggle-stats');
  const btnCloseFiltros = $('btn-close-filtros');
  const btnCloseStats = $('btn-close-stats');
  const backdrop = $('panel-backdrop');

  const isMobile = () => window.innerWidth <= 1024;

  // Inicialización inteligente de paneles flotantes según resolución
  const storedFiltros = localStorage.getItem('sismos_panel_filtros');
  const storedStats = localStorage.getItem('sismos_panel_stats');

  if (!isMobile()) {
    if (storedFiltros === 'open') {
      layoutEl.classList.remove('filtros-collapsed');
      btnToggleFiltros?.classList.add('active');
      btnToggleFiltros?.setAttribute('aria-expanded', 'true');
    } else if (storedFiltros === 'closed' || window.innerWidth < 1440) {
      // En laptops o si se cerró expresamente, iniciar filtros colapsado para que el mapa respire
      layoutEl.classList.add('filtros-collapsed');
      btnToggleFiltros?.classList.remove('active');
      btnToggleFiltros?.setAttribute('aria-expanded', 'false');
    }

    if (storedStats === 'closed') {
      layoutEl.classList.add('stats-collapsed');
      btnToggleStats?.classList.remove('active');
      btnToggleStats?.setAttribute('aria-expanded', 'false');
    } else {
      layoutEl.classList.remove('stats-collapsed');
      btnToggleStats?.classList.add('active');
      btnToggleStats?.setAttribute('aria-expanded', 'true');
    }
  }

  function invalidateMapDelayed() {
    setTimeout(() => map.invalidateSize(), 360);
  }

  function toggleFiltros() {
    if (isMobile()) {
      const isOpen = panelFiltros.classList.toggle('mobile-open');
      if (isOpen) {
        panelStats.classList.remove('mobile-open');
        btnToggleStats?.classList.remove('active');
        backdrop.classList.add('active');
        btnToggleFiltros?.classList.add('active');
      } else {
        backdrop.classList.remove('active');
        btnToggleFiltros?.classList.remove('active');
      }
    } else {
      const isCollapsed = layoutEl.classList.toggle('filtros-collapsed');
      btnToggleFiltros?.classList.toggle('active', !isCollapsed);
      btnToggleFiltros?.setAttribute('aria-expanded', String(!isCollapsed));
      localStorage.setItem('sismos_panel_filtros', isCollapsed ? 'closed' : 'open');
      invalidateMapDelayed();
    }
  }

  function toggleStats() {
    if (isMobile()) {
      const isOpen = panelStats.classList.toggle('mobile-open');
      if (isOpen) {
        panelFiltros.classList.remove('mobile-open');
        btnToggleFiltros?.classList.remove('active');
        backdrop.classList.add('active');
        btnToggleStats?.classList.add('active');
      } else {
        backdrop.classList.remove('active');
        btnToggleStats?.classList.remove('active');
      }
    } else {
      const isCollapsed = layoutEl.classList.toggle('stats-collapsed');
      btnToggleStats?.classList.toggle('active', !isCollapsed);
      btnToggleStats?.setAttribute('aria-expanded', String(!isCollapsed));
      localStorage.setItem('sismos_panel_stats', isCollapsed ? 'closed' : 'open');
      invalidateMapDelayed();
    }
  }

  function closeMobilePanels() {
    panelFiltros.classList.remove('mobile-open');
    panelStats.classList.remove('mobile-open');
    backdrop.classList.remove('active');
    btnToggleFiltros?.classList.remove('active');
    btnToggleStats?.classList.remove('active');
    setTimeout(() => map.invalidateSize(), 150);
  }

  function closeFiltros() {
    if (isMobile()) {
      closeMobilePanels();
    } else {
      layoutEl.classList.add('filtros-collapsed');
      btnToggleFiltros?.classList.remove('active');
      btnToggleFiltros?.setAttribute('aria-expanded', 'false');
      localStorage.setItem('sismos_panel_filtros', 'closed');
      invalidateMapDelayed();
    }
  }

  function closeStats() {
    if (isMobile()) {
      closeMobilePanels();
    } else {
      layoutEl.classList.add('stats-collapsed');
      btnToggleStats?.classList.remove('active');
      btnToggleStats?.setAttribute('aria-expanded', 'false');
      localStorage.setItem('sismos_panel_stats', 'closed');
      invalidateMapDelayed();
    }
  }

  btnToggleFiltros?.addEventListener('click', toggleFiltros);
  btnToggleStats?.addEventListener('click', toggleStats);
  btnCloseFiltros?.addEventListener('click', closeFiltros);
  btnCloseStats?.addEventListener('click', closeStats);
  backdrop?.addEventListener('click', closeMobilePanels);

  window.addEventListener('resize', () => {
    map.invalidateSize();
    if (!isMobile()) {
      panelFiltros.classList.remove('mobile-open');
      panelStats.classList.remove('mobile-open');
      backdrop.classList.remove('active');
      btnToggleFiltros?.classList.toggle('active', !layoutEl.classList.contains('filtros-collapsed'));
      btnToggleStats?.classList.toggle('active', !layoutEl.classList.contains('stats-collapsed'));
    }
  });

  // ---- Eventos UI ----
  $('f-continente').addEventListener('change', (e) => {
    const cid = e.target.value;
    actualizarPaisesParaContinente(cid);
    if (cid) {
      const cont = regionesData.find((c) => c.id === cid);
      if (cont && cont.center) {
        map.flyTo(cont.center, cont.zoom || 3, { duration: 1.2 });
      }
    } else {
      map.flyTo(INITIAL_CENTER, INITIAL_ZOOM, { duration: 1.2 });
    }
    cargar({ autoFit: false });
  });

  function updatePresetButtons(val) {
    document.querySelectorAll('.btn-preset').forEach((btn) => {
      const pMag = parseFloat(btn.dataset.mag);
      btn.classList.toggle('active', Math.abs(pMag - val) < 0.05);
    });
  }

  document.querySelectorAll('.btn-preset').forEach((btn) => {
    btn.addEventListener('click', () => {
      const val = parseFloat(btn.dataset.mag) || 0;
      $('f-mag-min').value = val;
      $('o-mag-min').textContent = val === 0 ? 'Todas' : val.toFixed(1);
      updatePresetButtons(val);
    });
  });

  $('f-mag-min').addEventListener('input', (e) => {
    const val = parseFloat(e.target.value);
    $('o-mag-min').textContent = val === 0 ? 'Todas' : val.toFixed(1);
    updatePresetButtons(val);
  });

  $('f-prof-max').addEventListener('input', (e) => {
    $('o-prof-max').textContent = `${e.target.value} km`;
  });

  $('filtros-form').addEventListener('submit', (e) => {
    e.preventDefault();
    cargar();
    if (isMobile()) closeMobilePanels();
  });

  $('btn-reset').addEventListener('click', () => {
    $('filtros-form').reset();
    setFechaDesdeHoy();
    $('o-mag-min').textContent = '2.5';
    $('o-prof-max').textContent = '700 km';
    updatePresetButtons(2.5);
    actualizarPaisesParaContinente('');
    map.flyTo(INITIAL_CENTER, INITIAL_ZOOM, { duration: 1.2 });
    cargar();
    if (isMobile()) closeMobilePanels();
  });

  // ---- Ciclo de Polling y Cuenta Regresiva (Tiempo Real) ----
  let pollIntervalSec = 300; // 5 minutos por defecto (sincronizado con ingesta de la BD)
  let remainingSec = pollIntervalSec;
  let timerId = null;

  function formatTime(s) {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec < 10 ? '0' : ''}${sec}`;
  }

  function updateTimerDisplay() {
    const liveText = $('live-text');
    const livePill = $('live-pill');
    if (!liveText || !livePill) return;

    if (pollIntervalSec === 0) {
      liveText.textContent = 'Pausa';
      livePill.classList.add('paused');
    } else {
      liveText.textContent = formatTime(remainingSec);
      livePill.classList.remove('paused');
    }
  }

  function resetCountdown() {
    remainingSec = pollIntervalSec;
    updateTimerDisplay();
  }

  function startCountdown() {
    if (timerId) clearInterval(timerId);
    timerId = setInterval(() => {
      if (pollIntervalSec <= 0) return;
      remainingSec--;
      if (remainingSec <= 0) {
        resetCountdown();
        cargar({ autoFit: false, isAuto: true });
      } else {
        updateTimerDisplay();
      }
    }, 1000);
  }

  $('select-intervalo')?.addEventListener('change', (e) => {
    pollIntervalSec = parseInt(e.target.value, 10) || 0;
    resetCountdown();
    if (pollIntervalSec > 0) startCountdown();
  });

  $('btn-refresh')?.addEventListener('click', async () => {
    const icon = $('icon-refresh');
    icon?.classList.add('spinning');
    resetCountdown();
    await cargar({ autoFit: false });
    setTimeout(() => icon?.classList.remove('spinning'), 700);
  });

  $('btn-sound-toggle')?.addEventListener('click', () => {
    audioAlertsEnabled = !audioAlertsEnabled;
    localStorage.setItem('sismos_audio_alert', String(audioAlertsEnabled));
    updateAudioButtonUI();
    if (audioAlertsEnabled) playNotificationSound();
  });

  // Actualizador periódico de tiempos relativos (cada 30s)
  setInterval(() => {
    document.querySelectorAll('.event-time[data-utc]').forEach((el) => {
      const utc = el.dataset.utc;
      if (utc) el.textContent = timeAgo(utc);
    });
    if (latestFeatRef && latestFeatRef.properties) {
      const el = $('latest-time-ago');
      if (el) el.textContent = timeAgo(latestFeatRef.properties.fecha_utc);
    }
  }, 30000);

  // ---- Eventos de Modos de Visualización y Capas Geológicas ----
  document.querySelectorAll('.btn-mode').forEach((btn) => {
    btn.addEventListener('click', () => {
      setViewMode(btn.dataset.mode);
    });
  });

  $('check-placas')?.addEventListener('change', (e) => {
    if (e.target.checked) {
      if (!map.hasLayer(placasLayer)) map.addLayer(placasLayer);
    } else {
      if (map.hasLayer(placasLayer)) map.removeLayer(placasLayer);
    }
  });

  // ---- Herramientas de Exportación y Compartir (Fase 4) ----
  function exportCSV() {
    if (!currentGeoJson || !currentGeoJson.features || currentGeoJson.features.length === 0) {
      showToast({ title: 'Exportar CSV', subtitle: 'No hay eventos sísmicos en la vista actual' });
      return;
    }
    const headers = ['id', 'fecha_utc', 'magnitud', 'tipo_magnitud', 'profundidad_km', 'lugar', 'pais', 'latitud', 'longitud'];
    const rows = currentGeoJson.features.map((f) => {
      const p = f.properties || {};
      const [lon, lat] = f.geometry.coordinates || [null, null];
      return [
        JSON.stringify(p.id || ''),
        JSON.stringify(p.fecha_utc || ''),
        p.magnitud ?? '',
        JSON.stringify(p.tipo_magnitud || ''),
        p.profundidad_km ?? '',
        JSON.stringify(p.referencia || ''),
        JSON.stringify(p.pais || ''),
        lat ?? '',
        lon ?? '',
      ].join(',');
    });
    const csvContent = [headers.join(','), ...rows].join('\r\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sismos_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast({ title: 'Exportación completada', subtitle: `Se descargaron ${currentGeoJson.features.length} eventos en CSV` });
  }

  function exportGeoJSON() {
    if (!currentGeoJson || !currentGeoJson.features || currentGeoJson.features.length === 0) {
      showToast({ title: 'Exportar GeoJSON', subtitle: 'No hay eventos sísmicos en la vista actual' });
      return;
    }
    const blob = new Blob([JSON.stringify(currentGeoJson, null, 2)], { type: 'application/geo+json;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `sismos_${new Date().toISOString().slice(0, 10)}.geojson`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast({ title: 'Exportación completada', subtitle: `Se descargaron ${currentGeoJson.features.length} eventos en GeoJSON` });
  }

  function shareView() {
    const params = new URLSearchParams();
    if ($('f-continente').value) params.set('continente', $('f-continente').value);
    if (paisSelect.getValue()) params.set('pais', paisSelect.getValue());
    if (regionSelect.getValue()) params.set('region', regionSelect.getValue());
    if (parseFloat($('f-mag-min').value) > 0) params.set('mag_min', $('f-mag-min').value);
    if (parseFloat($('f-prof-max').value) < 700) params.set('prof_max', $('f-prof-max').value);
    if ($('f-desde').value) params.set('desde', toIsoUtc($('f-desde').value));
    if ($('f-hasta').value) params.set('hasta', toIsoUtc($('f-hasta').value));

    const center = map.getCenter();
    params.set('lat', center.lat.toFixed(4));
    params.set('lon', center.lng.toFixed(4));
    params.set('zoom', map.getZoom());

    const shareUrl = `${window.location.origin}${window.location.pathname}?${params.toString()}`;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(shareUrl).then(() => {
        showToast({ title: 'Enlace copiado', subtitle: 'La URL con tus filtros y vista actual está en el portapapeles' });
      }).catch(() => prompt('Copia este enlace para compartir:', shareUrl));
    } else {
      prompt('Copia este enlace para compartir:', shareUrl);
    }
  }

  $('btn-export-csv')?.addEventListener('click', exportCSV);
  $('btn-export-geojson')?.addEventListener('click', exportGeoJSON);
  $('btn-share-url')?.addEventListener('click', shareView);

  // ---- Búsqueda por Área Visible del Mapa ----
  const btnSearchArea = $('btn-search-area');
  let hasMovedMap = false;

  map.on('moveend', () => {
    if (hasInitialLoaded && btnSearchArea) {
      hasMovedMap = true;
      btnSearchArea.style.display = 'inline-flex';
    }
  });

  btnSearchArea?.addEventListener('click', () => {
    const b = map.getBounds();
    const bbox = `${b.getWest().toFixed(4)},${b.getSouth().toFixed(4)},${b.getEast().toFixed(4)},${b.getNorth().toFixed(4)}`;
    btnSearchArea.style.display = 'none';
    cargar({ bbox, autoFit: false });
  });

  // ---- Filtro rápido desde Histograma ----
  document.querySelectorAll('.histo-row').forEach((row) => {
    row.addEventListener('click', () => {
      const range = row.dataset.range;
      if (range === 'm1') {
        $('f-mag-min').value = 0;
        $('o-mag-min').textContent = '0';
      } else if (range === 'm2') {
        $('f-mag-min').value = 3.0;
        $('o-mag-min').textContent = '3.0';
      } else if (range === 'm3') {
        $('f-mag-min').value = 5.0;
        $('o-mag-min').textContent = '5.0';
      } else if (range === 'm4') {
        $('f-mag-min').value = 6.5;
        $('o-mag-min').textContent = '6.5';
      }
      cargar();
    });
  });

  function applyUrlParameters() {
    const params = new URLSearchParams(window.location.search);
    const sismoId = params.get('sismo') || params.get('sismo_id');
    if (sismoId) {
      cargar({ sismoId, autoFit: true });
      return true;
    }

    if (params.get('desde')) {
      $('f-desde').value = params.get('desde');
    } else {
      setFechaDesdeHoy();
    }

    if (params.get('hasta')) {
      $('f-hasta').value = params.get('hasta');
    }

    if (params.get('mag_min')) {
      $('f-mag-min').value = params.get('mag_min');
      $('o-mag-min').textContent = params.get('mag_min');
      updatePresetButtons(parseFloat(params.get('mag_min')) || 0);
    }
    if (params.get('prof_max')) {
      $('f-prof-max').value = params.get('prof_max');
      $('o-prof-max').textContent = `${params.get('prof_max')} km`;
    }
    if (params.get('lat') && params.get('lon')) {
      const lat = parseFloat(params.get('lat'));
      const lon = parseFloat(params.get('lon'));
      const z = parseInt(params.get('zoom'), 10) || 5;
      map.setView([lat, lon], z);
    }
    return false;
  }

  // Inicialización
  updateAudioButtonUI();
  updateTimerDisplay();
  cargarPlacasTectonicas();
  cargarRegiones();
  const hadCustomSismo = applyUrlParameters();
  if (!hadCustomSismo) cargar();
  startCountdown();
})();
