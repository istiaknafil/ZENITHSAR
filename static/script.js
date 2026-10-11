(() => {
  const $ = (id) => document.getElementById(id);
  const status = (message) => { $('toolbarStatus').textContent = message; $('processingStatus').textContent = message; };
  const apiBase = window.EARTH_CHANGE_CONFIG?.apiBase || '';
  let observations = [];

  const baseLayer = new ol.layer.Tile({
    source: new ol.source.OSM({ attributions: '© OpenStreetMap contributors' }),
    properties: { key: 'basemap', name: 'Basemap', kind: 'basemap' },
  });
  const studySource = new ol.source.Vector();
  const studyLayer = new ol.layer.Vector({
    source: studySource,
    style: new ol.style.Style({
      stroke: new ol.style.Stroke({ color: '#76d0ae', width: 2 }),
      fill: new ol.style.Fill({ color: 'rgba(118,208,174,.12)' }),
    }),
    properties: { key: 'study', name: 'DEMO DATA: study area', kind: 'demo' },
  });
  const map = new ol.Map({
    target: 'map',
    layers: [baseLayer, studyLayer],
    view: new ol.View({ center: ol.proj.fromLonLat([-98, 38]), zoom: 3 }),
    controls: ol.control.defaults.defaults({ attribution: false, zoom: false }),
  });
  const drawLayer = new ol.layer.Vector({ source: studySource });
  let drawInteraction = null;
  let selectedLayer = baseLayer;
  let localLayer = null;

  const layers = [
    { layer: baseLayer, visible: true },
    { layer: studyLayer, visible: true },
  ];

  function setActiveLayer(layer) {
    selectedLayer = layer;
    $('activeLayerStatus').textContent = `Active layer: ${layer.get('name') || 'unnamed'}`;
    $('selectedLayerBadge').textContent = layer.get('name') || 'Unnamed';
    const source = layer.getSource?.();
    const metadata = $('metadataList');
    const sourceDescription = layer.get('kind') === 'demo' ? 'Local demo geometry (not an observation)' : 'OpenStreetMap XYZ tiles';
    metadata.innerHTML = `<dt>Source</dt><dd>${sourceDescription}</dd><dt>CRS</dt><dd>${source?.getProjection?.()?.getCode?.() || 'EPSG:3857'}</dd><dt>Dimensions</dt><dd>${source?.getFeatures ? `${source.getFeatures().length} feature(s)` : 'Tile source'}</dd><dt>Pixel spacing</dt><dd>Not available</dd><dt>NoData</dt><dd>Not available</dd>`;
    document.querySelectorAll('.layer-row').forEach((row) => row.classList.toggle('selected', row.dataset.key === layer.get('key')));
  }

  function renderLayers() {
    const list = $('layerList');
    list.innerHTML = '';
    layers.forEach(({ layer }) => {
      const row = document.createElement('div');
      row.className = 'layer-row';
      row.dataset.key = layer.get('key');
      row.innerHTML = `<input type="checkbox" ${layer.getVisible() ? 'checked' : ''} aria-label="Toggle ${layer.get('name')}"><span class="layer-swatch ${layer.get('kind') || ''}"></span><label>${layer.get('name')}</label><button class="layer-action" title="Zoom to layer">⌖</button>`;
      row.querySelector('input').addEventListener('change', (event) => layer.setVisible(event.target.checked));
      row.querySelector('label').addEventListener('click', () => setActiveLayer(layer));
      row.querySelector('.layer-action').addEventListener('click', () => {
        const extent = layer.getSource()?.getExtent?.();
        if (extent && !ol.extent.isEmpty(extent)) map.getView().fit(extent, { padding: [50, 50, 50, 50], duration: 300 });
      });
      list.appendChild(row);
    });
    if (selectedLayer) setActiveLayer(selectedLayer);
  }

  function addDerivedChangeLayer(result) {
    const georeferencing = result.before?.georeferencing;
    const bounds = georeferencing?.bounds;
    const projectionAttribute = georeferencing?.projection_attributes || {};
    const epsgValue = Object.entries(projectionAttribute).find(([key]) => key.toLowerCase().includes('epsg'))?.[1];
    const epsg = Number(epsgValue);
    if (!bounds || !Number.isFinite(epsg)) {
      return false;
    }
    const sourceProjection = `EPSG:${epsg}`;
    let extent;
    try {
      extent = ol.proj.transformExtent(
        [bounds.xmin, bounds.ymin, bounds.xmax, bounds.ymax],
        sourceProjection,
        'EPSG:3857',
      );
    } catch (error) {
      console.warn('Could not transform derived NISAR extent', error);
      return false;
    }
    const key = `change-${result.before_product_id}-${result.after_product_id}`;
    const existing = layers.find(({ layer }) => layer.get('key') === key);
    if (existing) {
      existing.layer.setVisible(true);
      setActiveLayer(existing.layer);
      return true;
    }
    const imageLayer = new ol.layer.Image({
      source: new ol.source.ImageStatic({
        url: `${apiBase}/${result.preview}`,
        projection: 'EPSG:3857',
        imageExtent: extent,
      }),
      opacity: 0.8,
      properties: {
        key,
        name: `NISAR candidate change: ${result.before_product_id} → ${result.after_product_id}`,
        kind: 'change',
        metadata: result,
      },
    });
    map.addLayer(imageLayer);
    layers.push({ layer: imageLayer, visible: true });
    renderLayers();
    map.getView().fit(extent, { padding: [50, 50, 50, 50], duration: 300 });
    return true;
  }
  renderLayers();

  function activateTab(tab) {
    document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
    document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.remove('active'));
    if (tab === 'catalog' || tab === 'layers') {
      document.querySelector(`.left-sidebar .tab-panel[data-panel="${tab}"]`).classList.add('active');
      document.querySelector('.right-sidebar .tab-panel[data-panel="analysis"]').classList.add('active');
    } else if (tab === 'analysis' || tab === 'metadata') {
      document.querySelector('.left-sidebar .tab-panel[data-panel="layers"]').classList.add('active');
      document.querySelector(`.right-sidebar .tab-panel[data-panel="${tab}"]`).classList.add('active');
    }
    if (tab === 'comparison') $('comparisonDrawer').hidden = false;
  }
  document.querySelectorAll('[data-tab]').forEach((button) => button.addEventListener('click', () => activateTab(button.dataset.tab)));
  $('closeComparisonBtn').addEventListener('click', () => { $('comparisonDrawer').hidden = true; });
  $('aboutBtn').addEventListener('click', () => window.alert('EarthChange AI is an early-stage GIS and image-comparison prototype. Live NISAR data require an optional Earthdata-enabled backend.'));
  $('newProjectBtn').addEventListener('click', () => { studySource.clear(); if (localLayer) { map.removeLayer(localLayer); localLayer = null; } renderLayers(); status('New local project'); });
  $('saveProjectBtn').addEventListener('click', () => status('Project saving is local-only scaffolding; no server is configured.'));
  function catalogRequest() {
    const body = { collection: $('collectionSelect').value, count: 20 };
    if ($('startDateInput').value) body.start = `${$('startDateInput').value}T00:00:00Z`;
    if ($('endDateInput').value) body.end = `${$('endDateInput').value}T23:59:59Z`;
    if ($('bboxInput').value.trim()) {
      const values = $('bboxInput').value.split(',').map(Number);
      if (values.length !== 4 || values.some((value) => !Number.isFinite(value))) throw new Error('Bounding box must be west,south,east,north');
      body.bbox = { west: values[0], south: values[1], east: values[2], north: values[3] };
    }
    return body;
  }

  function renderObservationList() {
    const list = $('observationList');
    if (!observations.length) {
      list.innerHTML = 'No observations matched this filter.<br /><span>Try a wider date range or location.</span>';
      return;
    }
    list.className = 'observation-list';
    list.innerHTML = '';
    observations.forEach((observation) => {
      const row = document.createElement('button');
      row.className = 'observation-row';
      row.textContent = `${observation.acquisition_start || 'Unknown date'} · ${observation.product_id}`;
      row.title = `${observation.collection} · ${observation.size_mb?.toFixed?.(1) || 'unknown'} MB`;
      row.addEventListener('click', () => {
        const earlier = $('earlierSelect');
        const later = $('laterSelect');
        if (!earlier.value) earlier.value = observation.id;
        else if (!later.value && earlier.value !== observation.id) later.value = observation.id;
        else earlier.value = observation.id;
        $('metadataList').innerHTML = `<dt>Product</dt><dd>${observation.product_id}</dd><dt>Acquisition</dt><dd>${observation.acquisition_start || 'Unknown'}</dd><dt>Collection</dt><dd>${observation.collection}</dd><dt>Maturity</dt><dd>${observation.collection.includes('BETA') ? 'BETA' : 'PROVISIONAL'}</dd><dt>Estimated size</dt><dd>${observation.size_mb?.toFixed?.(1) || 'Unknown'} MB</dd><dt>Source</dt><dd>${observation.source}</dd>`;
        activateTab('metadata');
      });
      list.appendChild(row);
    });
  }

  function renderTimeline(timeline) {
    const dates = timeline.available_dates || [];
    $('timelineRange').textContent = timeline.earliest_acquisition && timeline.latest_acquisition
      ? `${timeline.earliest_acquisition.slice(0, 10)} → ${timeline.latest_acquisition.slice(0, 10)}${timeline.inventory_complete ? '' : ' · partial inventory'}`
      : 'No dates returned';
    const slider = $('timelineSlider');
    slider.disabled = dates.length === 0;
    slider.max = Math.max(0, dates.length - 1);
    slider.value = dates.length ? dates.length - 1 : 0;
    $('timelineDate').textContent = dates.length ? `Latest returned: ${dates[dates.length - 1]}` : 'No observation dates match';
    slider.oninput = () => { $('timelineDate').textContent = dates[Number(slider.value)] || 'No observation date'; };
  }

  async function refreshObservations() {
    status('Searching Earthdata metadata…');
    $('connectionText').textContent = 'Metadata search in progress';
    try {
      const response = await fetch(`${apiBase}/api/earthdata/archive/search?page=1&page_size=20`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(catalogRequest()),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      observations = payload.observations || [];
      renderObservationList();
      const timelineParams = new URLSearchParams({ collection: $('collectionSelect').value });
      if (body.start) timelineParams.set('start', body.start);
      if (body.end) timelineParams.set('end', body.end);
      if (body.bbox) {
        Object.entries(body.bbox).forEach(([key, value]) => timelineParams.set(key, value));
      }
      const timelineResponse = await fetch(`${apiBase}/api/earthdata/timeline?${timelineParams}`);
      const timeline = timelineResponse.ok ? await timelineResponse.json() : payload.timeline || {};
      renderTimeline(timeline);
      ['earlierSelect', 'laterSelect'].forEach((id) => {
        const select = $(id);
        select.innerHTML = '<option value="">Not selected</option>';
        observations.forEach((observation) => {
          const option = document.createElement('option');
          option.value = observation.id;
          option.textContent = `${observation.acquisition_start || 'Unknown time'} · ${observation.product_id}`;
          select.appendChild(option);
        });
      });
      $('connectionText').textContent = `${payload.status === 'partial' ? 'Partial catalog' : 'Search complete'} · ${payload.count} observation(s)`;
      $('connectionText').previousElementSibling?.classList.remove('offline');
      $('analysisNotice').textContent = payload.count
        ? 'Real Earthdata metadata loaded. Select two compatible observations before processing.'
        : 'Search complete: no matching observations were returned.';
      status(payload.count ? `${payload.status === 'partial' ? 'Partial catalog' : 'Search complete'} · ${payload.count} observation(s)` : 'Search complete · no matches');
    } catch (error) {
      $('connectionText').textContent = 'Metadata search unavailable';
      $('analysisNotice').textContent = `No live observations loaded: ${error.message}`;
      status('Earthdata search unavailable');
    }
  }
  $('refreshBtn').addEventListener('click', refreshObservations);
  $('searchCatalogBtn').addEventListener('click', refreshObservations);
  $('processBtn').addEventListener('click', async () => {
    const selected = [$('earlierSelect').value, $('laterSelect').value].filter(Boolean);
    if (!selected.length) {
      $('analysisNotice').textContent = 'Select one or two catalog observations for bounded streaming.';
      status('Streaming not started: no observation selected');
      return;
    }
    status('Streaming selected GCOV preview…');
    try {
      const params = new URLSearchParams({
        frequency: $('frequencySelect').value || 'frequencyA',
        polarization: $('polarizationSelect').value || 'HH',
        threshold_db: $('thresholdInput').value || '3',
      });
      const endpoint = selected.length === 2 ? '/api/earthdata/change' : '/api/earthdata/stream';
      const response = await fetch(`${apiBase}${endpoint}?${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(selected),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      if (selected.length === 2) {
        const result = payload.result;
        const mapped = addDerivedChangeLayer(result);
        $('analysisNotice').innerHTML = `Candidate change calculated: ${result.changed_percent.toFixed(2)}% of valid sampled pixels changed at ±${result.threshold_db} dB. <a href="${apiBase}/${result.preview}" target="_blank" rel="noopener">Open derived preview</a>${mapped ? ' · Added to map' : ''}<br><small>${result.warning}</small>`;
        status(`Change calculated · ${result.changed_pixels.toLocaleString()} candidate pixels`);
      } else {
        const bytes = payload.granules.reduce((total, item) => total + (item.bytes_read || 0), 0);
        $('analysisNotice').textContent = `Streaming complete. Read approximately ${(bytes / 1_000_000).toFixed(2)} MB and wrote derived previews only.`;
        status(`Streaming complete · ${payload.count} preview(s)`);
      }
    } catch (error) {
      $('analysisNotice').textContent = `Streaming unavailable: ${error.message}`;
      status('Streaming failed');
    }
  });
  $('layerOpacity').addEventListener('input', (event) => { selectedLayer.setOpacity(Number(event.target.value) / 100); });

  function zoomToInput() {
    const values = $('studyAreaInput').value.split(',').map(Number);
    if (values.length !== 3 || values.some((value) => !Number.isFinite(value)) || values[0] < -90 || values[0] > 90 || values[1] < -180 || values[1] > 180) {
      status('Use latitude, longitude, zoom');
      return;
    }
    map.getView().setCenter(ol.proj.fromLonLat([values[1], values[0]]));
    map.getView().setZoom(Math.max(1, Math.min(20, values[2])));
    $('studyAreaStatus').textContent = `Study area: ${values[0].toFixed(4)}, ${values[1].toFixed(4)}`;
    status('Study area centered');
  }
  $('zoomStudyBtn').addEventListener('click', zoomToInput);
  $('studyAreaInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') zoomToInput(); });
  $('resetViewBtn').addEventListener('click', () => {
    map.getView().setCenter(ol.proj.fromLonLat([-98, 38]));
    map.getView().setZoom(3);
  });
  $('zoomInBtn').addEventListener('click', () => map.getView().setZoom(map.getView().getZoom() + 1));
  $('zoomOutBtn').addEventListener('click', () => map.getView().setZoom(map.getView().getZoom() - 1));
  // Use a rectangle geometry function for the study-area tool.
  $('drawBoxBtn').onclick = () => {
    if (drawInteraction) map.removeInteraction(drawInteraction);
    drawInteraction = new ol.interaction.Draw({
      source: studySource,
      type: 'Circle',
      geometryFunction: ol.interaction.Draw.createBox(),
    });
    map.addInteraction(drawInteraction);
    drawInteraction.once('drawend', (event) => {
      map.removeInteraction(drawInteraction);
      const extent = event.feature.getGeometry().getExtent();
      if (extent) {
        const [west, south] = ol.proj.toLonLat([extent[0], extent[1]]);
        const [east, north] = ol.proj.toLonLat([extent[2], extent[3]]);
        $('bboxInput').value = [west, south, east, north].map((value) => value.toFixed(6)).join(',');
        $('studyAreaStatus').textContent = 'Study area: rectangle ready for catalog search';
      }
      status('Study area drawn');
    });
  };
  map.on('pointermove', (event) => {
    const [lon, lat] = ol.proj.toLonLat(event.coordinate);
    $('cursorPosition').textContent = `Cursor: ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  });
  function updateScale() {
    const resolution = map.getView().getResolution();
    if (!resolution) return;
    const denominator = Math.round(resolution * 3779.527559);
    $('mapScale').textContent = `Scale: 1:${denominator.toLocaleString()}`;
  }
  map.getView().on('change:resolution', updateScale);
  updateScale();

  $('rasterInput').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.name.toLowerCase().endsWith('.geojson')) {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const source = new ol.source.Vector({ features: new ol.format.GeoJSON().readFeatures(reader.result, { featureProjection: 'EPSG:3857' }) });
          localLayer = new ol.layer.Vector({ source, style: new ol.style.Style({ stroke: new ol.style.Stroke({ color: '#d6b36a', width: 2 }), fill: new ol.style.Fill({ color: 'rgba(214,179,106,.12)' }) }), properties: { key: 'local', name: `Local GeoJSON: ${file.name}`, kind: 'raster' } });
          map.addLayer(localLayer); layers.push({ layer: localLayer, visible: true }); renderLayers(); map.getView().fit(source.getExtent(), { padding: [50, 50, 50, 50], duration: 300 }); status(`Loaded ${file.name}`);
        } catch (error) { status(`GeoJSON error: ${error.message}`); }
      };
      reader.readAsText(file);
    } else {
      status('GeoTIFF selection recorded; browser rendering needs a COG/GeoTIFF backend or module build.');
    }
  });
  map.once('rendercomplete', () => status('Map ready · live observations not connected'));

  const compareState = { before: null, after: null, beforeUrl: null, afterUrl: null };
  function loadImage(file, slot) {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      if (slot === 'before' && compareState.beforeUrl) URL.revokeObjectURL(compareState.beforeUrl);
      if (slot === 'after' && compareState.afterUrl) URL.revokeObjectURL(compareState.afterUrl);
      compareState[slot] = image; compareState[`${slot}Url`] = url;
      if (compareState.before && compareState.after) $('compareBtn').disabled = false;
      $('comparisonStatus').textContent = `${file.name} loaded · local browser processing`;
    };
    image.onerror = () => { URL.revokeObjectURL(url); $('comparisonStatus').textContent = 'Could not read this image.'; };
    image.src = url;
  }
  $('beforeInput').addEventListener('change', (event) => loadImage(event.target.files[0], 'before'));
  $('afterInput').addEventListener('change', (event) => loadImage(event.target.files[0], 'after'));
  function drawImage(canvas, image) {
    const context = canvas.getContext('2d'); context.clearRect(0, 0, canvas.width, canvas.height);
    const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
    const width = image.width * scale; const height = image.height * scale;
    context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
  }
  function compareImages() {
    if (!compareState.before || !compareState.after) return;
    const width = Math.max(compareState.before.width, compareState.after.width);
    const height = Math.max(compareState.before.height, compareState.after.height);
    const before = document.createElement('canvas'); before.width = width; before.height = height;
    const after = document.createElement('canvas'); after.width = width; after.height = height;
    const draw = (canvas, image) => { const scale = Math.min(width / image.width, height / image.height); canvas.getContext('2d').drawImage(image, (width - image.width * scale) / 2, (height - image.height * scale) / 2, image.width * scale, image.height * scale); };
    draw(before, compareState.before); draw(after, compareState.after);
    const a = before.getContext('2d').getImageData(0, 0, width, height).data; const b = after.getContext('2d').getImageData(0, 0, width, height).data;
    const output = document.createElement('canvas'); output.width = width; output.height = height; const pixels = output.getContext('2d').createImageData(width, height); const threshold = Number($('imageThreshold').value); let changed = 0;
    for (let i = 0; i < a.length; i += 4) { const score = Math.max(Math.abs(a[i] - b[i]), Math.abs(a[i + 1] - b[i + 1]), Math.abs(a[i + 2] - b[i + 2])); if (score >= threshold) { changed += 1; pixels.data[i] = 103; pixels.data[i + 1] = 214; pixels.data[i + 2] = 171; pixels.data[i + 3] = 255; } }
    output.getContext('2d').putImageData(pixels, 0, 0); drawImage($('beforeCanvas'), compareState.before); drawImage($('afterCanvas'), compareState.after); const diffContext = $('diffCanvas').getContext('2d'); diffContext.clearRect(0, 0, 640, 240); diffContext.drawImage(output, 0, 0, 640, 240); $('pixelMetric').textContent = `${((changed / (width * height)) * 100).toFixed(2)}% flagged · ${width} × ${height}px`; $('downloadBtn').disabled = false; $('downloadBtn').onclick = () => { const link = document.createElement('a'); link.href = output.toDataURL('image/png'); link.download = 'earthchange-difference-map.png'; link.click(); };
  }
  $('compareBtn').addEventListener('click', compareImages); $('imageThreshold').addEventListener('input', compareImages);
  $('resetBtn').addEventListener('click', () => { compareState.before = null; compareState.after = null; $('compareBtn').disabled = true; $('downloadBtn').disabled = true; $('comparisonStatus').textContent = 'Upload two PNG/JPG images. This is not geospatial validation.'; ['beforeCanvas', 'afterCanvas', 'diffCanvas'].forEach((id) => $(id).getContext('2d').clearRect(0, 0, $(id).width, $(id).height)); });
  activateTab('catalog');
})();
