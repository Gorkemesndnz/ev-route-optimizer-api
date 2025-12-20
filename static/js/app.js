/**
 * EV Route Optimizer - Main Application
 * Version: 1.6
 * 
 * Form handler ve results renderer
 * AI Planlama Modu: Akıllı varsayılan değerler
 */

// ============================================
// AI MODE TOGGLE
// ============================================

/**
 * AI Planlama Modu toggle
 * AI açıkken: Batarya/Şarj ayarları gizli (otomatik), Yolcu/Yük her zaman görünür
 * AI kapalıyken: Batarya/Şarj ayarları görünür (manuel)
 */
function toggleAiMode() {
    const aiMode = document.getElementById('aiPlanningMode').checked;

    // Yönetilecek ana kapsayıcıları (kartları) seçiyoruz
    const batterySettings = document.getElementById('batterySettings');
    const passengerLoadCard = document.getElementById('passengerLoadCard');
    const stationPreferencesCard = document.getElementById('stationPreferencesCard');

    if (aiMode) {
        // --- AI MODU AÇIK: Kartları GİZLE ---

        // 1. Batarya ayarlarını gizle
        if (batterySettings) {
            batterySettings.style.display = 'none';
        }

        // 2. Yolcu ve Yük kartını tamamen gizle
        if (passengerLoadCard) {
            passengerLoadCard.style.display = 'none';
        }

        // 3. İstasyon tercihleri kartını tamamen gizle
        if (stationPreferencesCard) {
            stationPreferencesCard.style.display = 'none';
        }

        // 4. (Opsiyonel) Değerleri temizle ki AI yanlış veri almasın
        if (typeof clearBatteryInputs === 'function') {
            clearBatteryInputs();
        }

    } else {
        // --- AI MODU KAPALI: Kartları GÖSTER ---

        // Varsayılan görünüm neyse (block, flex, grid) ona geri döndürür.
        // Genelde 'block' veya boş string '' işe yarar.

        if (batterySettings) {
            batterySettings.style.display = 'block';
        }

        if (passengerLoadCard) {
            passengerLoadCard.style.display = 'block';
        }

        if (stationPreferencesCard) {
            stationPreferencesCard.style.display = 'block';
        }
    }
}

/**
 * Strategy & traffic info render
 */
function renderStrategyTrafficInfo(data) {
    const strategy = data.route_strategy;
    const trafficRatio = data.traffic_ratio;
    const durationNoTraffic = data.duration_without_traffic_minutes;

    if (!strategy && trafficRatio == null && durationNoTraffic == null) return '';

    const labels = {
        fastest: 'En Hızlı',
        efficient: 'En Verimli',
        optimal: 'Optimal',
        cheapest: 'En Ucuz',
        renewable: 'Yenilenebilir'
    };
    const strategyLabel = strategy ? (labels[strategy] || strategy) : '-';

    const totalMin = data.total_duration_minutes;
    const delayMin = (durationNoTraffic != null && totalMin != null)
        ? Math.max(0, totalMin - durationNoTraffic)
        : null;

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-white/5 rounded-lg p-3">
                    <p class="text-gray-500 text-xs mb-1">Strateji</p>
                    <p class="text-white font-medium">${strategyLabel}</p>
                </div>
                <div class="bg-white/5 rounded-lg p-3">
                    <p class="text-gray-500 text-xs mb-1">Trafik Oranı</p>
                    <p class="text-white font-medium">${(trafficRatio != null) ? trafficRatio.toFixed(2) : '-'}</p>
                </div>
                <div class="bg-white/5 rounded-lg p-3">
                    <p class="text-gray-500 text-xs mb-1">Trafik Gecikmesi</p>
                    <p class="text-white font-medium">${(delayMin != null) ? `${delayMin.toFixed(0)} dk` : '-'}</p>
                </div>
            </div>
        </div>
    `;
}

/**
 * 🔧 V3.1: İstasyon Tercihleri toggle
 */
function togglePreferences() {
    const prefs = document.getElementById('stationPreferences');
    const toggleText = document.getElementById('prefsToggleText');

    if (prefs.style.display === 'none') {
        prefs.style.display = 'block';
        toggleText.textContent = 'Gizle ▲';
    } else {
        prefs.style.display = 'none';
        toggleText.textContent = 'Göster ▼';
    }
}

/**
 * 🔧 V3.1: Preferences verilerini topla
 */
function getStationPreferences() {
    return {
        max_detour_km: parseFloat(document.getElementById('maxDetourKm')?.value) || 10,
        preferred_plug_types: document.getElementById('preferredPlugType')?.value ?
            [document.getElementById('preferredPlugType').value] : [],
        preferred_operators: document.getElementById('preferredOperator')?.value ?
            [document.getElementById('preferredOperator').value] : [],
        amenities_required: [
            document.getElementById('reqToilet')?.checked ? 'toilet' : null,
            document.getElementById('reqFood')?.checked ? 'food' : null,
            document.getElementById('reqShopping')?.checked ? 'shopping' : null,
            document.getElementById('reqParking')?.checked ? 'parking' : null
        ].filter(Boolean)
    };
}

/**
 * Batarya/Şarj inputlarını temizle (AI modu için)
 */
function clearBatteryInputs() {
    document.getElementById('chargeMinSoc').value = '';
    document.getElementById('chargeTargetSoc').value = '';
    document.getElementById('targetArrivalSoc').value = '';
}

/**
 * Input değerini al - boş ise null döndür
 */
function getOptionalValue(elementId, parser = parseInt) {
    const element = document.getElementById(elementId);
    const value = element ? element.value.trim() : '';
    if (value === '' || value === null || value === undefined) {
        return null;
    }
    const parsed = parser(value);
    return isNaN(parsed) ? null : parsed;
}

// ============================================
// FORM SUBMISSION
// ============================================

document.addEventListener('DOMContentLoaded', function () {
    const routeForm = document.getElementById('routeForm');

    if (routeForm) {
        routeForm.addEventListener('submit', handleFormSubmit);
    }

    initVehicleCatalogUI();

    // Sayfa ilk açılışta AI toggle varsayılanı açıksa kilitleri hemen uygula
    toggleAiMode();
});

async function initVehicleCatalogUI() {
    const brandEl = document.getElementById('vehicleBrand');
    const modelEl = document.getElementById('vehicleModel');
    const searchEl = document.getElementById('vehicleSearch');
    const resultsEl = document.getElementById('vehicleSearchResults');

    if (!brandEl || !modelEl) return;

    const setBrandOptions = (brands) => {
        brandEl.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = 'Marka seçin';
        placeholder.className = 'bg-slate-800';
        brandEl.appendChild(placeholder);

        for (const b of brands) {
            const opt = document.createElement('option');
            opt.value = b;
            opt.textContent = b;
            opt.className = 'bg-slate-800';
            brandEl.appendChild(opt);
        }
    };

    const setModelOptions = (vehicles, placeholderText = 'Model seçin') => {
        modelEl.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = placeholderText;
        placeholder.className = 'bg-slate-800';
        modelEl.appendChild(placeholder);

        for (const v of vehicles) {
            const opt = document.createElement('option');
            opt.value = v.id;
            opt.textContent = v.display_name;
            opt.className = 'bg-slate-800';
            modelEl.appendChild(opt);
        }
    };

    const hideResults = () => {
        if (!resultsEl) return;
        resultsEl.classList.add('hidden');
        resultsEl.innerHTML = '';
    };

    const showResults = (items) => {
        if (!resultsEl) return;
        resultsEl.innerHTML = '';
        resultsEl.classList.remove('hidden');

        if (!items || items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'px-4 py-3 text-sm text-gray-400';
            empty.textContent = 'Sonuç bulunamadı';
            resultsEl.appendChild(empty);
            return;
        }

        for (const item of items) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'w-full text-left px-4 py-3 text-sm text-white hover:bg-white/10 transition-colors';
            btn.textContent = item.display_name;
            btn.addEventListener('click', async () => {
                hideResults();
                if (searchEl) searchEl.value = item.display_name;
                if (brandEl && item.brand) {
                    brandEl.value = item.brand;
                    try {
                        const brandResp = await fetch(`/vehicles/by_brand?brand=${encodeURIComponent(item.brand)}&limit=5000`);
                        const brandData = await brandResp.json();
                        const vehicles = Array.isArray(brandData.vehicles) ? brandData.vehicles : [];
                        setModelOptions(vehicles, 'Model seçin');
                        modelEl.value = item.id;
                    } catch (e) {
                        setModelOptions([{ id: item.id, display_name: item.display_name }], 'Model seçin');
                        modelEl.value = item.id;
                    }
                } else {
                    setModelOptions([{ id: item.id, display_name: item.display_name }], 'Model seçin');
                    modelEl.value = item.id;
                }
            });
            resultsEl.appendChild(btn);
        }
    };

    try {
        const resp = await fetch('/vehicles/brands');
        const data = await resp.json();
        const brands = Array.isArray(data.brands) ? data.brands : [];
        setBrandOptions(brands);
    } catch (e) {
        brandEl.innerHTML = '<option value="" class="bg-slate-800">Markalar yüklenemedi</option>';
    }

    brandEl.addEventListener('change', async () => {
        hideResults();
        const brand = brandEl.value;
        if (!brand) {
            setModelOptions([], 'Önce marka seçin');
            return;
        }
        try {
            modelEl.disabled = true;
            setModelOptions([], 'Modeller yükleniyor...');
            const resp = await fetch(`/vehicles/by_brand?brand=${encodeURIComponent(brand)}&limit=5000`);
            const data = await resp.json();
            const vehicles = Array.isArray(data.vehicles) ? data.vehicles : [];
            setModelOptions(vehicles, vehicles.length ? 'Model seçin' : 'Model bulunamadı');
        } catch (e) {
            setModelOptions([], 'Model yüklenemedi');
        } finally {
            modelEl.disabled = false;
        }
    });

    if (searchEl && resultsEl) {
        let debounceTimer = null;

        const runSearch = async () => {
            const q = (searchEl.value || '').trim();
            if (q.length < 2) {
                hideResults();
                return;
            }
            try {
                const resp = await fetch(`/vehicles/search?query=${encodeURIComponent(q)}&limit=20`);
                const data = await resp.json();
                const vehicles = Array.isArray(data.vehicles) ? data.vehicles : [];
                showResults(vehicles);
            } catch (e) {
                hideResults();
            }
        };

        searchEl.addEventListener('input', () => {
            if (debounceTimer) window.clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(runSearch, 200);
        });

        document.addEventListener('click', (evt) => {
            const target = evt.target;
            if (!target) return;
            if (target === searchEl || resultsEl.contains(target)) return;
            hideResults();
        });

        searchEl.addEventListener('keydown', (evt) => {
            if (evt.key === 'Escape') {
                hideResults();
            }
        });
    }
}

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
    const aiMode = document.getElementById('aiPlanningMode').checked;

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
            current_soc_percent: parseInt(document.getElementById('initialSoc').value)
        };

        // Rota stratejisi (varsayılan: optimal)
        const routeStrategyEl = document.getElementById('routeStrategy');
        if (routeStrategyEl && routeStrategyEl.value) {
            formData.route_strategy = routeStrategyEl.value;
        }

        // Çıkış zamanı (opsiyonel) -> ISO 8601 UTC (Z)
        const departureTimeEl = document.getElementById('departureTime');
        if (departureTimeEl && departureTimeEl.value) {
            const dt = new Date(departureTimeEl.value);
            if (!isNaN(dt.getTime())) {
                formData.departure_time_iso = dt.toISOString();
            }
        }

        // Yolcu/Yük ayarları her zaman gönderilir (AI modu fark etmez)
        const passengerCount = getOptionalValue('passengerCount');
        const childCount = getOptionalValue('childCount');
        const extraLoad = getOptionalValue('extraLoad', parseFloat);

        if (passengerCount !== null) formData.passenger_count = passengerCount;
        if (childCount !== null) formData.child_count = childCount;
        if (extraLoad !== null) formData.extra_load_kg = extraLoad;

        // AI modu kapalıysa Batarya/Şarj ayarlarını da ekle
        if (!aiMode) {
            const chargeMinSoc = getOptionalValue('chargeMinSoc');
            const chargeTargetSoc = getOptionalValue('chargeTargetSoc');
            const targetArrivalSoc = getOptionalValue('targetArrivalSoc');

            if (chargeMinSoc !== null) formData.charge_min_soc_percent = chargeMinSoc;
            if (chargeTargetSoc !== null) formData.charge_target_soc_percent = chargeTargetSoc;
            if (targetArrivalSoc !== null) formData.target_arrival_soc_percent = targetArrivalSoc;
        }
        // AI modu açıksa Batarya/Şarj otomatik - backend hesaplar

        // 🔧 V3.1: İstasyon Tercihleri
        formData.preferences = getStationPreferences();

        // DEBUG: Form verilerini kontrol et
        console.log('🤖 AI Modu:', aiMode ? 'AÇIK' : 'KAPALI');
        console.log('🔍 Form verileri:', formData);

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
    // 🔧 V3.1: Rota verisini sakla (feedback için)
    storeRouteData(data);

    const resultsDiv = document.getElementById('routeResults');
    const firstLeg = data.legs?.[0] || {};

    // 🔧 Fix: Batarya Durumu için rota geneli SOC değerlerini kullan
    const firstDriveLeg = (data.legs || []).find(l => l.type === 'drive' && l.start_soc_percent != null) || firstLeg;
    const lastDriveLeg = (data.legs || []).slice().reverse().find(l => l.type === 'drive' && l.end_soc_percent != null) || (data.legs || []).slice().reverse()[0] || firstLeg;
    const routeStartSoc = firstDriveLeg.start_soc_percent;
    const routeEndSoc = lastDriveLeg.end_soc_percent;

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

        <!-- Strategy & Traffic Info -->
        ${renderStrategyTrafficInfo(data)}
        
        <!-- Battery Status -->
        ${renderBatteryStatus(routeStartSoc, routeEndSoc)}
        
        <!-- Charging Status Message -->
        ${data.message ? renderStatusMessage(data.message) : ''}
        
        <!-- Weather Information (Başlangıç + Varış) -->
        ${(data.start_weather || data.end_weather) ? renderWeatherInfo(data.start_weather, data.end_weather, data.total_duration_minutes) : ''}
        
        <!-- Multi-Leg Timeline (Rota Planı) -->
        ${data.legs && data.legs.length > 0 ? renderMultiLegs(data.legs) : ''}
        
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
        
        <!-- 🔧 V3.1: Şarj Maliyeti Özeti -->
        ${data.total_charging_cost > 0 ? `
        <div class="bg-gradient-to-r from-yellow-500/10 to-orange-500/10 rounded-xl p-4 mb-6 border border-yellow-500/20">
            <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <span class="text-yellow-400 text-lg">💰</span>
                    <span class="text-gray-300 text-sm">Tahmini Şarj Maliyeti</span>
                </div>
                <div class="text-right">
                    <span class="text-2xl font-bold text-yellow-400">${data.total_charging_cost.toFixed(2)}</span>
                    <span class="text-yellow-400/70 text-sm ml-1">TRY</span>
                </div>
            </div>
        </div>
        ` : ''}
    `;
}

/**
 * Battery status render
 */
function renderBatteryStatus(startSocPercent, endSocPercent) {
    const startSoc = (startSocPercent ?? 0).toFixed(0);
    const endSoc = (endSocPercent ?? 0).toFixed(0);

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <div class="flex items-center justify-between mb-3">
                <span class="text-gray-400 text-sm">Batarya Durumu</span>
                <span class="text-white font-medium">${startSoc}% → ${endSoc}%</span>
            </div>
            <div class="relative h-6 bg-white/10 rounded-full overflow-hidden">
                <div class="absolute left-0 top-0 h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all battery-fill" style="width: ${startSoc}%"></div>
                <div class="absolute left-0 top-0 h-full bg-gradient-to-r from-orange-500 to-red-500 rounded-full transition-all battery-fill" style="width: ${endSoc}%"></div>
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
 * Weather info render - Başlangıç ve Varış hava durumu
 * 🔧 V2.7: Başlangıç current, Varış forecast (ETA bazlı)
 */
function renderWeatherInfo(startWeather, endWeather, totalDurationMin) {
    // ETA hesapla
    const etaHours = Math.floor(totalDurationMin / 60);
    const etaMins = Math.round(totalDurationMin % 60);
    const etaStr = etaHours > 0 ? `~${etaHours}s ${etaMins}dk sonra` : `~${etaMins}dk sonra`;

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <h4 class="text-white font-medium mb-4 flex items-center gap-2">
                <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 15a4 4 0 004 4h9a5 5 0 10-.1-9.999 5.002 5.002 0 10-9.78 2.096A4.001 4.001 0 003 15z"/>
                </svg>
                Hava Durumu
            </h4>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                ${renderWeatherCard('Başlangıç', document.getElementById('startLocation').value, startWeather, 'green', 'Şu an')}
                ${renderWeatherCard('Varış', document.getElementById('endLocation').value, endWeather, 'red', etaStr + ' (forecast)')}
            </div>
        </div>
    `;
}

/**
 * Weather card render
 * 🔧 V2.7: timeLabel parametresi eklendi (current/forecast gösterimi)
 */
function renderWeatherCard(title, location, weather, color, timeLabel = '') {
    if (!weather) {
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
                <p class="text-gray-500 text-sm">Hava durumu bilgisi alınamadı</p>
            </div>
        `;
    }

    const weatherEmoji = getWeatherEmoji(weather.condition);
    const weatherCondition = formatWeatherCondition(weather.condition);

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
                    ${timeLabel ? `<p class="text-blue-400 text-xs">${timeLabel}</p>` : ''}
                </div>
            </div>
            <div class="space-y-2">
                <div class="flex justify-between text-sm">
                    <span class="text-gray-400">🌡️ Sıcaklık:</span>
                    <span class="text-white font-medium">${weather.temp_c?.toFixed(1)}°C</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-gray-400">${weatherEmoji} Durum:</span>
                    <span class="text-white">${weatherCondition}</span>
                </div>
                <div class="flex justify-between text-sm">
                    <span class="text-gray-400">💨 Rüzgar:</span>
                    <span class="text-white">${weather.wind_speed_mps?.toFixed(1)} m/s</span>
                </div>
            </div>
        </div>
    `;
}

/**
 * Multi-Leg Timeline Render - Tüm sürüş ve şarj adımlarını göster
 */
function renderMultiLegs(legs) {
    if (!legs || legs.length === 0) return '';

    const legsHtml = legs.map((leg, i) => {
        if (leg.type === 'drive') {
            return renderDriveLeg(leg, i);
        } else if (leg.type === 'charge') {
            return renderChargeLeg(leg, i);
        }
        return '';
    }).join('');

    const driveCount = legs.filter(l => l.type === 'drive').length;
    const chargeCount = legs.filter(l => l.type === 'charge').length;

    return `
        <div class="bg-white/5 rounded-xl p-5 mb-6">
            <h4 class="text-white font-medium mb-4 flex items-center gap-2">
                <svg class="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"/>
                </svg>
                Rota Planı
                <span class="text-xs text-gray-500 ml-2">(${driveCount} sürüş + ${chargeCount} şarj)</span>
            </h4>
            <div class="relative">
                <!-- Timeline Line -->
                <div class="absolute left-4 top-0 bottom-0 w-0.5 bg-gradient-to-b from-green-500 via-yellow-500 to-red-500"></div>
                
                <!-- Legs -->
                <div class="space-y-4">
                    ${legsHtml}
                </div>
            </div>
        </div>
    `;
}

/**
 * Drive Leg Render
 */
function renderDriveLeg(leg, index) {
    const durationHours = Math.floor(leg.duration_minutes / 60);
    const durationMins = Math.round(leg.duration_minutes % 60);
    const durationStr = durationHours > 0 ? `${durationHours}s ${durationMins}dk` : `${durationMins}dk`;

    return `
        <div class="relative pl-10">
            <!-- Icon -->
            <div class="absolute left-0 w-8 h-8 bg-blue-500/20 rounded-full flex items-center justify-center border-2 border-blue-500">
                <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17a2 2 0 11-4 0 2 2 0 014 0zM19 17a2 2 0 11-4 0 2 2 0 014 0z"/>
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10a1 1 0 001 1h1m8-1a1 1 0 01-1 1H9m4-1V8a1 1 0 011-1h2.586a1 1 0 01.707.293l3.414 3.414a1 1 0 01.293.707V16a1 1 0 01-1 1h-1m-6-1a1 1 0 001 1h1M5 17a2 2 0 104 0m-4 0a2 2 0 114 0m6 0a2 2 0 104 0m-4 0a2 2 0 114 0"/>
                </svg>
            </div>
            
            <!-- Content -->
            <div class="bg-gradient-to-r from-blue-500/10 to-cyan-500/10 rounded-lg p-4 border border-blue-500/20">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-blue-400 font-medium text-sm">🚗 Sürüş ${Math.ceil((index + 1) / 2)}</span>
                    <span class="text-white font-bold">${leg.distance_km?.toFixed(1)} km</span>
                </div>
                <div class="grid grid-cols-3 gap-2 text-center text-xs">
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Süre</p>
                        <p class="text-white font-medium">${durationStr}</p>
                    </div>
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Tüketim</p>
                        <p class="text-yellow-400 font-medium">${leg.consumption_kwh?.toFixed(1)} kWh</p>
                    </div>
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Batarya</p>
                        <p class="text-white font-medium">${leg.start_soc_percent?.toFixed(0)}% → ${leg.end_soc_percent?.toFixed(0)}%</p>
                    </div>
                </div>
            </div>
        </div>
    `;
}

/**
 * Charge Leg Render
 * 🔧 V2.7: Google Places bilgileri (rating, vicinity) ve detaylı connector bilgileri eklendi
 */
function renderChargeLeg(leg, index) {
    const station = leg.station || {};
    const connector = station.connectors?.[0] || {};

    // Hava durumu bilgisi (varsa)
    const weather = leg.weather_context;
    const weatherHtml = weather ? `
        <div class="mt-3 pt-3 border-t border-yellow-500/20">
            <div class="flex items-center gap-2 text-xs">
                <span class="text-gray-400">🌡️</span>
                <span class="text-white">${weather.temp_c?.toFixed(1)}°C</span>
                <span class="text-gray-500">|</span>
                <span class="text-gray-400">${getWeatherEmoji(weather.condition)}</span>
                <span class="text-white">${formatWeatherCondition(weather.condition)}</span>
                ${weather.wind_speed_mps ? `
                    <span class="text-gray-500">|</span>
                    <span class="text-gray-400">💨</span>
                    <span class="text-white">${weather.wind_speed_mps?.toFixed(1)} m/s</span>
                ` : ''}
            </div>
        </div>
    ` : '';

    // Google Places rating (varsa)
    const rating = station.rating;
    const userRatings = station.user_ratings_total;
    const ratingHtml = (rating && rating > 0) ? `
        <div class="flex items-center gap-1 mt-1">
            <span class="text-yellow-400">⭐</span>
            <span class="text-white text-xs font-medium">${rating.toFixed(1)}</span>
            ${userRatings ? `<span class="text-gray-500 text-xs">(${userRatings})</span>` : ''}
        </div>
    ` : '';

    // Konum/adres bilgisi (vicinity)
    const vicinity = station.vicinity;
    const vicinityHtml = vicinity ? `
        <p class="text-gray-400 text-xs mt-1 truncate" title="${vicinity}">📍 ${vicinity}</p>
    ` : '';

    // Veri kaynağı badge
    const dataSource = station.data_source || 'unknown';
    const sourceBadge = dataSource === 'google'
        ? '<span class="bg-blue-500/20 text-blue-400 text-xs px-1.5 py-0.5 rounded">Google</span>'
        : dataSource === 'ocm'
            ? '<span class="bg-green-500/20 text-green-400 text-xs px-1.5 py-0.5 rounded">OCM</span>'
            : '';

    // Connector detayları
    const powerKw = connector.power_kw || 50;
    const plugType = connector.plug_type || 'CCS2';
    const chargerType = connector.charger_type || 'DC';

    // Rotadan sapma mesafesi
    const deviationKm = station.distance_from_route_km;
    const deviationHtml = (deviationKm && deviationKm > 0)
        ? `<span class="text-gray-500 text-xs">↗️ ${deviationKm.toFixed(1)} km sapma</span>`
        : '';

    // 🔧 V2.8: Amenities (tesis olanakları)
    const amenities = station.amenities || {};
    const amenityTags = [];
    if (amenities.has_toilet) amenityTags.push('🚻 WC');
    if (amenities.has_food) amenityTags.push('🍽️ Yemek');
    if (amenities.has_shopping) amenityTags.push('🛒 Market');
    if (amenities.has_parking) amenityTags.push('🅿️ Otopark');
    if (station.is_open_now === true) amenityTags.push('✅ Açık');
    else if (station.is_open_now === false) amenityTags.push('❌ Kapalı');

    const amenitiesHtml = amenityTags.length > 0 ? `
        <div class="flex flex-wrap gap-1 mt-2">
            ${amenityTags.map(tag => `<span class="bg-white/10 text-gray-300 text-xs px-1.5 py-0.5 rounded">${tag}</span>`).join('')}
        </div>
    ` : '';

    return `
        <div class="relative pl-10">
            <!-- Icon -->
            <div class="absolute left-0 w-8 h-8 bg-yellow-500/20 rounded-full flex items-center justify-center border-2 border-yellow-500">
                <svg class="w-4 h-4 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                </svg>
            </div>
            
            <!-- Content -->
            <div class="bg-gradient-to-r from-yellow-500/10 to-orange-500/10 rounded-lg p-4 border border-yellow-500/20">
                <!-- Header: İstasyon adı + Güç -->
                <div class="flex items-start justify-between mb-3">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-yellow-400 font-medium text-sm">⚡ Şarj Durağı</span>
                            ${sourceBadge}
                        </div>
                        <p class="text-white font-medium text-sm mt-1">${station.name || 'Şarj İstasyonu'}</p>
                        ${ratingHtml}
                        ${vicinityHtml}
                        ${deviationHtml}
                    </div>
                    <div class="text-right ml-3">
                        <p class="text-white font-bold text-lg">${powerKw} kW</p>
                        <p class="text-gray-400 text-xs">${plugType} • ${chargerType}</p>
                    </div>
                </div>
                
                <!-- Stats Grid -->
                <div class="grid grid-cols-5 gap-2 text-center text-xs">
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Varış</p>
                        <p class="text-red-400 font-medium">${leg.arrival_soc_percent?.toFixed(0)}%</p>
                    </div>
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Hedef</p>
                        <p class="text-green-400 font-medium">${leg.target_soc_percent?.toFixed(0)}%</p>
                    </div>
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Süre</p>
                        <p class="text-blue-400 font-medium">${leg.duration_minutes?.toFixed(0)} dk</p>
                    </div>
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Enerji</p>
                        <p class="text-yellow-400 font-medium">+${leg.energy_added_kwh?.toFixed(1)} kWh</p>
                    </div>
                    <div class="bg-white/5 rounded p-2">
                        <p class="text-gray-500">Maliyet</p>
                        <p class="text-orange-400 font-medium">${leg.estimated_cost ? leg.estimated_cost.toFixed(0) + '₺' : '-'}</p>
                    </div>
                </div>
                
                <!-- Amenities -->
                ${amenitiesHtml}
                
                <!-- Weather -->
                ${weatherHtml}
                
                <!-- 🔧 V3.1: Action Buttons -->
                <div class="flex flex-wrap gap-2 mt-3 pt-3 border-t border-yellow-500/20">
                    <!-- Feedback Button -->
                    <button 
                        onclick="openFeedbackModal('${station.id}', '${station.name?.replace(/'/g, "\\'")}', ${index})"
                        class="flex items-center gap-1 px-2 py-1 text-xs bg-red-500/20 text-red-400 rounded hover:bg-red-500/30 transition-colors"
                    >
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
                        </svg>
                        Sorun Bildir
                    </button>
                    
                    <!-- Alternatives Button (if available) -->
                    ${leg.alternative_stations && leg.alternative_stations.length > 0 ? `
                        <button 
                            onclick="showAlternativeStations(${index}, ${JSON.stringify(leg.alternative_stations).replace(/"/g, '&quot;')})"
                            class="flex items-center gap-1 px-2 py-1 text-xs bg-blue-500/20 text-blue-400 rounded hover:bg-blue-500/30 transition-colors"
                        >
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"/>
                            </svg>
                            Alternatifler (${leg.alternative_stations.length})
                        </button>
                    ` : ''}
                </div>
            </div>
        </div>
    `;
}

/**
 * Hava durumu için emoji döndür
 */
function getWeatherEmoji(condition) {
    const emojis = {
        'clear': '☀️',
        'sunny': '☀️',
        'cloudy': '☁️',
        'partly_cloudy': '⛅',
        'rain': '🌧️',
        'light_rain': '🌦️',
        'heavy_rain': '⛈️',
        'snow': '❄️',
        'fog': '🌫️',
        'wind': '💨'
    };
    return emojis[condition?.toLowerCase()] || '🌡️';
}

/**
 * Hava durumu condition'ını Türkçe'ye çevir
 */
function formatWeatherCondition(condition) {
    const translations = {
        'clear': 'Açık',
        'sunny': 'Güneşli',
        'cloudy': 'Bulutlu',
        'partly_cloudy': 'Parçalı Bulutlu',
        'rain': 'Yağmurlu',
        'light_rain': 'Hafif Yağmur',
        'heavy_rain': 'Şiddetli Yağmur',
        'snow': 'Karlı',
        'fog': 'Sisli',
        'wind': 'Rüzgarlı'
    };
    return translations[condition?.toLowerCase()] || condition || 'Bilinmiyor';
}

/**
 * Charge stops render (Legacy - backward compatibility)
 */
function renderChargeStops(legs) {
    return renderMultiLegs(legs);
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


// ============================================
// 🔧 V3.1: FEEDBACK & ALTERNATIVES SYSTEM
// ============================================

// Global state for feedback
let currentFeedbackData = null;
let currentRouteData = null;
let excludedStationIds = [];

/**
 * Rota verisini sakla (feedback için)
 */
function storeRouteData(data) {
    currentRouteData = data;
}

/**
 * Feedback modal'ını aç
 */
function openFeedbackModal(stationId, stationName, legIndex) {
    currentFeedbackData = { stationId, stationName, legIndex };

    const modalHtml = `
        <div id="feedbackModal" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
            <div class="bg-slate-800 rounded-2xl p-6 max-w-md w-full border border-white/10">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-white font-semibold text-lg">⚠️ Sorun Bildir</h3>
                    <button onclick="closeFeedbackModal()" class="text-gray-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                    </button>
                </div>
                
                <p class="text-gray-400 text-sm mb-4">
                    <span class="text-yellow-400">${stationName}</span> istasyonu için sorun bildirin:
                </p>
                
                <div class="space-y-2 mb-4">
                    <button onclick="submitFeedback('station_broken')" class="w-full p-3 bg-white/5 hover:bg-red-500/20 rounded-lg text-left text-white text-sm transition-colors flex items-center gap-2">
                        <span class="text-red-400">🔧</span> İstasyon Arızalı
                    </button>
                    <button onclick="submitFeedback('station_occupied')" class="w-full p-3 bg-white/5 hover:bg-yellow-500/20 rounded-lg text-left text-white text-sm transition-colors flex items-center gap-2">
                        <span class="text-yellow-400">🚗</span> İstasyon Meşgul
                    </button>
                    <button onclick="submitFeedback('soc_low')" class="w-full p-3 bg-white/5 hover:bg-orange-500/20 rounded-lg text-left text-white text-sm transition-colors flex items-center gap-2">
                        <span class="text-orange-400">🔋</span> Şarjım Yetmedi
                    </button>
                    <button onclick="submitFeedback('high_price')" class="w-full p-3 bg-white/5 hover:bg-purple-500/20 rounded-lg text-left text-white text-sm transition-colors flex items-center gap-2">
                        <span class="text-purple-400">💰</span> Yüksek Ücret
                    </button>
                    <button onclick="submitFeedback('wrong_location')" class="w-full p-3 bg-white/5 hover:bg-blue-500/20 rounded-lg text-left text-white text-sm transition-colors flex items-center gap-2">
                        <span class="text-blue-400">📍</span> Yanlış Konum
                    </button>
                    <button onclick="submitFeedback('private_property')" class="w-full p-3 bg-white/5 hover:bg-gray-500/20 rounded-lg text-left text-white text-sm transition-colors flex items-center gap-2">
                        <span class="text-gray-400">🚫</span> Özel Mülk / Erişilemiyor
                    </button>
                </div>
                
                <button onclick="closeFeedbackModal()" class="w-full p-2 text-gray-400 hover:text-white text-sm transition-colors">
                    İptal
                </button>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

/**
 * Feedback modal'ını kapat
 */
function closeFeedbackModal() {
    const modal = document.getElementById('feedbackModal');
    if (modal) modal.remove();
    currentFeedbackData = null;
}

/**
 * Feedback gönder ve rotayı yeniden hesapla
 */
async function submitFeedback(feedbackType) {
    if (!currentFeedbackData || !currentRouteData) {
        alert('Hata: Rota verisi bulunamadı');
        closeFeedbackModal();
        return;
    }

    // Modal'ı güncelle - loading state
    const modal = document.getElementById('feedbackModal');
    if (modal) {
        modal.innerHTML = `
            <div class="bg-slate-800 rounded-2xl p-6 max-w-md w-full border border-white/10 text-center">
                <div class="animate-spin w-8 h-8 border-2 border-green-500 border-t-transparent rounded-full mx-auto mb-4"></div>
                <p class="text-white">Yeni rota hesaplanıyor...</p>
            </div>
        `;
    }

    try {
        // Excluded stations listesine ekle
        excludedStationIds.push(currentFeedbackData.stationId);

        const response = await fetch('/station_feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                station_id: currentFeedbackData.stationId,
                feedback_type: feedbackType,
                current_location: {
                    lat: parseFloat(document.getElementById('startLocation').dataset.lat || 41.0082),
                    lon: parseFloat(document.getElementById('startLocation').dataset.lon || 28.9784)
                },
                current_soc_percent: parseFloat(document.getElementById('currentSoc')?.value || 80),
                destination: {
                    lat: parseFloat(document.getElementById('endLocation').dataset.lat || 39.9334),
                    lon: parseFloat(document.getElementById('endLocation').dataset.lon || 32.8597)
                },
                vehicle_model_id: document.getElementById('vehicleModel')?.value || 'mg4_51kwh',
                excluded_station_ids: excludedStationIds
            })
        });

        const data = await response.json();

        if (data.status === 'success' && data.route) {
            // Yeni rotayı göster
            storeRouteData(data.route);
            displayRouteResults(data.route);
            showSuccessToast('Rota yeniden hesaplandı!');
        } else {
            showErrorToast(data.message || 'Rota hesaplanamadı');
        }

    } catch (error) {
        console.error('Feedback error:', error);
        showErrorToast('Bağlantı hatası');
    }

    closeFeedbackModal();
}

/**
 * Alternatif istasyonları göster
 */
function showAlternativeStations(legIndex, alternatives) {
    // JSON parse if string
    const altList = typeof alternatives === 'string' ? JSON.parse(alternatives) : alternatives;

    const modalHtml = `
        <div id="alternativesModal" class="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
            <div class="bg-slate-800 rounded-2xl p-6 max-w-lg w-full border border-white/10 max-h-[80vh] overflow-y-auto">
                <div class="flex items-center justify-between mb-4">
                    <h3 class="text-white font-semibold text-lg">🔄 Alternatif İstasyonlar</h3>
                    <button onclick="closeAlternativesModal()" class="text-gray-400 hover:text-white">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                    </button>
                </div>
                
                <p class="text-gray-400 text-sm mb-4">
                    Bu bacak için ${altList.length} alternatif istasyon bulundu:
                </p>
                
                <div class="space-y-3">
                    ${altList.map((alt, i) => `
                        <div class="p-3 bg-white/5 hover:bg-blue-500/10 rounded-lg border border-white/10 cursor-pointer transition-colors"
                             onclick="selectAlternativeStation(${legIndex}, '${alt.id}', ${JSON.stringify(alt).replace(/"/g, '&quot;')})">
                            <div class="flex items-start justify-between">
                                <div class="flex-1">
                                    <p class="text-white font-medium text-sm">${alt.name}</p>
                                    <p class="text-gray-400 text-xs mt-1">${alt.vicinity || ''}</p>
                                    ${alt.rating ? `
                                        <div class="flex items-center gap-1 mt-1">
                                            <span class="text-yellow-400 text-xs">⭐ ${alt.rating.toFixed(1)}</span>
                                            ${alt.user_ratings_total ? `<span class="text-gray-500 text-xs">(${alt.user_ratings_total})</span>` : ''}
                                        </div>
                                    ` : ''}
                                </div>
                                <div class="text-right ml-3">
                                    <p class="text-white font-bold">${alt.connectors?.[0]?.power_kw || 120} kW</p>
                                    <p class="text-gray-400 text-xs">${alt.distance_from_route_km?.toFixed(1) || '?'} km sapma</p>
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
                
                <button onclick="closeAlternativesModal()" class="w-full mt-4 p-2 text-gray-400 hover:text-white text-sm transition-colors">
                    İptal
                </button>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', modalHtml);
}

/**
 * Alternatifler modal'ını kapat
 */
function closeAlternativesModal() {
    const modal = document.getElementById('alternativesModal');
    if (modal) modal.remove();
}

/**
 * Alternatif istasyon seç ve rotayı yeniden planla
 * 🔧 V3.2: /switch_station endpoint'i ile doğru istasyon değişimi
 */
async function selectAlternativeStation(legIndex, newStationId, newStation) {
    // JSON parse if string
    const stationData = typeof newStation === 'string' ? JSON.parse(newStation) : newStation;

    closeAlternativesModal();

    // Loading toast
    showInfoToast('İstasyon değiştiriliyor ve rota yeniden planlanıyor...');

    try {
        // Mevcut rota verisinden bilgileri al
        if (!currentRouteData) {
            showErrorToast('Rota verisi bulunamadı');
            return;
        }

        showLoading();

        // Mevcut bacaktan orijinal istasyon bilgisini al
        const currentLeg = currentRouteData.legs?.[legIndex];
        const originalStationId = currentLeg?.station?.id || '';

        // Sonraki şarj bacağını bul (etki analizi için)
        let nextStationLocation = null;
        for (let i = legIndex + 1; i < currentRouteData.legs.length; i++) {
            if (currentRouteData.legs[i].type === 'charge') {
                nextStationLocation = currentRouteData.legs[i].station?.location;
                break;
            }
        }

        // Varış noktasını son drive leg'den al
        const lastLeg = currentRouteData.legs[currentRouteData.legs.length - 1];
        const destination = lastLeg?.end_point || lastLeg?.station?.location;

        // Kullanıcının mevcut konumu (başlangıç noktası varsay)
        const firstLeg = currentRouteData.legs[0];
        const currentLocation = firstLeg?.start_point || { lat: 41.0082, lon: 28.9784 };

        // Batarya kapasitesini al (vehicle model'den)
        const vehicleModelId = document.getElementById('vehicleModel')?.value || 'mg4_51kwh';
        const batteryCapacity = 51.0; // Default, backend'de hesaplanacak

        // /switch_station endpoint'ini çağır
        const response = await fetch('/switch_station', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                original_station_id: originalStationId,
                new_station_id: newStationId,
                new_station: stationData,
                leg_index: legIndex,
                current_location: currentLocation,
                current_soc_percent: parseFloat(document.getElementById('currentSoc')?.value || 80),
                destination: destination,
                vehicle_model_id: vehicleModelId,
                next_station_location: nextStationLocation,
                battery_capacity_kwh: batteryCapacity
            })
        });

        const data = await response.json();
        hideLoading();

        if (data.status === 'success' || data.status === 'full_recalculate') {
            if (data.route) {
                // Tam rota yeniden hesaplandı
                storeRouteData(data.route);
                showResults(data.route);
                showSuccessToast(`Rota güncellendi! ${stationData.name}`);
            } else {
                // Tek bacak güncellendi - mevcut rotada sadece istasyonu değiştir
                updateSingleLegStation(legIndex, stationData);
                showSuccessToast(`İstasyon değiştirildi: ${stationData.name}`);
            }
        } else {
            showErrorToast('İstasyon değiştirilemedi: ' + (data.message || 'Bilinmeyen hata'));
        }
    } catch (error) {
        hideLoading();
        showErrorToast('Hata: ' + error.message);
    }
}

/**
 * 🔧 V3.2: Tek bacaktaki istasyonu güncelle (UI only)
 */
function updateSingleLegStation(legIndex, newStation) {
    if (!currentRouteData || !currentRouteData.legs[legIndex]) return;

    // Mevcut rotadaki istasyonu güncelle
    currentRouteData.legs[legIndex].station = newStation;

    // UI'ı yeniden render et
    showResults(currentRouteData);
}

/**
 * Form verilerini topla (yeniden planlama için) - async geocoding dahil
 */
async function getFormDataAsync() {
    const aiMode = document.getElementById('aiPlanningMode')?.checked ?? true;

    // Adresleri koordinata çevir
    const startAddress = document.getElementById('startLocation')?.value || '';
    const endAddress = document.getElementById('endLocation')?.value || '';

    const [startLocation, endLocation] = await Promise.all([
        geocodeAddress(startAddress),
        geocodeAddress(endAddress)
    ]);

    const formData = {
        start_location: startLocation,
        end_location: endLocation,
        vehicle_model_id: document.getElementById('vehicleModel')?.value || '',
        current_soc_percent: parseInt(document.getElementById('initialSoc')?.value) || 85,
        route_strategy: document.getElementById('routeStrategy')?.value || 'optimal',
        preferences: getStationPreferences()
    };

    // Yolcu ve yük bilgileri
    const passengerCount = parseInt(document.getElementById('passengerCount')?.value) || 1;
    const childCount = parseInt(document.getElementById('childCount')?.value) || 0;
    const extraLoad = parseInt(document.getElementById('extraLoad')?.value) || 0;

    if (passengerCount > 1) formData.passenger_count = passengerCount;
    if (childCount > 0) formData.child_count = childCount;
    if (extraLoad > 0) formData.extra_load_kg = extraLoad;

    // Çıkış zamanı
    const departureTime = document.getElementById('departureTime')?.value;
    if (departureTime) {
        formData.departure_time_iso = new Date(departureTime).toISOString();
    }

    return formData;
}

/**
 * Toast bildirimleri
 */
function showSuccessToast(message) {
    showToast(message, 'green');
}

function showErrorToast(message) {
    showToast(message, 'red');
}

function showInfoToast(message) {
    showToast(message, 'blue');
}

function showToast(message, color) {
    const toast = document.createElement('div');
    toast.className = `fixed bottom-4 right-4 bg-${color}-500/90 text-white px-4 py-2 rounded-lg shadow-lg z-50 animate-pulse`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), 3000);
}
