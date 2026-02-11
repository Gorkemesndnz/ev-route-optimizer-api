/**
 * EV Route Optimizer - Google Maps Integration
 * Version: 1.0
 * 
 * Rota polyline'ını ve şarj istasyonu marker'larını gösterir.
 */

// ============================================
// STATE
// ============================================
let map = null;
let routePolyline = null;
let markers = [];
let mapsLoaded = false;
let mapsLoading = false;

// ============================================
// GOOGLE MAPS LOADER
// ============================================

/**
 * Google Maps JS API'yi backend key ile dinamik yükler.
 * @returns {Promise<void>}
 */
async function loadGoogleMaps() {
    if (mapsLoaded) return;
    if (mapsLoading) {
        // Zaten yükleniyor, bekle
        return new Promise((resolve) => {
            const check = setInterval(() => {
                if (mapsLoaded) { clearInterval(check); resolve(); }
            }, 100);
        });
    }

    mapsLoading = true;

    try {
        const resp = await fetch('/api/maps-key');
        if (!resp.ok) throw new Error('Maps key alınamadı');
        const { key } = await resp.json();

        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = `https://maps.googleapis.com/maps/api/js?key=${key}&libraries=geometry&callback=onGoogleMapsReady`;
            script.async = true;
            script.defer = true;
            script.onerror = () => { mapsLoading = false; reject(new Error('Maps yüklenemedi')); };
            document.head.appendChild(script);

            // Callback global'e tanımla
            window.onGoogleMapsReady = () => {
                mapsLoaded = true;
                mapsLoading = false;
                resolve();
            };
        });
    } catch (err) {
        mapsLoading = false;
        console.error('Google Maps yükleme hatası:', err);
    }
}

// ============================================
// MAP INITIALIZATION
// ============================================

/**
 * Harita container'ını başlat.
 * @param {string} containerId - HTML element ID
 */
function initMap(containerId = 'routeMap') {
    const container = document.getElementById(containerId);
    if (!container || !window.google) return;

    map = new google.maps.Map(container, {
        zoom: 7,
        center: { lat: 39.9, lng: 32.8 }, // Türkiye merkez
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: true,
        zoomControl: true,
        styles: [
            // Dark map theme — slate-900 UI ile uyumlu
            { elementType: 'geometry', stylers: [{ color: '#1e293b' }] },
            { elementType: 'labels.text.stroke', stylers: [{ color: '#0f172a' }] },
            { elementType: 'labels.text.fill', stylers: [{ color: '#94a3b8' }] },
            { featureType: 'road', elementType: 'geometry', stylers: [{ color: '#334155' }] },
            { featureType: 'road', elementType: 'labels.text.fill', stylers: [{ color: '#64748b' }] },
            { featureType: 'water', elementType: 'geometry', stylers: [{ color: '#0c4a6e' }] },
            { featureType: 'poi', stylers: [{ visibility: 'off' }] },
            { featureType: 'transit', stylers: [{ visibility: 'off' }] },
        ]
    });
}

// ============================================
// ROUTE RENDERING
// ============================================

/**
 * API yanıtındaki legs verisinden haritayı oluşturur.
 * @param {Object} data - MultiStopRouteResponse
 */
async function renderRouteOnMap(data) {
    // Maps henüz yüklü değilse yükle
    await loadGoogleMaps();

    const container = document.getElementById('routeMap');
    if (!container) return;

    // Harita container'ını görünür yap
    container.style.display = 'block';

    // Map yoksa oluştur
    if (!map) initMap();

    // Önceki render'ı temizle
    clearMapOverlays();

    const bounds = new google.maps.LatLngBounds();
    const legs = data.legs || [];

    if (legs.length === 0) return;

    // ── Polyline'ları çiz ──
    legs.forEach((leg, index) => {
        if (leg.type === 'drive' && leg.polyline) {
            const path = google.maps.geometry.encoding.decodePath(leg.polyline);
            const poly = new google.maps.Polyline({
                path: path,
                geodesic: true,
                strokeColor: '#22c55e',
                strokeOpacity: 0.9,
                strokeWeight: 4,
                map: map,
            });
            routePolyline = poly;

            // Bounds'u genişlet
            path.forEach(p => bounds.extend(p));
        }
    });

    // ── Başlangıç Marker ──
    const firstLeg = legs[0];
    if (firstLeg) {
        const startPos = parseCoord(firstLeg.start_point);
        if (startPos) {
            addMarker(startPos, '🟢', 'Başlangıç', bounds);
        }
    }

    // ── Şarj İstasyonu Marker'ları ──
    let chargeIndex = 1;
    legs.forEach((leg, legIndex) => {
        if (leg.type === 'charge') {
            // Ana istasyon
            if (leg.station && leg.station.location) {
                const loc = leg.station.location;
                const pos = { lat: loc.lat, lng: loc.lon || loc.lng };
                const label = `⚡ ${leg.station.name || 'İstasyon ' + chargeIndex}`;
                const detail = `${leg.arrival_soc_percent?.toFixed(0)}% → ${leg.target_soc_percent?.toFixed(0)}% | ${leg.duration_minutes?.toFixed(0)} dk`;

                addStationMarker(pos, chargeIndex, label, detail, bounds);
            }

            // 🔧 Alternatif İstasyonlar (Varsa)
            if (leg.alternative_stations && leg.alternative_stations.length > 0) {
                leg.alternative_stations.forEach(alt => {
                    if (alt.location) {
                        const altPos = { lat: alt.location.lat, lng: alt.location.lon || alt.location.lng };
                        addAlternativeMarker(altPos, alt, legIndex, bounds);
                    }
                });
            }

            chargeIndex++;
        }
    });

    // ── Varış Marker ──
    const lastDrive = [...legs].reverse().find(l => l.type === 'drive');
    if (lastDrive) {
        const endPos = parseCoord(lastDrive.end_point);
        if (endPos) {
            addMarker(endPos, '🔴', 'Varış', bounds);
        }
    }

    // Haritayı sığdır
    if (!bounds.isEmpty()) {
        map.fitBounds(bounds, { padding: 40 });
    }
}

// ============================================
// MARKER HELPERS
// ============================================

function addMarker(position, emoji, title, bounds) {
    const marker = new google.maps.Marker({
        position: position,
        map: map,
        label: { text: emoji, fontSize: '20px' },
        title: title,
        icon: {
            path: google.maps.SymbolPath.CIRCLE,
            scale: 0,
        }
    });
    markers.push(marker);
    bounds.extend(position);
}

function addStationMarker(position, index, title, detail, bounds) {
    const marker = new google.maps.Marker({
        position: position,
        map: map,
        title: title,
        zIndex: 100, // Ana istasyonlar önde
        icon: {
            url: `data:image/svg+xml,${encodeURIComponent(createChargeSVG(index, '#22c55e'))}`, // Yeşil
            scaledSize: new google.maps.Size(36, 36),
            anchor: new google.maps.Point(18, 18),
        }
    });

    const infoWindow = new google.maps.InfoWindow({
        content: `<div style="font-family:Inter,sans-serif;padding:4px 0">
            <strong style="font-size:13px">${title}</strong>
            <br><span style="color:#666;font-size:12px">${detail}</span>
        </div>`
    });

    marker.addListener('click', () => {
        // Diğer infowindow'ları kapat (Global yönetilebilir ama şimdilik basit tutuyoruz)
        infoWindow.open(map, marker);
    });
    markers.push(marker);
    bounds.extend(position);
}

function addAlternativeMarker(position, station, legIndex, bounds) {
    const marker = new google.maps.Marker({
        position: position,
        map: map,
        title: station.name + ' (Alternatif)',
        zIndex: 90, // Alternatifler arkada
        icon: {
            url: `data:image/svg+xml,${encodeURIComponent(createChargeSVG('?', '#94a3b8'))}`, // Gri
            scaledSize: new google.maps.Size(28, 28), // Biraz daha küçük
            anchor: new google.maps.Point(14, 14),
        }
    });

    // İstasyon verisini güvenli stringe çevir
    const safeStationStr = JSON.stringify(station).replace(/"/g, '&quot;');
    const power = station.connectors?.[0]?.power_kw || '?';

    const infoContent = `
        <div style="font-family:Inter,sans-serif;padding:6px;max-width:200px">
            <div style="font-weight:600;font-size:13px;margin-bottom:4px">🔄 ${station.name}</div>
            <div style="font-size:12px;color:#64748b;margin-bottom:8px">
                ${station.operator || 'Bilinmiyor'} • ${power} kW
                <br>
                Rotadan sapma: ${station.distance_from_route_km?.toFixed(1) || 0} km
            </div>
            <button onclick="selectAlternativeStation(${legIndex}, '${station.id}', ${safeStationStr})" 
                style="background:#22c55e;color:white;border:none;padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer;width:100%">
                Bu İstasyonu Seç
            </button>
        </div>
    `;

    const infoWindow = new google.maps.InfoWindow({ content: infoContent });

    marker.addListener('click', () => {
        infoWindow.open(map, marker);
    });
    markers.push(marker);
    // Bounds'u genişletmiyoruz ki ana rota odakta kalsın, alternatifler için zoom out gerekebilir
}


function createChargeSVG(label, color) {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="16" fill="${color}" stroke="white" stroke-width="2"/>
        <text x="18" y="13" text-anchor="middle" fill="white" font-size="9" font-weight="bold">⚡</text>
        <text x="18" y="26" text-anchor="middle" fill="white" font-size="11" font-weight="bold">${label}</text>
    </svg>`;
}

// ============================================
// UTILITIES
// ============================================

function parseCoord(point) {
    if (!point) return null;

    // GeoPoint object
    if (typeof point === 'object' && point.lat !== undefined) {
        return { lat: point.lat, lng: point.lon || point.lng };
    }

    // "lat,lon" string
    if (typeof point === 'string') {
        const parts = point.split(',').map(Number);
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
            return { lat: parts[0], lng: parts[1] };
        }
    }

    return null;
}

function clearMapOverlays() {
    if (routePolyline) {
        routePolyline.setMap(null);
        routePolyline = null;
    }
    markers.forEach(m => m.setMap(null));
    markers = [];
}
