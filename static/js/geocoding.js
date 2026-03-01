/**
 * EV Route Optimizer - Geocoding Functions
 * Version: 1.5
 * 
 * Adres → Koordinat dönüşümü
 */

// ============================================
// GEOCODING
// ============================================

/**
 * Adres string'ini koordinata çevir
 * @param {string} address - Adres (örn: "İstanbul, Türkiye")
 * @returns {Promise<{lat: number, lon: number}>} Koordinatlar
 * @throws {Error} Adres bulunamazsa hata fırlatır
 */
async function geocodeAddress(address) {
    const response = await fetch(`/geocode?address=${encodeURIComponent(address)}`);
    const data = await response.json();

    if (data.status !== 'success') {
        throw new Error(data.detail || `Adres bulunamadı: ${address}`);
    }

    return data.location; // {lat, lon}
}

/**
 * Birden fazla adresi paralel olarak çevir
 * @param {string[]} addresses - Adres dizisi
 * @returns {Promise<Array<{lat: number, lon: number}>>} Koordinat dizisi
 */
async function geocodeMultiple(addresses) {
    return Promise.all(addresses.map(addr => geocodeAddress(addr)));
}


/**
 * Adres önerileri (autocomplete) için API çağrısı
 * @param {string} query - Aranacak metin
 * @returns {Promise<Array<Object>>} Öneriler listesi
 */
async function fetchAutocomplete(query) {
    if (!query || query.length < 3) return [];
    
    try {
        const response = await fetch(`/autocomplete?query=${encodeURIComponent(query)}`);
        const data = await response.json();
        
        if (data.status === 'success') {
            return data.predictions || [];
        }
        return [];
    } catch (error) {
        console.error("Autocomplete fetch error:", error);
        return [];
    }
}
