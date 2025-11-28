/**
 * EV Route Optimizer - UI Functions
 * Version: 1.6
 * 
 * Passenger controls ve toggle fonksiyonları
 * AI Planlama Modu desteği
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
    // Boş değer varsa 1'den başla
    let currentValue = parseInt(input.value) || 1;
    let value = currentValue + delta;
    value = Math.max(1, Math.min(5, value));
    input.value = value;
}

// ============================================
// CHILD CONTROLS
// ============================================

/**
 * Çocuk sayısını artır/azalt
 * @param {number} delta - Değişim miktarı (+1 veya -1)
 */
function adjustChildren(delta) {
    const input = document.getElementById('childCount');
    // Boş değer varsa 0'dan başla
    let currentValue = parseInt(input.value) || 0;
    let value = currentValue + delta;
    value = Math.max(0, Math.min(4, value));
    input.value = value;
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
