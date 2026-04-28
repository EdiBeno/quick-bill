// ===============================
// CURRENCY SYMBOLS MAP
// ===============================
const currencySymbols = {
    "he": "₪", "en": "$", "ru": "₽", "fr": "€", "de": "€", "es": "€",
    "it": "€", "nl": "€", "pt": "€", "el": "€", "ro": "lei", "tr": "₺",
    "ar": "﷼", "zh": "¥", "ja": "¥", "hi": "₹", "ko": "₩", "pl": "zł",
    "uk": "₴", "fa": "﷼", "cs": "Kč", "sv": "kr", "th": "฿", "vi": "₫",
    "bn": "৳", "id": "Rp", "ms": "RM", "tl": "₱", "hu": "Ft", "bg": "лв"
};

// ===============================
// CONFIGURATION MAP (ALL COUNTRIES)
// ===============================
const configMap = {
    "he": { country: "IL", currency: "ILS", locale: "he-IL", dir: "rtl" },
    "en": { country: "US", currency: "USD", locale: "en-US", dir: "ltr" },
    "fr": { country: "FR", currency: "EUR", locale: "fr-FR", dir: "ltr" },
    "es": { country: "ES", currency: "EUR", locale: "es-ES", dir: "ltr" },
    "de": { country: "DE", currency: "EUR", locale: "de-DE", dir: "ltr" },
    "ru": { country: "RU", currency: "RUB", locale: "ru-RU", dir: "ltr" },
    "ar": { country: "SA", currency: "SAR", locale: "ar-SA", dir: "rtl" },
    "zh": { country: "CN", currency: "CNY", locale: "zh-CN", dir: "ltr" },
    "ja": { country: "JP", currency: "JPY", locale: "ja-JP", dir: "ltr" },
    "hi": { country: "IN", currency: "INR", locale: "hi-IN", dir: "ltr" },
    "pt": { country: "PT", currency: "EUR", locale: "pt-PT", dir: "ltr" },
    "it": { country: "IT", currency: "EUR", locale: "it-IT", dir: "ltr" },
    "nl": { country: "NL", currency: "EUR", locale: "nl-NL", dir: "ltr" },
    "sv": { country: "SE", currency: "SEK", locale: "sv-SE", dir: "ltr" },
    "tr": { country: "TR", currency: "TRY", locale: "tr-TR", dir: "ltr" },
    "ko": { country: "KR", currency: "KRW", locale: "ko-KR", dir: "ltr" },
    "pl": { country: "PL", currency: "PLN", locale: "pl-PL", dir: "ltr" },
    "uk": { country: "UA", currency: "UAH", locale: "uk-UA", dir: "ltr" },
    "fa": { country: "IR", currency: "IRR", locale: "fa-IR", dir: "rtl" },
    "ro": { country: "RO", currency: "RON", locale: "ro-RO", dir: "ltr" },
    "cs": { country: "CZ", currency: "CZK", locale: "cs-CZ", dir: "ltr" },
    "el": { country: "GR", currency: "EUR", locale: "el-GR", dir: "ltr" },
    "th": { country: "TH", currency: "THB", locale: "th-TH", dir: "ltr" },
    "vi": { country: "VN", currency: "VND", locale: "vi-VN", dir: "ltr" },
    "bn": { country: "BD", currency: "BDT", locale: "bn-BD", dir: "ltr" },
    "id": { country: "ID", currency: "IDR", locale: "id-ID", dir: "ltr" },
    "ms": { country: "MY", currency: "MYR", locale: "ms-MY", dir: "ltr" },
    "tl": { country: "PH", currency: "PHP", locale: "tl-PH", dir: "ltr" },
    "hu": { country: "HU", currency: "HUF", locale: "hu-HU", dir: "ltr" },
    "bg": { country: "BG", currency: "BGN", locale: "bg-BG", dir: "ltr" }
};

// ===============================
// UNIVERSAL FORMAT ENGINE (30 LANGS)
// ===============================
const FormatEngine = {
    getConfig() {
        const lang = localStorage.getItem("lang") || "he";
        return configMap[lang] || configMap["he"];
    },

    // PARSE STRING → NUMBER (ANY LOCALE)
    parse(str) {
        if (typeof str === "number") return str;
        if (!str) return 0;

        const cfg = this.getConfig();
        const example = (1234.5).toLocaleString(cfg.locale);

        const thousandSep = example[1]; // "." or ","
        const decimalSep = example[5];  // "," or "."

        let normalized = String(str)
            .trim()
            .replace(/\s/g, "")
            .replace(new RegExp("\\" + thousandSep, "g"), "")
            .replace(new RegExp("\\" + decimalSep), ".");

        return parseFloat(normalized) || 0;
    },

    // FORMAT NUMBER → STRING
    format(num, digits = 2) {
        const cfg = this.getConfig();
        return Number(num || 0).toLocaleString(cfg.locale, {
            minimumFractionDigits: digits,
            maximumFractionDigits: digits
        });
    },

    // FORMAT CURRENCY
    currency(num) {
        const cfg = this.getConfig();
        return Number(num || 0).toLocaleString(cfg.locale, {
            style: "currency",
            currency: cfg.currency
        });
    },

    // RTL / LTR
    isRTL() {
        return this.getConfig().dir === "rtl";
    }
};

// ===============================
// HELPERS
// ===============================
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

function formatCurrency(amount) {
    // משתמש עכשיו ב‑FormatEngine כדי שלא יהיו עיוותים
    return FormatEngine.currency(amount);
}

function t(key) {
    if (key === 'currency_symbol') {
        const lang = localStorage.getItem("lang") || "he";
        return currencySymbols[lang] || "$";
    }
    return window.currentLangData ? window.currentLangData[key] : key;
}

// ===============================
// MAIN FUNCTIONS
// ===============================

function setLanguage(lang) {
    const config = configMap[lang] || configMap["he"];
    
    localStorage.setItem("lang", lang);
    document.cookie = "lang=" + lang + "; path=/; max-age=31536000; SameSite=Lax";
    document.cookie = "country=" + config.country + "; path=/; max-age=31536000; SameSite=Lax";
    document.cookie = "currency=" + config.currency + "; path=/; max-age=31536000; SameSite=Lax";

    document.documentElement.dir = config.dir;
    document.documentElement.lang = lang;

    loadLanguage(lang);

    if (typeof updateAllPrices === "function") updateAllPrices();
}

function loadLanguage(lang) {
    fetch(`/static/${lang}.json`)
        .then(res => res.json())
        .then(data => {
            window.currentLangData = data;
            const country = getCookie("country") || "IL";

            // 1. תרגום טקסט רגיל (Labels)
            document.querySelectorAll("[data-i18n]").forEach(el => {
                const key = el.getAttribute("data-i18n");
                if (data[key]) {
                    el.textContent = data[key].replace("{{country}}", country);
                }
            });

            // 2. תרגום placeholders
            document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
                const key = el.getAttribute("data-i18n-placeholder");
                if (data[key]) el.placeholder = data[key];
            });

            // 3. תרגום אופציות
            document.querySelectorAll("option[data-i18n]").forEach(el => {
                const key = el.getAttribute("data-i18n");
                if (data[key]) el.textContent = data[key];
            });

            // 3B. תרגום חודשים
            if (data.months) {
                document.querySelectorAll("option[data-i18n-month]").forEach(el => {
                    const index = el.getAttribute("data-i18n-month");
                    el.textContent = data.months[index];
                });
            }

            // 4. תרגום תוויות מובייל
            translateMobileItemLabels();

            // 5. עדכון סמל מטבע
            const langCode = localStorage.getItem("lang") || "he";
            const symbol = currencySymbols[langCode] || "$";
            document.querySelectorAll(".currency-symbol").forEach(el => {
                el.textContent = symbol;
            });

            // 6. COMPANY DATA DISPLAY
            if (data.company_data) {
                const nameEl = document.querySelector(".company-name-display");
                if (nameEl) nameEl.textContent = data.company_data.name || "";
                
                const addrEl = document.querySelector(".company-address-display");
                if (addrEl) addrEl.textContent = data.company_data.address || "";
            }

            // 7. INPUT VALUES (data-i18n-value)
            document.querySelectorAll("[data-i18n-value]").forEach(el => {
                const key = el.getAttribute("data-i18n-value");
                if (data[key]) {
                    el.value = data[key];
                }
            });

        })
        .catch(err => console.error("Language load error:", err));
}

// ===============================
// MOBILE LABEL TRANSLATION (ITEMS)
// ===============================

function translateMobileItemLabels() {
    if (!window.currentLangData) return;
    const data = window.currentLangData;
    const mobileMap = {
        'product_code': ['מק"ט:', 'Product Code:', 'Item Code:'],
        'description': ['תיאור מוצר:', 'Description:', 'Product Description:'],
        'quantity': ['כמות:', 'Quantity:', 'Qty:'],
        'unit_price': ['מחיר יחידה:', 'Unit Price:', 'Price:'],
        'discount': ['הנחה:', 'Discount:'],
        'total': ['סה"כ שורה:', 'Total:', 'Line Total:']
    };

    document.querySelectorAll("#items-tbody td").forEach(td => {
        const currentLabel = td.getAttribute('data-label');
        if (!currentLabel) return;
        const entry = Object.entries(mobileMap).find(([key, labels]) =>
            labels.includes(currentLabel) || key === currentLabel
        );
        if (entry && data[entry[0]]) {
            td.setAttribute('data-label', data[entry[0]]);
        }
    });
}

// ===============================
// INITIALIZE
// ===============================

document.addEventListener("DOMContentLoaded", () => {
    const savedLang = getCookie("lang") || localStorage.getItem("lang") || "he";
    const select = document.getElementById("language");
    if (select) select.value = savedLang;
    setLanguage(savedLang);
});

// Export
window.loadLanguage = loadLanguage;
window.setLanguage = setLanguage;
window.formatCurrency = formatCurrency;
window.t = t;
window.translateMobileItemLabels = translateMobileItemLabels;
window.FormatEngine = FormatEngine;
