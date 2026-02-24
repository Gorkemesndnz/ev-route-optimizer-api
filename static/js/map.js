/**
 * EV Route Optimizer - Google Maps Integration
 * Version: 1.2 (Robust Rendering)
 * 
 * Rota polyline'ını ve şarj istasyonu marker'larını gösterir.
 */

// ============================================
// STATE
// ============================================
let map = null;
let routePolylines = []; // Dizi olarak sakla (Tüm segmentleri yönetmek için)
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

            window.onGoogleMapsReady = () => {
                console.log("🗺️ Google Maps API Yüklendi");
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

function initMap(containerId = 'routeMap') {
    const container = document.getElementById(containerId);
    if (!container || !window.google) return;

    console.log("🗺️ Harita Başlatılıyor...");

    map = new google.maps.Map(container, {
        zoom: 7,
        center: { lat: 39.9, lng: 32.8 },
        mapTypeId: 'roadmap', // Zorunlu ROAMAP
        mapTypeControl: true,
        streetViewControl: true,
        fullscreenControl: true,
        zoomControl: true,
        styles: null // Standart tema
    });
}

// ============================================
// ROUTE RENDERING
// ============================================

async function renderRouteOnMap(data) {
    console.log("🛣️ Rota Çizimi Başladı", data);

    try {
        await loadGoogleMaps();

        const container = document.getElementById('routeMap');
        if (!container) return;
        container.style.display = 'block';

        if (!map) initMap();

        clearMapOverlays();

        const bounds = new google.maps.LatLngBounds();
        const legs = data.legs || [];

        if (legs.length === 0) {
            console.warn("⚠️ Rota verisi boş (legs empty)");
            return;
        }

        // ── Polyline'ları çiz ──
        legs.forEach((leg, index) => {
            if (leg.type === 'drive' && leg.polyline) {
                try {
                    // Geometry kütüphanesi kontrolü
                    if (!google.maps.geometry) {
                        console.error("❌ Google Maps Geometry library yüklenmemiş!");
                        return;
                    }

                    const path = google.maps.geometry.encoding.decodePath(leg.polyline);

                    if (!path || path.length === 0) {
                        console.warn(`⚠️ Leg ${index} için path decode edilemedi.`);
                        return;
                    }

                    const poly = new google.maps.Polyline({
                        path: path,
                        geodesic: false,        // Geodesic kapalı, düz render
                        strokeColor: '#4285F4', // Google Blue
                        strokeOpacity: 1.0,     // Tam opak
                        strokeWeight: 8,        // Daha da kalın çizgi
                        zIndex: 999,            // En üstte
                        map: map,
                        clickable: false        // Tıklamaları engellememesi için
                    });

                    routePolylines.push(poly);
                    path.forEach(p => bounds.extend(p));

                    // Hava durumu marker'ı
                    if (leg.weather_context) {
                        const midPoint = path[Math.floor(path.length / 2)];
                        addWeatherMarker(midPoint, leg.weather_context, bounds);
                    }
                } catch (e) {
                    console.error("❌ Polyline çizim hatası:", e);
                }
            }
        });

        // ── Markerları Ekle ──
        const firstLeg = legs[0];
        if (firstLeg) {
            const startPos = parseCoord(firstLeg.start_point);
            if (startPos) addMarker(startPos, '🟢', 'Başlangıç', bounds);
        }

        let chargeIndex = 1;
        legs.forEach((leg, legIndex) => {
            if (leg.type === 'charge') {
                if (leg.station && leg.station.location) {
                    const loc = leg.station.location;
                    const pos = { lat: loc.lat, lng: loc.lon || loc.lng };
                    const detail = `${leg.arrival_soc_percent?.toFixed(0)}% → ${leg.target_soc_percent?.toFixed(0)}% | ${leg.duration_minutes?.toFixed(0)} dk`;
                    const label = leg.station.name || 'İstasyon ' + chargeIndex;

                    addStationMarker(pos, chargeIndex, label, detail, bounds);
                }

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

        const lastDrive = [...legs].reverse().find(l => l.type === 'drive');
        if (lastDrive) {
            const endPos = parseCoord(lastDrive.end_point);
            if (endPos) addMarker(endPos, '🏁', 'Varış', bounds);
        }

        if (!bounds.isEmpty()) {
            map.fitBounds(bounds, { padding: 50 });
        }

    } catch (globalErr) {
        console.error("🔥 renderRouteOnMap kritik hatası:", globalErr);
    }
}

// ============================================
// MARKER HELPERS
// ============================================

function addMarker(position, emoji, title, bounds) {
    const marker = new google.maps.Marker({
        position: position,
        map: map,
        label: { text: emoji, fontSize: '24px' },
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
        zIndex: 100,
        icon: {
            url: `data:image/svg+xml,${encodeURIComponent(createPinSVG(index, '#EA4335'))}`,
            scaledSize: new google.maps.Size(40, 50),
            anchor: new google.maps.Point(20, 50),
        }
    });

    const infoWindow = new google.maps.InfoWindow({
        content: `<div style="font-family:Roboto,Arial,sans-serif;padding:8px;">
            <strong style="font-size:14px;color:#202124">${title}</strong>
            <div style="margin-top:4px;font-size:13px;color:#5f6368">
                ⚡ Hızlı Şarj (DC)<br>
                ${detail}
            </div>
        </div>`
    });

    marker.addListener('click', () => infoWindow.open(map, marker));
    markers.push(marker);
    bounds.extend(position);
}

function addAlternativeMarker(position, station, legIndex, bounds) {
    const marker = new google.maps.Marker({
        position: position,
        map: map,
        title: station.name + ' (Alternatif)',
        zIndex: 90,
        icon: {
            url: `data:image/svg+xml,${encodeURIComponent(createPinSVG('?', '#9AA0A6'))}`,
            scaledSize: new google.maps.Size(32, 40),
            anchor: new google.maps.Point(16, 40),
        }
    });

    const safeStationStr = JSON.stringify(station).replace(/"/g, '&quot;');
    const power = station.connectors?.[0]?.power_kw || '?';

    const infoContent = `
        <div style="font-family:Roboto,Arial,sans-serif;padding:8px;max-width:220px">
            <div style="font-weight:600;font-size:14px;color:#202124;margin-bottom:4px">${station.name}</div>
            <div style="font-size:13px;color:#5f6368;margin-bottom:8px">
                ${station.operator || 'Operatör Bilinmiyor'} • ${power} kW
                <br>
                Rotadan sapma: ${station.distance_from_route_km?.toFixed(1) || 0} km
            </div>
            <button onclick="selectAlternativeStation(${legIndex}, '${station.id}', ${safeStationStr})" 
                style="background:#1a73e8;color:white;border:none;padding:8px 16px;border-radius:4px;font-size:13px;cursor:pointer;width:100%;font-weight:500;">
                Bu İstasyonu Seç
            </button>
        </div>
    `;

    const infoWindow = new google.maps.InfoWindow({ content: infoContent });
    marker.addListener('click', () => infoWindow.open(map, marker));
    markers.push(marker);
}

function addWeatherMarker(position, weather, bounds) {
    if (!weather) return;

    let emoji = '🌤️';
    let label = 'Açık'; // Default
    const cond = (weather.condition || '').toLowerCase(); // Güvenli lowercase

    if (cond.includes('rain')) emoji = '🌧️';
    else if (cond.includes('snow')) emoji = '❄️';
    else if (cond.includes('fog')) emoji = '🌫️';
    else if (cond.includes('wind')) emoji = '💨';
    else if (cond.includes('cloud')) emoji = '☁️';
    else if (cond.includes('storm')) emoji = '⛈️';

    const temp = weather.temp_c?.toFixed(0) || '?';

    const marker = new google.maps.Marker({
        position: position,
        map: map,
        title: `${temp}°C`,
        zIndex: 80,
        icon: {
            url: `data:image/svg+xml,${encodeURIComponent(createWeatherBadgeSVG(emoji, temp))}`,
            scaledSize: new google.maps.Size(48, 28),
            anchor: new google.maps.Point(24, 14),
        }
    });

    const infoWindow = new google.maps.InfoWindow({
        content: `<div style="text-align:center"><b>${temp}°C</b><br>${weather.condition}</div>`
    });
    marker.addListener('click', () => infoWindow.open(map, marker));
    markers.push(marker);
}

function createPinSVG(label, color) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 42" width="30" height="42">
        <path d="M15 0C6.7 0 0 6.7 0 15c0 10 15 27 15 27s15-17 15-27C30 6.7 23.3 0 15 0z" fill="${color}"/>
        <circle cx="15" cy="15" r="6" fill="white"/>
        <text x="15" y="19" text-anchor="middle" font-family="Arial" font-size="12" font-weight="bold" fill="${color}">${label}</text>
    </svg>`;
}

function createWeatherBadgeSVG(emoji, temp) {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="28" viewBox="0 0 48 28">
        <rect x="0" y="0" width="48" height="28" rx="14" ry="14" fill="white" stroke="#dadce0" stroke-width="1"/>
        <text x="14" y="20" font-size="16">${emoji}</text>
        <text x="36" y="19" text-anchor="middle" font-family="Arial" font-size="12" font-weight="bold" fill="#3c4043">${temp}°</text>
    </svg>`;
}

function parseCoord(point) {
    if (!point) return null;
    if (typeof point === 'object' && point.lat !== undefined) return { lat: point.lat, lng: point.lon || point.lng };
    if (typeof point === 'string') {
        const parts = point.split(',').map(Number);
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) return { lat: parts[0], lng: parts[1] };
    }
    return null;
}

function clearMapOverlays() {
    if (routePolylines && routePolylines.length > 0) {
        routePolylines.forEach(p => p.setMap(null));
        routePolylines = [];
    }

    markers.forEach(m => m.setMap(null));
    markers = [];
}
