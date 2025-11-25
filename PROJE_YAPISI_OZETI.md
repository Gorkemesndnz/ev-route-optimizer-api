# EV Route Optimizer API - Proje Yapısı Özeti

## Proje Genel Bakış
Bu proje, Elektrikli Araç (EA) rotaları optimize etmek için geliştirilmiş bir API'dir. Proje, rota planlama, enerji tüketimi hesaplama, şarj istasyonu bulma ve sürdürülebilirlik analizi gibi özellikler sunar.

---

## 📁 Klasör Yapısı ve Amaçları

### 🗂️ **Ana Kök Dizin**
- **`c:\Ev-Route-Optimizer-Api\`** - Projenin ana dizini

### 📄 **Kök Düzeyindeki Dosyalar**
- **`.env`** - Ortam değişkenleri ve API anahtarları
- **`.env.example`** - Ortam değişkenleri şablonu
- **`.git/`** - Git versiyon kontrolü dosyaları
- **`.gitattributes`** - Git özellik ayarları
- **`.gitignore`** - Git tarafından göz ardı edilecek dosyalar
- **`.venv/`** - Python sanal ortamı
- **`README.md`** - Proje açıklaması
- **`requirements.txt`** - Python bağımlılıkları
- **`test_request.json`** - Test isteği örnekleri
- **`static/index.html`** - Web arayüzü

---

## 🚀 **`app/` - Ana Uygulama Dizini**
API'nin temel işlevselliğini içeren ana dizindir.

### 📋 **Ana Modüller**
- **`main.py`** - FastAPI ana uygulama ve endpoint'ler
- **`models.py`** - Veri modelleri ve Pydantic şemaları
- **`route_planner.py`** - Rota planlama algoritması (10,085 bytes)
- **`route_selector.py`** - Rota seçim ve optimizasyon (5,069 bytes)
- **`station_finder.py`** - Şarj istasyonu bulma algoritması (16,420 bytes)
- **`sustainability_calculator.py`** - Sürdürülebilirlik hesaplamaları

---

## ⚡ **`app/consumption_engine/` - Tüketim Motoru**
Enerji tüketimi hesaplamaları için motor ve katmanlar.

### 🔧 **Ana Dosyalar**
- **`main_calculator.py`** - Ana tüketim hesaplayıcı
- **`vehicle_models.py`** - Araç modelleri ve özellikleri

### 📊 **Versiyonlama Sistemi**
#### **`v1_rule_based/` - Kural Tabanlı Sistem**
- **`elevation_layer.py`** - Rakım etkisi hesaplamaları
- **`load_layer.py`** - Yük etkisi hesaplamaları
- **`weather_layer.py`** - Hava durumu etkisi hesaplamaları

#### **`v2_ml_model/` - Makine Öğrenmesi Sistemi**
- *(Boş dizin - gelecekte ML modeli için ayrılmış)*

---

## 🌐 **`app/services/` - Harici Servisler**
Dış API'ler ve servislerle iletişim için modüller.

### 🔗 **Servis Modülleri**
- **`base_service.py`** - Temel servis sınıfı ve ortak fonksiyonlar
- **`google_service.py`** - Google Maps API entegrasyonu
- **`ocm_service.py`** - Open Charge Map API entegrasyonu
- **`weather_service.py`** - Hava durumu API entegrasyonu

---

## 🛠️ **`app/utils/` - Yardımcı Araçlar**
Genel amaçlı yardımcı fonksiyonlar ve araçlar.

### 📦 **Utility Modülleri**
- **`cache_manager.py`** - Önbellek yönetimi
- **`config_manager.py`** - Yapılandırma yönetimi
- **`data_logger.py`** - Veri loglama
- **`logger.py`** - Genel loglama sistemi

---

## 📓 **`notebooks/` - Jupyter Notebook'ları**
Veri analizi ve geliştirme için notebook'lar.

### 🔬 **Alt Dizinler**
- **`future_training_data/`** - Gelecekteki model eğitimi için veriler
  - **`v1_training_data.jsonl`** - V1 model eğitim verileri

---

## 🧪 **`tests/` - Test Dosyaları**
Birim testleri ve entegrasyon testleri.
- **`__init__.py`** - Test paketi başlatıcı

---

## 🌍 **`static/` - Statik Dosyalar**
Web arayüzü ve statik içerikler.
- **`index.html`** - Ana web arayüzü (10,465 bytes)

---

## 🔄 **İş Akışı Mantığı**

1. **Gelen İstek** → `main.py` (FastAPI endpoint)
2. **Rota Planlama** → `route_planner.py`
3. **İstasyon Bulma** → `station_finder.py`
4. **Tüketim Hesaplama** → `consumption_engine/`
5. **Harici Servisler** → `services/` (Google, OCM, Weather)
6. **Optimizasyon** → `route_selector.py`
7. **Sürdürülebilirlik** → `sustainability_calculator.py`
8. **Önbellek/Yapılandırma** → `utils/`

---

## 🎯 **Önemli Notlar**
- Proje modüler yapıda, her bileşen ayrı bir sorumluluğa sahip
- Tüketim motoru iki versiyon destekliyor: kural tabanlı ve ML tabanlı
- Harici servisler için soyutlama katmanı mevcut
- Gelecekte ML modeli entegrasyonu için altyapı hazır
- Web arayüzü statik dosya olarak sunuluyor

---
**Oluşturulma Tarihi:** 25.11.2025  
**Proje:** EV Route Optimizer API  
**Amaç:** Elektrikli araç rota optimizasyonu
