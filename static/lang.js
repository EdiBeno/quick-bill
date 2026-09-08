window.currentLangData = window.currentLangData || {};

// זיכרון מקומי לשערי חליפין (כדי למנוע פניות כפולות ל-API באותו סשן)
const exchangeRatesCache = {};

// CURRENCY SYMBOLS MAP
const currencySymbols = {
    "he": "₪", "en": "$", "ru": "₽", "fr": "€", "de": "€", "es": "€",
    "it": "€", "nl": "€", "pt": "€", "el": "€", "ro": "lei", "tr": "₺",
    "ar": "﷼", "zh": "¥", "ja": "¥", "hi": "₹", "ko": "₩", "pl": "zł",
    "uk": "₴", "fa": "﷼", "cs": "Kč", "sv": "kr", "th": "฿", "vi": "₫",
    "bn": "৳", "id": "Rp", "ms": "RM", "tl": "₱", "hu": "Ft", "bg": "лв"
};

// CONFIGURATION MAP (ALL COUNTRIES)
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

// UNIVERSAL FORMAT ENGINE (30 LANGS)
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

        const thousandSep = example[1]; 
        const decimalSep = example[5];  

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

    // פונקציית עזר למשיכת שערי חליפין בזמן אמת מ-API פתוח ויציב
    async fetchRates(base) {
        if (exchangeRatesCache[base]) return exchangeRatesCache[base];
        try {
            // תיקון כתובת ה-API הרשמית והיציבה
            const res = await fetch(`https://er-api.com{base}`);
            const data = await res.json();
            if (data.result === "success") {
                exchangeRatesCache[base] = data.rates;
                return data.rates;
            }
        } catch (e) {
            console.error("Exchange rates fetch failed:", e);
        }
        return null;
    },

    // FORMAT CURRENCY - גרסה רגילה ומהירה (סינכרונית) לחישובים והקלדה בזמן אמת במסך!
    currency(num) {
        const cfg = this.getConfig();
        return Number(num || 0).toLocaleString(cfg.locale, {
            style: "currency",
            currency: cfg.currency
        });
    },

    // FORMAT & CONVERT CURRENCY ASYNC - גרסה אסינכרונית נפרדת המשמשת רק את מנוע התרגום הכללי בהצגת דפים
    async currencyConvert(num, baseCurrency = null) {
        const cfg = this.getConfig();
        let finalAmount = Number(num || 0);

        // אם הוזן מטבע מקור לחשבונית והוא שונה ממטבע המערכת הנוכחי - מבצעים המרה
        if (baseCurrency && baseCurrency !== cfg.currency) {
            const rates = await this.fetchRates(baseCurrency);
            if (rates && rates[cfg.currency]) {
                finalAmount = finalAmount * rates[cfg.currency];
            }
        }

        return finalAmount.toLocaleString(cfg.locale, {
            style: "currency",
            currency: cfg.currency
        });
    },

    // RTL / LTR
    isRTL() {
        return this.getConfig().dir === "rtl";
    }
};


// פונקציית הגשר הקריטית שפתרה את שגיאת השפה בכרטיסיות ושעוני הנוכחות
function getJsTranslation(key, fallbackText) {
    if (window.currentLangData && window.currentLangData[key]) {
        return window.currentLangData[key];
    }
    return fallbackText;
}

// פונקציית גיבוי שקטה למניעת קריסות של סעיף 4 במנגנון הטעינה
function translateMobileItemLabels() {
    try {
        if (typeof window.translateMobileItemLabelsCustom === "function") {
            window.translateMobileItemLabelsCustom();
        }
    } catch (e) {
        console.warn("Mobile elements translation hook skipped:", e);
    }
}

// ===============================
// MAIN FUNCTIONS
// ===============================

function loadLanguage(lang) {
    fetch(`/static/${lang}.json`)
        .then(res => res.json())
        .then(async data => { // הפיכת הפונקציה ל-async לצורך המרת ה-API
            window.currentLangData = data;
            const country = getCookie("country") || "IL";

            // 1. תרגום טקסט רגיל
            document.querySelectorAll("[data-i18n]:not(option)").forEach(el => {
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
                if (data[key]) {
                    el.textContent = data[key].replace("{{country}}", country);
                }
            });

            // 3B. תרגום חודשים
            if (data.months) {
                document.querySelectorAll("option[data-i18n-month]").forEach(el => {
                    const index = el.getAttribute("data-i18n-month");
                    if (data.months[index]) el.textContent = data.months[index];
                });
            }

            // 4. תרגום תוויות מובייל
            translateMobileItemLabels();

            // 5. עדכון סמל מטבע כללי + מנגנון המרת מחירי חשבוניות אוטומטי
            const langCode = localStorage.getItem("lang") || "he";
            const symbol = currencySymbols[langCode] || "$";
            
            // עדכון סמלים סטטיים כלליים במערכת
            document.querySelectorAll(".currency-symbol").forEach(el => {
                el.textContent = symbol;
            });

            // >>> מנגנון הליבה האוטומטי החדש <<<
            // סורק וממיר אוטומטית שדות מחיר עם data-raw בכל 18 הדפים
            const priceElements = document.querySelectorAll("#sub-total-display, #discount-total-display, #vat-amount-display, #grand-total-display");
            
            if (priceElements.length > 0 && langCode !== "he") {
                const baseCurrency = "ILS"; // מטבע המקור של החשבוניות ב-DB
                
                // שימוש בלולאת for...of אסינכרונית חסינה שממתינה ל-API בצורה נכונה
                for (const el of priceElements) {
                    let baseAmount = parseFloat(el.getAttribute("data-raw"));
                    
                    // גיבוי בטוח אם אין data-raw על האלמנט
                    if (isNaN(baseAmount)) {
                        const rawText = el.innerText.replace(/[^\d.-]/g, "");
                        baseAmount = parseFloat(rawText) || 0;
                    }
                    
                    // קריאה למנוע ההמרה האסינכרוני המבודד שבנינו בתוך FormatEngine
                    const convertedPrice = await FormatEngine.currencyConvert(baseAmount, baseCurrency);
                    el.textContent = convertedPrice;
                }
            }

            // 6. COMPANY DATA DISPLAY
            // מונעים מה-JS לדרוס את עבודת התרגום העמוקה של הפייתון בדיסק
            console.log("Company and Customer translations are managed natively by Python dict engine. Skipping JS overwrite.");

            // 7. INPUT VALUES
            document.querySelectorAll("[data-i18n-value]").forEach(el => {
                const key = el.getAttribute("data-i18n-value");
                if (data[key]) {
                    el.value = data[key];
                }
            });

            if (typeof updateDays === "function") {
                updateDays();
            }

        })
        .catch(err => console.error("Language load error:", err));
}

// 1B. SET LANGUAGE ENGINE (FOR MAIN.PY)
function setLanguage(lang) {
    const config = configMap[lang] || configMap["he"];
    
    localStorage.setItem("lang", lang);
    document.cookie = "lang=" + lang + "; path=/; max-age=31536000; SameSite=Lax";
    document.cookie = "country=" + config.country + "; path=/; max-age=31536000; SameSite=Lax";
    document.cookie = "currency=" + config.currency + "; path=/; max-age=31536000; SameSite=Lax";    
    document.documentElement.dir = config.dir;
    document.documentElement.lang = lang;
    
    sessionStorage.clear();
    loadLanguage(lang);

    if (window.location.pathname === "/login" || window.location.pathname === "/" || document.getElementById("auth-form")) {
        const cookiePair = document.cookie.split('; ').find(row => row.trim().startsWith('lang='));
        let currentCookieVal = cookiePair ? cookiePair.split('=')[1] : '';
        
        if (currentCookieVal) {
            currentCookieVal = currentCookieVal.trim().replace(';', '');
        }
        
        if (currentCookieVal !== lang) {
            window.location.href = "/set_language/" + lang;
        }
    }
}

// 8. OPEN TAB / TOGGLE ROW ENGINE
function toggleRow(id, el) {
    const row = document.getElementById(id);
    if (!row) return;

    const icon = el ? el.querySelector('.toggle-icon') : null;
    const isHidden = window.getComputedStyle(row).display === 'none' || row.classList.contains('hidden-content');

    if (isHidden) {
        row.style.display = 'table-row';
        row.classList.remove('hidden-content');
        row.classList.add('show-content');
        if (icon) icon.textContent = '-';
    } else {
        row.style.display = 'none';
        row.classList.remove('show-content');
        row.classList.add('hidden-content');
        if (icon) icon.textContent = '+';
    }
}

// MOBILE LABEL TRANSLATION (ITEMS) - OPTIMIZED
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

    const reverseLookup = {};
    Object.entries(mobileMap).forEach(([key, labels]) => {
        reverseLookup[key.toLowerCase()] = key;
        labels.forEach(label => {
            const clean = label.replace(':', '').trim().toLowerCase();
            reverseLookup[clean] = key;
        });
    });

    document.querySelectorAll("#items-tbody td").forEach(td => {
        const currentLabel = td.getAttribute('data-label');
        if (!currentLabel) return;
        
        const cleanLabel = currentLabel.replace(':', '').trim().toLowerCase();
        const entryKey = reverseLookup[cleanLabel] || reverseLookup[currentLabel.toLowerCase()];
        
        if (entryKey && data[entryKey]) {
            td.setAttribute('data-label', data[entryKey]);
        }
    });
}

// UTILITIES
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
    return null;
}

function formatCurrency(amount) {
    return FormatEngine.currency(amount);
}

function t(key) {
    if (key === 'currency_symbol') {
        const lang = localStorage.getItem("lang") || "he";
        return currencySymbols[lang] || "$";
    }
    return window.currentLangData ? window.currentLangData[key] : key;
}

// INITIALIZE
document.addEventListener("DOMContentLoaded", () => {
    const savedLang = getCookie("lang") || localStorage.getItem("lang") || "he";
    const select = document.getElementById("language");
    if (select) select.value = savedLang;
    
    setLanguage(savedLang);

    const allForms = document.querySelectorAll("form");
    allForms.forEach(form => {
        form.addEventListener("submit", () => {
            sessionStorage.setItem("needs_hard_refresh", "true");
        });
    });

    if (sessionStorage.getItem("needs_hard_refresh") === "true") {
        sessionStorage.removeItem("needs_hard_refresh");
        window.location.reload();
    }
});

// Global Exports
window.loadLanguage = loadLanguage;
window.setLanguage = setLanguage;
window.formatCurrency = formatCurrency;
window.t = t;
window.translateMobileItemLabels = translateMobileItemLabels;
window.FormatEngine = FormatEngine;
window.toggleRow = toggleRow;
