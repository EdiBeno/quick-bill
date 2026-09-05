
// ==========================================
// CONFIGURATION & CONFIG MAP FOR PYTHON
// ==========================================
window.currentLangData = window.currentLangData || {};

// 1. CURRENCY SYMBOLS MAP
const currencySymbols = {
    "he": "₪", "en": "$", "ru": "₽", "fr": "€", "de": "€", "es": "€",
    "it": "€", "nl": "€", "pt": "€", "el": "€", "ro": "lei", "tr": "₺",
    "ar": "﷼", "zh": "¥", "ja": "¥", "hi": "₹", "ko": "₩", "pl": "zł",
    "uk": "₴", "fa": "﷼", "cs": "Kč", "sv": "kr", "th": "฿", "vi": "₫",
    "bn": "৳", "id": "Rp", "ms": "RM", "tl": "₱", "hu": "Ft", "bg": "лв"
};

// 2. CONFIGURATION MAP (ALL COUNTRIES + SYNCHRONIZED WITH PYTHON BACKEND)
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

// GLOBAL EXCHANGE STORAGE
let currentExchangeRate = 1;
let currentCurrencySymbol = "₪";

// 3. UNIVERSAL FORMAT ENGINE (30 LANGS)
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

    // FORMAT NUMBER → STRING (MATCHES PYTHON'S SMART FORMATTER FOR INDEPENDENT TENANTS)
    format(value, digits = 2) {
        const cfg = this.getConfig();
        try {
            // הפיכה דינמית לספרות בנגליות (১.৪১১,২০) ואירופאיות לפי ה-locale המדויק
            return Number(value || 0).toLocaleString(cfg.locale, {
                minimumFractionDigits: digits,
                maximumFractionDigits: digits
            });
        } catch (e) {
            return Number(value || 0).toFixed(digits);
        }
    },

    // FORMAT CURRENCY
    currency(num) {
        const cfg = this.getConfig();
        return this.format(num) + " " + (currencySymbols[localStorage.getItem("lang")] || cfg.currency);
    },

    // RTL / LTR
    isRTL() {
        return this.getConfig().dir === "rtl";
    }
};

// 4. HELPERS & TRANSLATION FUNCTIONS
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

function getJsTranslation(key, fallbackText) {
    if (window.currentLangData && window.currentLangData[key]) {
        return window.currentLangData[key];
    }
    return fallbackText;
}

// 5. LIVE REALTIME INVOICE HISTORICAL CURRENCY CONVERSION ENGINE
async function runVisualCurrencyConversion() {
    const cfg = FormatEngine.getConfig();
    const targetCurrency = cfg.currency || "ILS";

    // אם המטבע הוא שקל - מאפסים את ה-Rate ל-1 ולא פונים לשרת החיצוני
    if (targetCurrency === "ILS") {
        currentExchangeRate = 1;
        currentCurrencySymbol = "₪";
        applyVisualCurrencyToDOM();
        return;
    }

    // שואבים את תאריך החשבונית מתוך לוח השנה בבנגלית/עברית של הדף
    let invoiceDate = document.querySelector("input[name='date']")?.value || document.querySelector(".payment-date-input")?.value;
    if (!invoiceDate) {
        invoiceDate = new Date().toISOString().split('T')[0]; // Fallback
    }

    try {
        console.log(`⏳ Fetching historic rate (ILS -> ${targetCurrency}) for date: ${invoiceDate}...`);
 
        // פנייה ל-API לקבלת השער ההיסטורי המדויק לאותו יום של החשבונית       
        const response = await fetch(`https://frankfurter.app{invoiceDate}?from=ILS&to=${targetCurrency}`);
        if (!response.ok) throw new Error("Exchange API error response");
        
        const data = await response.json();
        currentExchangeRate = data.rates[targetCurrency] || 1;
        
        const langCode = localStorage.getItem("lang") || "he";
        currentCurrencySymbol = currencySymbols[langCode] || targetCurrency;

        console.log(`✔ Locked conversion rate: 1 ILS = ${currentExchangeRate} ${targetCurrency}`);
        applyVisualCurrencyToDOM();

    } catch (err) {
        console.warn("⚠️ Historical rate server offline. Fallback to base currency:", err);
        currentExchangeRate = 1;
        currentCurrencySymbol = "₪";
        applyVisualCurrencyToDOM();
    }
}

// 6. DOM VISUAL INJECTION LAYER (A4 DISPLAY + PAYMENTS RECOGNITION)
function applyVisualCurrencyToDOM() {
    // א. עדכון תיבת סכום סופי כללי (Grand Total סופי)
    const grandTotalEl = document.getElementById("grand-total-display") || document.querySelector(".total-amount-box");
    if (grandTotalEl) {
        if (!grandTotalEl.dataset.originalAmount) {
            grandTotalEl.dataset.originalAmount = grandTotalEl.value || grandTotalEl.textContent.replace(/[^0-9.]/g, '');
        }
        
        const baseAmount = parseFloat(grandTotalEl.dataset.originalAmount || 0);
        const calculatedAmount = (baseAmount * currentExchangeRate).toFixed(2);
        const localizedString = FormatEngine.format(calculatedAmount);

        if (grandTotalEl.tagName === "INPUT") {
            grandTotalEl.value = localizedString;
        } else {
            grandTotalEl.textContent = localizedString;
        }
    }

    // ב. עדכון ויזואלי של טבלת התשלומים (CASH / CREDIT) - שומר שקלים בפנים, ומציג מטבע מומר בחוץ
    const paymentInputs = document.querySelectorAll(".payment-table td input[type='number'], .payment-table .payment-amount-input");
    paymentInputs.forEach(input => {
        if (!input.dataset.originalAmount) {
            input.dataset.originalAmount = input.value || "0.00";
            
            // פוקוס עריכה: מחזיר לשקלים לצורך סליקה נקייה ושמירה
            input.addEventListener("focus", function() {
                this.value = this.dataset.originalAmount;
            });

            // יציאה מהשדה: נועל שקלים ומציג את הפורמט המתורגם ללקוח הזר
            input.addEventListener("blur", function() {
                this.dataset.originalAmount = this.value;
                const convertedPayment = (parseFloat(this.value || 0) * currentExchangeRate).toFixed(2);
                this.value = FormatEngine.format(convertedPayment);
                
                if (typeof calculateTotals === "function") calculateTotals();
            });
        }

        const basePayment = parseFloat(input.dataset.originalAmount || 0);
        const finalPayment = (basePayment * currentExchangeRate).toFixed(2);
        input.value = FormatEngine.format(finalPayment);
    });

    // ג. החלפת סמלי המטבע (₪) לסימנים המקומיים בכל רחבי הדף
    document.querySelectorAll(".currency-symbol, .mobile-currency-symbol").forEach(el => {
        el.textContent = currentCurrencySymbol;
    });
}

// 7. LANGUAGE LOADER & DOM UPDATER
function loadLanguage(lang) {
    fetch(`/static/${lang}.json`)
        .then(res => res.json())
        .then(data => {
            window.currentLangData = data;
            const country = getCookie("country") || "IL";

            // 1. תרגום תוויות טקסט רגיל
            document.querySelectorAll("[data-i18n]:not(option)").forEach(el => {
                const key = el.getAttribute("data-i18n");
                if (data[key]) {
                    el.textContent = data[key].replace("{{country}}", country);
                }
            });

            // 2. תרגום Placeholders
            document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
                const key = el.getAttribute("data-i18n-placeholder");
                if (data[key]) el.placeholder = data[key];
            });

            // 3. תרגום אלמנטים מסוג Option
            document.querySelectorAll("option[data-i18n]").forEach(el => {
                const key = el.getAttribute("data-i18n");
                if (data[key]) {
                    el.textContent = data[key].replace("{{country}}", country);
                }
            });

            // 3B. תרגום מערך חודשים לקומבובוקסים
            if (data.months) {
                document.querySelectorAll("option[data-i18n-month]").forEach(el => {
                    const index = el.getAttribute("data-i18n-month");
                    if (data.months[index]) el.textContent = data.months[index];
                });
            }

            // 4. תרגום תוויות מובייל
            translateMobileItemLabels();

            // 5. עדכון סמלי המטבע הראשוניים
            const langCode = localStorage.getItem("lang") || "he";
            const symbol = currencySymbols[langCode] || "$";
            document.querySelectorAll(".currency-symbol").forEach(el => {
                el.textContent = symbol;
            });

            // 6. תרגום נתוני פרופיל חברה בתצוגה הכללית
            if (data.company_data) {
                const nameEl = document.querySelector(".company-name-display");
                if (nameEl) nameEl.textContent = data.company_data.name || "";
                
                const addrEl = document.querySelector(".company-address-display");
                if (addrEl) addrEl.textContent = data.company_data.address || "";
            }

            // 7. עדכון ערכי קלט מוגדרים מראש
            document.querySelectorAll("[data-i18n-value]").forEach(el => {
                const key = el.getAttribute("data-i18n-value");
                if (data[key]) el.value = data[key];
            });

            // FIX הרמטי: העפת הקריאה השבורה ל-updateAllPrices שגרמה לקריסות, והוספת typeof מגן
            if (typeof updateAllPrices === "function") {
                updateAllPrices();
            }

            // סינכרון חוזר של תצוגת ימי כרטיסי הנוכחות
            if (typeof updateDays === "function") {
                updateDays();
            }

            // סעיף 8: הזנקת מנוע המרת המטבע הויזואלי לאותו יום של החשבונית
            runVisualCurrencyConversion();

            // כפיית סינכרון אקטיבי מול ראוטי השרת בדפי הכניסה והלוגין
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
        })
        .catch(err => console.error("Language loader failed:", err));
}

// 8. SET LANGUAGE ENGINE (INTERFACE BETWEEN CORES)
function setLanguage(lang) {
    const config = configMap[lang] || configMap["he"];
    
    localStorage.setItem("lang", lang);
    document.cookie = "lang=" + lang + "; path=/; max-age=31536000; SameSite=Lax";
    document.cookie = "country=" + config.country + "; path=/; max-age=31536000; SameSite=Lax";
    document.cookie = "currency=" + config.currency + "; path=/; max-age=31536000; SameSite=Lax";

    document.documentElement.dir = config.dir;
    document.documentElement.lang = lang;

    sessionStorage.clear(); // ניקוי ה-Cache לריענון אוטומטי חלק של כל 18 הדפים
    loadLanguage(lang);
}

// 9. MOBILE TRANSLATION MAPPING (REVERSE LOOKUP PROTECTED)
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

// 10. TOGGLE LAYOUT ENGINE
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

// 11. LIFECYCLE INITIALIZER & DOM READY HANDLER
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

// GLOBAL EXPORTS FOR PLATFORM SYNC
window.loadLanguage = loadLanguage;
window.setLanguage = setLanguage;
window.formatCurrency = formatCurrency;
window.t = t;
window.translateMobileItemLabels = translateMobileItemLabels;
window.FormatEngine = FormatEngine;
window.toggleRow = toggleRow;
window.runVisualCurrencyConversion = runVisualCurrencyConversion;
