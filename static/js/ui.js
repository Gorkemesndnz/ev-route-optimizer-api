/**
 * EV Route Optimizer - UI Functions
 * Version: 1.5
 * 
 * Passenger controls ve toggle fonksiyonları
 */

// ============================================
// PASSENGER CONTROLS
// ============================================

/**
 * Yolcu sayısını artır/azalt
 * @param {number} delta - Değişim miktarı (+1 veya -1)
 */
function adjustPassengers(delta) {
    const input = document.getElementById('passengerCount');
    let value = parseInt(input.value) + delta;
    value = Math.max(1, Math.min(5, value));
    input.value = value;
    updatePassengerIcons(value);
}

/**
 * Yolcu ikonlarını güncelle
 * @param {number} count - Yolcu sayısı
 */
function updatePassengerIcons(count) {
    const icons = document.getElementById('passengerIcons');
    icons.textContent = '👤'.repeat(count);
}

// ============================================
// ADVANCED SOC TOGGLE
// ============================================

let advancedSocEnabled = false;

/**
 * Gelişmiş SOC ayarlarını göster/gizle
 */
function toggleAdvancedSoc() {
    advancedSocEnabled = !advancedSocEnabled;
    const inputs = document.getElementById('socInputs');
    const icon = document.getElementById('toggleIcon');
    
    if (advancedSocEnabled) {
        inputs.style.display = 'grid';
        icon.textContent = '▼ Kapat';
        icon.classList.replace('text-gray-500', 'text-green-400');
    } else {
        inputs.style.display = 'none';
        icon.textContent = '▶ Aç';
        icon.classList.replace('text-green-400', 'text-gray-500');
    }
}

// ============================================
// UI STATE MANAGEMENT
// ============================================

/**
 * Loading durumunu göster
 */
function showLoading() {
    document.querySelector('.loading').style.display = 'block';
    document.getElementById('resultCard').style.display = 'none';
    document.getElementById('errorCard').style.display = 'none';
    document.getElementById('defaultState').style.display = 'none';
}

/**
 * Loading durumunu gizle
 */
function hideLoading() {
    document.querySelector('.loading').style.display = 'none';
}

/**
 * Default state'i göster
 */
function showDefaultState() {
    document.getElementById('defaultState').style.display = 'flex';
    document.getElementById('resultCard').style.display = 'none';
    document.getElementById('errorCard').style.display = 'none';
}
