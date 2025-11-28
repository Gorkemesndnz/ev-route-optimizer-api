/**
 * EV Route Optimizer - Main Application
 * Version: 1.5
 * 
 * Form handler ve results renderer
 */

// ============================================
// FORM SUBMISSION
// ============================================

document.addEventListener('DOMContentLoaded', function () {
    const routeForm = document.getElementById('routeForm');

    if (routeForm) {
        routeForm.addEventListener('submit', handleFormSubmit);
    }
});

/**
 * Form submit handler
 * @param {Event} e - Submit event
 */
async function handleFormSubmit(e) {
    e.preventDefault();

    // Show loading state
    showLoading();

    const startAddress = document.getElementById('startLocation').value;
    const endAddress = document.getElementById('endLocation').value;

    try {
        // Adresleri koordinata çevir
        const [startLocation, endLocation] = await Promise.all([
            geocodeAddress(startAddress),
            geocodeAddress(endAddress)
        ]);

        // Form verilerini topla
        const formData = {
            start_location: startLocation,
            end_location: endLocation,
            vehicle_model_id: document.getElementById('vehicleModel').value,
            current_soc_percent: parseInt(document.getElementById('initialSoc').value),
            target_arrival_soc_percent: parseInt(document.getElementById('targetArrivalSoc').value),
            charge_min_soc_percent: parseInt(document.getElementById('chargeMinSoc').value),
            charge_target_soc_percent: parseInt(document.getElementById('chargeTargetSoc').value),
            extra_load_kg: parseInt(document.getElementById('extraLoad').value),
            passenger_count: parseInt(document.getElementById('passengerCount').value)
        };

        // API isteği
        const response = await fetch('/optimize_route', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });

        const data = await response.json();
        hideLoading();

        if (data.status === 'success' || data.total_distance_km > 0) {
            showResults(data);
        } else {
            showError(data);
        }
    } catch (error) {
        hideLoading();
        showError({ status: 'error', message: error.message });
    }
}

// ============================================
// RESULTS RENDERER
// ============================================

/**
 * Başarılı sonuçları göster
 * @param {Object} data - API response data
 */
function showResults(data) {
    const resultsDiv = document.getElementById('routeResults');
    const leg = data.legs[0] || {};

    // Süre hesaplama
    const hours = Math.floor(data.total_duration_minutes / 60);
    const mins = Math.round(data.total_duration_minutes % 60);
    const durationStr = hours > 0 ? `${hours}s ${mins}dk` : `${mins}dk`;

    // Tüketim hesaplama
    const consumption = data.consumption_kwh ||
        data.legs.filter(l => l.type === 'drive').reduce((sum, l) => sum + (l.consumption_kwh || 0), 0);

    let html = `
        <!-- Stats Grid -->
        ${renderStatsGrid(data, durationStr, consumption)}
        
        <!-- Battery Status -->
        ${renderBatteryStatus(leg)}
        
        <!-- Charging Status Message -->
        ${data.message ? renderStatusMessage(data.message) : ''}
        
        <!-- Weather Information -->
        ${leg.weather_context ? renderWeatherInfo(leg.weather_context) : ''}
        
        <!-- Charge Stops -->
        ${data.charge_stops > 0 ? renderChargeStops(data.legs) : ''}
        
        <!-- Route Details -->
        ${renderRouteDetails()}
        
        <!-- Efficiency Metrics -->
        ${renderEfficiencyMetrics(data.total_co2_savings_kg)}
    `;

    resultsDiv.innerHTML = html;
    document.getElementById('resultCard').style.display = 'block';
}

/**
 * Stats grid render
 */
function renderStatsGrid(data, durationStr, consumption) {
    return `
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="stat-card rounded-xl p-4 text-center">
                <p class="text-gray-400 text-xs mb-1">Mesafe</p>
                <p class="text-2xl font-bold text-white">${data.total_distance_km.toFixed(0)}</p>
                <p class="text-green-400 text-sm">km</p>
            </div>
            <div class="stat-card rounded-xl p-4 text-center">
                <p class="text-gray-400 text-xs mb-1">Süre</p>
                <p class="text-2xl font-bold text-white">${durationStr}</p>
                <p class="text-blue-400 text-sm">tahmini</p>
            </div>
            <div class="stat-card rounded-xl p-4 text-center">
                <p class="text-gray-400 text-xs mb-1">Tüketim</p>
                <p class="text-2xl font-bold text-white">${consumption.toFixed(1)}</p>
                <p class="text-yellow-400 text-sm">kWh</p>
            </div>
            <div class="stat-card rounded-xl p-4 text-center">
                <p class="text-gray-400 text-xs mb-1">CO2 Tasarrufu</p>
                <p class="text-2xl font-bold text-white">${data.total_co2_savings_kg.toFixed(1)}</p>
                <p class="text-emerald-400 text-sm">kg</p>
            </div>
        </div>
    `;
}

/**
 * Battery status render
 */
function renderBatteryStatus(leg) {
    const startSoc = leg.start_soc_percent?.toFixed(0) || 0;
    const endSoc = leg.end_soc_percent?.toFixed(0) || 0;

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <div class="flex items-center justify-between mb-3">
                <span class="text-gray-400 text-sm">Batarya Durumu</span>
                <span class="text-white font-medium">${startSoc}% → ${endSoc}%</span>
            </div>
            <div class="relative h-6 bg-white/10 rounded-full overflow-hidden">
                <div class="absolute left-0 top-0 h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all battery-fill" style="width: ${startSoc}%"></div>
                <div class="absolute left-0 top-0 h-full bg-gradient-to-r from-yellow-500 to-orange-400 rounded-full transition-all battery-fill" style="width: ${endSoc}%"></div>
            </div>
            <div class="flex justify-between mt-2 text-xs text-gray-500">
                <span>Başlangıç: ${startSoc}%</span>
                <span>Varış: ${endSoc}%</span>
            </div>
        </div>
    `;
}

/**
 * Status message render
 */
function renderStatusMessage(message) {
    const isWarning = message.includes('⚠️');
    const bgColor = isWarning ? 'bg-yellow-500/20' : 'bg-green-500/20';
    const textColor = isWarning ? 'text-yellow-500' : 'text-green-500';
    const iconPath = isWarning
        ? 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 15.5c-.77.833.192 2.5 1.732 2.5z'
        : 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z';

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <div class="flex items-center gap-3">
                <div class="w-8 h-8 ${bgColor} rounded-full flex items-center justify-center">
                    <svg class="w-4 h-4 ${textColor}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${iconPath}"/>
                    </svg>
                </div>
                <div class="flex-1">
                    <p class="text-white text-sm font-medium">${message}</p>
                </div>
            </div>
        </div>
    `;
}

/**
 * Weather info render
 */
function renderWeatherInfo(weatherContext) {
    const startWeather = weatherContext.start_weather;
    const endWeather = weatherContext.end_weather;

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <h4 class="text-white font-medium mb-4 flex items-center gap-2">
                <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z"/>
                </svg>
                Hava Durumu
            </h4>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                ${renderWeatherCard('Başlangıç', document.getElementById('startLocation').value, startWeather, 'green')}
                ${renderWeatherCard('Varış', document.getElementById('endLocation').value, endWeather, 'red')}
            </div>
        </div>
    `;
}

/**
 * Weather card render
 */
function renderWeatherCard(title, location, weather, color) {
    const weatherContent = weather.temp_c !== null
        ? `
            <div class="space-y-2">
                <div class="flex justify-between text-sm">
                    <span class="text-gray-400">Sıcaklık:</span>
                    <span class="text-white">${weather.temp_c}°C</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-gray-400">Durum:</span>
                    <span class="text-white">${weather.condition}</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-gray-400">Rüzgar:</span>
                    <span class="text-white">${weather.wind_speed_mps} m/s</span>
                </div>
            </div>
        `
        : '<p class="text-gray-500 text-sm">Hava durumu bilgisi alınamadı</p>';

    return `
        <div class="bg-white/5 rounded-lg p-4">
            <div class="flex items-center gap-3 mb-3">
                <div class="w-8 h-8 bg-${color}-500/20 rounded-full flex items-center justify-center">
                    <svg class="w-4 h-4 text-${color}-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                    </svg>
                </div>
                <div>
                    <p class="text-white text-sm font-medium">${title}</p>
                    <p class="text-gray-500 text-xs">${location}</p>
                </div>
            </div>
            ${weatherContent}
        </div>
    `;
}

/**
 * Charge stops render
 */
function renderChargeStops(legs) {
    const chargeLegs = legs.filter(l => l.type === 'charge');

    const stopsHtml = chargeLegs.map((charge, i) => `
        <div class="bg-gradient-to-r from-yellow-500/10 to-orange-500/10 rounded-lg p-4 border border-yellow-500/20">
            <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-3">
                    <div class="w-8 h-8 bg-yellow-500/20 rounded-full flex items-center justify-center text-yellow-400 font-bold text-sm">${i + 1}</div>
                    <div>
                        <p class="text-white font-medium text-sm">${charge.station?.name || 'Şarj İstasyonu'}</p>
                        <p class="text-gray-500 text-xs">${charge.station?.operator || ''}</p>
                    </div>
                </div>
                <div class="text-right">
                    <p class="text-white font-medium">${charge.station?.connectors?.[0]?.power_kw || 0} kW</p>
                    <p class="text-gray-500 text-xs">${charge.station?.connectors?.[0]?.plug_type || 'CCS2'}</p>
                </div>
            </div>
            <div class="grid grid-cols-3 gap-2 text-center">
                <div class="bg-white/5 rounded-lg p-2">
                    <p class="text-xs text-gray-500">Varış</p>
                    <p class="text-white font-medium">${charge.arrival_soc_percent?.toFixed(0)}%</p>
                </div>
                <div class="bg-white/5 rounded-lg p-2">
                    <p class="text-xs text-gray-500">Hedef</p>
                    <p class="text-green-400 font-medium">${charge.target_soc_percent?.toFixed(0)}%</p>
                </div>
                <div class="bg-white/5 rounded-lg p-2">
                    <p class="text-xs text-gray-500">Süre</p>
                    <p class="text-blue-400 font-medium">${charge.duration_minutes?.toFixed(0)} dk</p>
                </div>
            </div>
            <div class="flex justify-between mt-3 text-xs">
                <span class="text-gray-500">+${charge.energy_added_kwh?.toFixed(1)} kWh</span>
                <span class="text-yellow-400">~₺${charge.estimated_cost?.toFixed(0) || 0}</span>
            </div>
        </div>
    `).join('');

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <h4 class="text-white font-medium mb-4 flex items-center gap-2">
                <svg class="w-5 h-5 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                </svg>
                Şarj Durakları (${chargeLegs.length})
            </h4>
            <div class="space-y-3">
                ${stopsHtml}
            </div>
        </div>
    `;
}

/**
 * Route details render
 */
function renderRouteDetails() {
    const startLocation = document.getElementById('startLocation').value;
    const endLocation = document.getElementById('endLocation').value;

    return `
        <div class="bg-white/5 rounded-xl p-5">
            <h4 class="text-white font-medium mb-4 flex items-center gap-2">
                <svg class="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"/>
                </svg>
                Rota Detayları
            </h4>
            <div class="space-y-3">
                <div class="flex items-center gap-3 p-3 bg-white/5 rounded-lg">
                    <div class="w-8 h-8 bg-green-500/20 rounded-full flex items-center justify-center">
                        <div class="w-3 h-3 bg-green-500 rounded-full"></div>
                    </div>
                    <div class="flex-1">
                        <p class="text-white text-sm">${startLocation}</p>
                        <p class="text-gray-500 text-xs">Başlangıç Noktası</p>
                    </div>
                </div>
                <div class="flex items-center gap-3 p-3 bg-white/5 rounded-lg">
                    <div class="w-8 h-8 bg-red-500/20 rounded-full flex items-center justify-center">
                        <div class="w-3 h-3 bg-red-500 rounded-full"></div>
                    </div>
                    <div class="flex-1">
                        <p class="text-white text-sm">${endLocation}</p>
                        <p class="text-gray-500 text-xs">Varış Noktası</p>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Efficiency metrics render
 */
function renderEfficiencyMetrics(co2Savings) {
    const treesEquivalent = Math.round(co2Savings / 21);

    return `
        <div class="mt-6 p-4 bg-gradient-to-r from-green-500/10 to-emerald-500/10 rounded-xl border border-green-500/20">
            <div class="flex items-center gap-2 mb-2">
                <svg class="w-5 h-5 text-green-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clip-rule="evenodd"/>
                </svg>
                <span class="text-green-400 font-medium text-sm">Çevre Dostu Sürüş</span>
            </div>
            <p class="text-gray-400 text-sm">
                Bu rotada <span class="text-white font-medium">${co2Savings.toFixed(1)} kg</span> CO2 tasarrufu sağladınız. 
                Bu, yaklaşık <span class="text-white font-medium">${treesEquivalent}</span> ağacın yıllık CO2 absorpsiyonuna eşdeğer!
            </p>
        </div>
    `;
}

// ============================================
// ERROR HANDLER
// ============================================

/**
 * Hata mesajını göster
 * @param {Object} data - Error data
 */
function showError(data) {
    const errorDiv = document.getElementById('errorResults');

    let html = `
        <div class="bg-red-500/10 border border-red-500/30 rounded-xl p-4">
            <p class="text-red-400 font-medium mb-1">${data.status || 'Hata'}</p>
            <p class="text-gray-400 text-sm">${data.message || data.detail || 'Bilinmeyen bir hata oluştu.'}</p>
        </div>
    `;

    if (data.status === 'error_no_station_found') {
        html += `
            <div class="mt-4 bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4">
                <p class="text-yellow-400 font-medium mb-1">💡 Bilgi</p>
                <p class="text-gray-400 text-sm">Rota hesaplandı ancak şarj istasyonları bulunamadı. Bu normal bir durum olabilir.</p>
            </div>
        `;
    }

    errorDiv.innerHTML = html;
    document.getElementById('errorCard').style.display = 'block';
}
