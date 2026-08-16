// static/hourscard.js
let localClockState = {
  isClockedIn: false,
  startTime: null,
  endTime: null,
  task: ""
};

// משתני מפתח גלובליים שיעודכנו דינמית מה-Session או מה-HTML
let CURRENT_EMPLOYEE_ID = null;
let CURRENT_COMPANY_ID = null;
let CURRENT_ID_NUMBER = null;

// Helper function to fetch real translation data matching your lang.js engine exactly
function getJsTranslation(key, fallbackText) {
  if (window.currentLangData && window.currentLangData[key]) {
    return window.currentLangData[key];
  }
  return fallbackText;
}

// --- פונקציית עזר גנרית שמחלצת את המיקום הגיאוגרפי החי של העובד מהדפדפן ---
function getUserLocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) {
      resolve("Not Supported");
      return;
    }
    
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const lat = position.coords.latitude.toFixed(6);
        const lng = position.coords.longitude.toFixed(6);
        resolve(`${lat},${lng}`);
      },
      (error) => {
        console.warn("Location access denied or unavailable:", error);
        resolve("Denied");
      },
      { enableHighAccuracy: true, timeout: 5000, maximumAge: 0 }
    );
  });
}

document.addEventListener("DOMContentLoaded", async function() {
  const clockInBtn = document.getElementById("clock-in-btn");
  const clockOutBtn = document.getElementById("clock-out-btn");
  const saveBtn = document.getElementById("save-btn");

  if (clockInBtn) clockInBtn.addEventListener("click", handleClockIn);
  if (clockOutBtn) clockOutBtn.addEventListener("click", handleClockOut);
  if (saveBtn) saveBtn.addEventListener("click", handleSave);

  const empInput = document.getElementById("employee-id") || document.getElementById("employee_id");
  const compInput = document.getElementById("company-id") || document.getElementById("company_id");
  const idInput = document.getElementById("id-number") || document.getElementById("id_number");

  CURRENT_EMPLOYEE_ID = empInput ? empInput.value : null;
  CURRENT_COMPANY_ID = compInput ? compInput.value : null;
  CURRENT_ID_NUMBER = idInput ? idInput.value : null;

  if (!CURRENT_EMPLOYEE_ID) {
    try {
      const res = await fetch('/api/current_user_info');
      const data = await res.json();
      
      if (data && data.employee_id) {
        CURRENT_EMPLOYEE_ID = data.employee_id;
        CURRENT_COMPANY_ID = data.company_id || CURRENT_COMPANY_ID;
        
        if (empInput) empInput.value = data.employee_id;
        if (compInput && data.company_id) compInput.value = data.company_id;
      }
    } catch (err) {
      console.error("Failed to fetch current user session info:", err);
    }
  }

  // שליפת הסטטוס החי והאמיץ של המשמרת מהדאטהבייס בשרת (מונע איפוסים!)
  if (CURRENT_EMPLOYEE_ID) {
    try {
      const statusRes = await fetch('/api/shiftstate');
      const shiftData = await statusRes.json();
      
      if (shiftData && (shiftData.isClockedIn === true || shiftData.isClockedIn === "true")) {
        localClockState.isClockedIn = true;
        localClockState.startTime = shiftData.startTime;
        localClockState.task = shiftData.task || "";
        
        const taskInp = document.getElementById("task");
        if (taskInp && shiftData.task) {
          taskInp.value = shiftData.task;
        }
      }
    } catch (err) {
      console.error("Failed to fetch shift state from server:", err);
    }
  }

  if (typeof updateDays === "function") {
    updateDays(); 
  }

  updateButtonsUI();
});

// --- עדכון ממשק המשתמש ---
function updateButtonsUI() {
  const statusText = document.getElementById("status-text");
  if (!statusText) return;

  // 🔒 מנעול פלדה אבסולוטי: אם המשתמש במשמרת, מקבע קשיח את הסטטוס הירוק!
  // שום לחיצת אישור ושום אירוע blur בדפדפן לא מסוגלים לשנות את זה לאדום בטעות יותר!
  if (localClockState.isClockedIn === true || localClockState.isClockedIn === "true") {
    statusText.setAttribute('data-i18n', 'employee.status_start');
    statusText.textContent = getJsTranslation('employee.status_start', "🟢 במשמרת");
    statusText.style.color = "lightgreen";
  } else if (localClockState.endTime && !localClockState.isClockedIn) {
    statusText.setAttribute('data-i18n', 'employee.status_pending_save');
    statusText.textContent = getJsTranslation('employee.status_pending_save', "🔴 נא לשמור דיווח");
    statusText.style.color = "orange";
  } else {
    statusText.setAttribute('data-i18n', 'employee.status_not_in_shift');
    statusText.textContent = getJsTranslation('employee.status_not_in_shift', "🔴 לא במשמרת");
    statusText.style.color = "red";
  }

  const inBtn = document.getElementById("clock-in-btn");
  const outBtn = document.getElementById("clock-out-btn");
  const saveBtn = document.getElementById("save-btn");
  const taskInp = document.getElementById("task");

  if (inBtn) {
    inBtn.disabled = localClockState.isClockedIn;
    inBtn.classList.toggle("disabled", localClockState.isClockedIn);
  }
  if (outBtn) {
    outBtn.disabled = !localClockState.isClockedIn;
    outBtn.classList.toggle("disabled", !localClockState.isClockedIn);
  }
  if (taskInp) {
    taskInp.disabled = !localClockState.isClockedIn;
  }

  const canSave = localClockState.endTime && !localClockState.isClockedIn;
  if (saveBtn) {
    saveBtn.disabled = !canSave;
    saveBtn.classList.toggle("disabled", !canSave);
  }

  const startDisplay = document.getElementById("start-time-display");
  const endDisplay = document.getElementById("end-time-display");
  if (startDisplay) startDisplay.textContent = formatTime(localClockState.startTime);
  if (endDisplay) endDisplay.textContent = formatTime(localClockState.endTime);
}

// --- פעולות כניסה ויציאה ---
async function handleClockIn() {
  const locationCoords = await getUserLocation();

  const now = new Date();
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');
  const localTimeStr = `${hours}:${minutes}:${seconds}`;

  localClockState.isClockedIn = true;
  localClockState.startTime = localTimeStr;
  localClockState.task = "";

  updateButtonsUI();

  await fetch("/api/clockin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      employee_id: CURRENT_EMPLOYEE_ID,
      startTime: localClockState.startTime,
      location: locationCoords
    })
  });

  await fetch("/api/record_time", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "START",
      time: localClockState.startTime,
      location: locationCoords
    })
  });

  alert(getJsTranslation('employee.alert_clock_in_success', "נכנסת למשמרת בהצלחה!"));
}


async function handleClockOut() {
  const taskInpElement = document.getElementById("task");
  const taskVal = taskInpElement ? taskInpElement.value.trim() : "";
  
  if (!taskVal) {
    alert(getJsTranslation('employee.alert_missing_task', "עליך למלא תיאור משימה לפני יציאה"));
    // כופה בדיקה חוזרת וקיבוע של הסטטוס הירוק כדי לחנוק אירועי שינוי פוקוס ברקע של הדפדפן
    updateButtonsUI(); 
    return; // 👈 חונק את הריצה המעוותת ומשאיר את העובד רשום קדוש בתוך המשמרת!
  }

  // חילוץ המיקום הגיאוגרפי בלייב מה-GPS של המכשיר ברגע הלחיצה על היציאה
  const locationCoords = await getUserLocation();

  const now = new Date();
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');
  const localTimeStr = `${hours}:${minutes}:${seconds}`;

  // שינוי הסטטוס מבוצע אך ורק לאחר שהמשימה עברה בהצלחה את חומת האש!
  localClockState.isClockedIn = false;
  localClockState.endTime = localTimeStr; // הזמן האמיתי של הלפטופ מקובע קשיח ללא שיבושים!
  localClockState.task = taskVal;

  updateButtonsUI();

  // שליחה ל-API של השרת
  await fetch("/api/clockout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      employee_id: CURRENT_EMPLOYEE_ID,
      endTime: localClockState.endTime,
      task: taskVal,
      location: locationCoords
    })
  });

  // סנכרון לוח זמנים מול קובץ ה-record_time של ה-CSV ליציאה
  await fetch("/api/record_time", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "END",
      time: localClockState.endTime,
      location: locationCoords
    })
  });

  alert(getJsTranslation('employee.alert_clock_out_success', "יציאה נרשמה. נא לשמור דיווח."));
}

async function handleSave() {
  if (!localClockState.startTime || !localClockState.endTime) {
    alert(getJsTranslation('employee.alert_no_report_ready', "אין דיווח מוכן לשמירה"));
    return;
  }

  // מניעת קריסות של מנוע ה-Date: מחשבים את הפרש השעות במדויק על בסיס זמני מערכת מקומיים لלא המרות UTC משבשות!
  const todayObj = new Date();
  const yearStr = todayObj.getFullYear();
  const monthStr = String(todayObj.getMonth() + 1).padStart(2, '0');
  const dayStr = String(todayObj.getDate()).padStart(2, '0');
  const dateKeyStr = `${yearStr}-${monthStr}-${dayStr}`;

  const start = new Date(`${dateKeyStr}T${localClockState.startTime}`);
  const end = new Date(`${dateKeyStr}T${localClockState.endTime}`);
  let diffHrs = ((end - start) / (1000 * 60 * 60));
  
  // הגנה על משמרות לילה: אם שעת הסיום קטנה משעת ההתחלה, מוסיפים אוטומטית יום שלם (24 שעות)
  if (diffHrs < 0) {
    diffHrs += 24;
  }
  diffHrs = diffHrs.toFixed(2);
  
  // משיכת המיקום האחרון לגיבוי בהיסטוריה 
  const locationCoords = await getUserLocation();

  const timesheetData = {
    employee_id: CURRENT_EMPLOYEE_ID,
    company_id: CURRENT_COMPANY_ID,
    id_number: CURRENT_ID_NUMBER,
    date: dateKeyStr, 
    startTime: localClockState.startTime,
    endTime: localClockState.endTime,
    startLocation: locationCoords, 
    endLocation: locationCoords,
    task: localClockState.task,
    totalHours: diffHrs
  };

  await fetch("/api/savetimesheet", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(timesheetData)
  });

  // הוספת הדיווח לטבלת ה-HTML באופן מיידי
  addTimesheetRow(timesheetData);

  // איפוס הסטטוס המקומי למצב נקי לקראת מחר        
  localClockState = { isClockedIn: false, startTime: null, endTime: null, task: "" };
  
  const taskInpElement = document.getElementById("task");
  if (taskInpElement) taskInpElement.value = ""; // ניקוי שדה המשימה על המסך

  if (typeof updateDays === "function") {
    updateDays(); // ריענון וטעינת הטבלה החודשית העליונה מחדש
  }

  updateButtonsUI();
  alert(getJsTranslation('employee.alert_save_success', "הדיווח נשמר בהצלחה!"));
}

// --- Helpers ---
function formatTime(timeStr) {
  if (!timeStr) return "--:--";
  const clean = str => str.trim().split("T").pop().substring(0, 5);
  return clean(timeStr);
}

// --- הוספת שורה חדשה לטבלת ההיסטוריה באופן דינמי ---
function addTimesheetRow(data) {
  const tableBody = document.getElementById("timesheets-list");
  if (!tableBody) return;
  
  const noDataRow = tableBody.querySelector("tr td.text-center");
  if (noDataRow) {
    noDataRow.parentElement.remove();
  }

  const newRow = document.createElement("tr");
  const hoursText = getJsTranslation('employee.hours_text', "שעות");

  newRow.innerHTML = `
    <td>${data.date}</td>
    <td>${data.startTime.substring(0, 5)} - ${data.endTime.substring(0, 5)}</td>
    <td>${data.task}</td>
    <td>${data.totalHours} <span data-i18n="employee.hours_text">${hoursText}</span></td>
    <td>✔</td>
  `;

  tableBody.insertBefore(newRow, tableBody.firstChild);
}
