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
let markers = [];
let mapsLoaded = false;
let mapsLoading = false;
let directionsService = null;
let directionsRenderer = null;

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

    // Inject Custom CSS for Google Maps UI
    if (!document.getElementById('gm-custom-css')) {
        const style = document.createElement('style');
        style.id = 'gm-custom-css';
        style.innerHTML = `
            /* Harita Tipi Butonu */
            .gm-style .gm-style-mtc > button {
                border-radius: 12px !important;
                font-family: inherit !important;
                font-size: 13px !important;
                font-weight: 500 !important;
                padding: 4px 12px !important;
                margin-top: 10px !important;
                margin-left: 10px !important;
                color: #374151 !important;
                background: white !important;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15) !important;
            }
            .gm-style .gm-style-mtc > div {
                border-radius: 12px !important;
            }
            /* Yakınlaştırma (+) (-) Butonları */
            .gm-style .gmnoprint > div > button {
                border-radius: 50% !important;
                width: 32px !important;
                height: 32px !important;
                margin: 4px !important;
                background: white !important;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15) !important;
            }
            .gm-style .gmnoprint > div {
                background: transparent !important;
                box-shadow: none !important;
            }
            .gm-style .gmnoprint > div > div {
                display: none !important; /* Aradaki çizgiyi gizle */
            }
            /* Tam ekran butonu */
            .gm-style .gm-fullscreen-control {
                border-radius: 50% !important;
                width: 32px !important;
                height: 32px !important;
                margin: 10px !important;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15) !important;
                background: white !important;
            }
        `;
        document.head.appendChild(style);
    }

    map = new google.maps.Map(container, {
        zoom: 7,
        center: { lat: 39.9, lng: 32.8 },
        mapTypeId: 'roadmap',
        mapTypeControl: true,
        streetViewControl: false,
        fullscreenControl: true,
        zoomControl: true,
        styles: [
            {
                featureType: "poi",
                elementType: "labels",
                stylers: [{ visibility: "off" }]
            }
        ]
    });

    directionsService = new google.maps.DirectionsService();
    directionsRenderer = new google.maps.DirectionsRenderer({
        map: map,
        suppressMarkers: true,
        preserveViewport: false,
        polylineOptions: {
            strokeColor: '#3b82f6',
            strokeOpacity: 0.8,
            strokeWeight: 6
        }
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

        const legs = data.legs || [];

        if (legs.length === 0) {
            console.warn("⚠️ Rota verisi boş (legs empty)");
            return;
        }

        const startLeg = legs[0];
        const endLeg = legs[legs.length - 1];

        const startPos = parseCoord(startLeg.start_point);
        const endPos = parseCoord(endLeg.end_point);

        const waypoints = [];
        let chargeIndex = 1;

        legs.forEach((leg, legIndex) => {
            if (leg.type === 'charge' && leg.station && leg.station.location) {
                const loc = leg.station.location;
                const pos = { lat: loc.lat, lng: loc.lon || loc.lng };
                waypoints.push({
                    location: new google.maps.LatLng(pos.lat, pos.lng),
                    stopover: true
                });

                let weatherStr = "";
                if (leg.weather_context) {
                    const cond = leg.weather_context.condition || "";
                    const temp = leg.weather_context.temp_c?.toFixed(0) || "?";
                    let emoji = '🌤️';
                    const condLower = cond.toLowerCase();
                    if (condLower.includes('rain')) emoji = '🌧️';
                    else if (condLower.includes('snow')) emoji = '❄️';
                    else if (condLower.includes('cloud')) emoji = '☁️';
                    else if (condLower.includes('storm')) emoji = '⛈️';
                    weatherStr = `<br><br><span style="font-size:16px;">${emoji}</span> <b>${temp}°C</b> ${cond}`;
                }

                const detail = `${leg.arrival_soc_percent?.toFixed(0)}% → ${leg.target_soc_percent?.toFixed(0)}% | ${leg.duration_minutes?.toFixed(0)} dk${weatherStr}`;
                const label = leg.station.name || 'İstasyon ' + chargeIndex;

                addStationMarker(pos, chargeIndex, label, detail, new google.maps.LatLngBounds()); // bounds ignored here since renderer handles zoom
                chargeIndex++;
            }
        });

        directionsService.route({
            origin: startPos,
            destination: endPos,
            waypoints: waypoints,
            optimizeWaypoints: false,
            travelMode: google.maps.TravelMode.DRIVING
        }, (response, status) => {
            if (status === 'OK') {
                directionsRenderer.setDirections(response);
                if (startPos) addMarker(startPos, 'start', 'Başlangıç', new google.maps.LatLngBounds());
                if (endPos) addMarker(endPos, 'end', 'Varış', new google.maps.LatLngBounds());
            } else {
                console.error("Directions request failed due to " + status);
            }
        });

    } catch (globalErr) {
        console.error("🔥 renderRouteOnMap kritik hatası:", globalErr);
    }
}

// ============================================
// MARKER HELPERS
// ============================================

function addMarker(position, type, title, bounds) {
    let svg = '';
    let size = 24;

    if (type === 'start') {
        svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" fill="#3B82F6" stroke="white" stroke-width="2.5"/>
            <circle cx="12" cy="12" r="4" fill="white"/>
        </svg>`;
    } else if (type === 'end') {
        svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
            <circle cx="12" cy="12" r="10" fill="#111827" stroke="white" stroke-width="2.5"/>
            <rect x="9" y="9" width="6" height="6" fill="white" rx="1"/>
        </svg>`;
    }

    const marker = new google.maps.Marker({
        position: position,
        map: map,
        title: title,
        zIndex: type === 'start' ? 50 : 40,
        icon: {
            url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
            scaledSize: new google.maps.Size(size, size),
            anchor: new google.maps.Point(size / 2, size / 2)
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
            url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(createStationSVG(index))}`,
            scaledSize: new google.maps.Size(40, 40),
            anchor: new google.maps.Point(20, 20),
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

function createStationSVG(label) {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">
        <!-- Zemin Daire -->
        <circle cx="20" cy="20" r="15" fill="#10B981" stroke="white" stroke-width="2.5"/>
        <!-- Şimşek İkonu -->
        <path d="M20 11l-5 9h5v7l6-10h-5v-6z" fill="white"/>
        <!-- Numara Rozeti -->
        <circle cx="31" cy="9" r="8" fill="#EF4444" stroke="white" stroke-width="1.5"/>
        <text x="31" y="12.5" font-family="Arial, sans-serif" font-size="10" font-weight="bold" fill="white" text-anchor="middle">${label}</text>
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
    if (directionsRenderer) {
        directionsRenderer.set('directions', null);
    }

    if (typeof routePolylines !== 'undefined' && routePolylines && routePolylines.length > 0) {
        routePolylines.forEach(p => p.setMap(null));
        routePolylines = [];
    }

    markers.forEach(m => m.setMap(null));
    markers = [];
}
