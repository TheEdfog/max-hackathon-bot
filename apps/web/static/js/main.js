const DEFAULT_LOADING_MESSAGES = [
    "Упаковываем ваш опыт",
    "Сверяем факты с вакансией",
    "Отбираем сильные формулировки",
    "Проверяем, чтобы не выдумать лишнего",
    "Собираем аккуратный документ",
    "Выкидываем лишний шум",
    "Расставляем акценты для рекрутера",
    "Проверяем must-have требования",
    "Готовим понятный результат",
    "Финально полируем формулировки",
];

const analysisCacheKey = `rezumit:last-analysis:${document.body.dataset.userId || "guest"}`;
let loadingMessageTimer = null;
let warningOverlayState = null;

function createLoadingOverlay() {
    let overlay = document.querySelector(".loading-overlay");
    if (overlay) {
        return overlay;
    }

    overlay = document.createElement("div");
    overlay.className = "loading-overlay";
    overlay.setAttribute("role", "status");
    overlay.setAttribute("aria-live", "polite");
    overlay.innerHTML = `
        <div class="loading-panel">
            <span class="loading-spinner" aria-hidden="true"></span>
            <span class="loading-kicker">РезюмИТ работает</span>
            <strong class="loading-caption"></strong>
            <span class="loading-subtitle">Это может занять немного времени, особенно если подключается интеллектуальный анализ.</span>
        </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
}

function parseLoadingMessages(rawMessages) {
    const messages = (rawMessages || "")
        .split("|")
        .map((message) => message.trim())
        .filter(Boolean);
    return messages.length ? messages : DEFAULT_LOADING_MESSAGES;
}

function showLoadingOverlay(rawMessages) {
    const captions = parseLoadingMessages(rawMessages);
    const overlay = createLoadingOverlay();
    const caption = overlay.querySelector(".loading-caption");

    window.clearInterval(loadingMessageTimer);
    let captionIndex = 0;
    caption.textContent = captions[captionIndex];
    document.body.classList.add("has-loading-overlay");
    overlay.classList.add("is-visible");

    loadingMessageTimer = window.setInterval(() => {
        captionIndex = (captionIndex + 1) % captions.length;
        caption.textContent = captions[captionIndex];
    }, 1900);
}

function hideLoadingOverlay() {
    const overlay = document.querySelector(".loading-overlay");
    window.clearInterval(loadingMessageTimer);
    loadingMessageTimer = null;
    document.body.classList.remove("has-loading-overlay");
    if (overlay) {
        overlay.classList.remove("is-visible");
    }
}

function createWarningOverlay() {
    let overlay = document.querySelector(".warning-overlay");
    if (overlay) {
        return overlay;
    }

    overlay = document.createElement("div");
    overlay.className = "warning-overlay";
    overlay.innerHTML = `
        <div class="warning-panel" role="dialog" aria-modal="true" aria-live="polite">
            <span class="warning-kicker">Проверьте форму</span>
            <strong class="warning-title"></strong>
            <p class="warning-description"></p>
            <ul class="warning-list"></ul>
            <div class="warning-actions">
                <button type="button" class="btn btn-secondary" data-warning-cancel></button>
                <button type="button" class="btn btn-primary" data-warning-confirm></button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    return overlay;
}

function hideWarningOverlay() {
    const overlay = document.querySelector(".warning-overlay");
    if (!overlay) {
        warningOverlayState = null;
        return;
    }
    overlay.classList.remove("is-visible");
    document.body.classList.remove("has-loading-overlay");
    warningOverlayState = null;
}

function showWarningOverlay(config) {
    const overlay = createWarningOverlay();
    const title = overlay.querySelector(".warning-title");
    const description = overlay.querySelector(".warning-description");
    const list = overlay.querySelector(".warning-list");
    const cancelButton = overlay.querySelector("[data-warning-cancel]");
    const confirmButton = overlay.querySelector("[data-warning-confirm]");

    title.textContent = config.title || "Проверьте форму";
    description.textContent = config.description || "";
    list.innerHTML = "";

    for (const item of config.items || []) {
        const li = document.createElement("li");
        li.textContent = item;
        list.appendChild(li);
    }

    cancelButton.textContent = config.cancelText || "Вернуться";
    confirmButton.textContent = config.confirmText || "Окей";
    warningOverlayState = config;

    overlay.classList.add("is-visible");
    document.body.classList.add("has-loading-overlay");
}

function isProfileForm(form) {
    if (!form) {
        return false;
    }
    return Boolean(
        form.querySelector("[name='full_name']") &&
        form.querySelector("[name='summary_text']") &&
        form.querySelector("[name='experience_company']")
    );
}

function isVacancyForm(form) {
    if (!form) {
        return false;
    }
    return Boolean(
        form.querySelector("[name='source_url']") &&
        form.querySelector("[name='raw_text']")
    );
}

function prepareSoftProfileFields() {
    document.querySelectorAll("form").forEach((form) => {
        if (!isProfileForm(form)) {
            return;
        }
        ["city", "work_format", "summary_text", "skills_text"].forEach((fieldName) => {
            const field = form.querySelector(`[name='${fieldName}']`);
            if (field) {
                field.removeAttribute("required");
            }
        });
    });
}

function getFirstFilledValue(form, fieldName) {
    const field = form.querySelector(`[name='${fieldName}']`);
    return field ? field.value.trim() : "";
}

function getProfileWarningPayload(form) {
    if (!isProfileForm(form)) {
        return null;
    }

    const warnings = [];
    const focusFieldNames = [];
    const fieldChecks = [
        ["city", "Не указан город, в котором вы готовы работать."],
        ["work_format", "Не указан формат работы: офис, гибрид или удалённо."],
        ["summary_text", "Не заполнен блок «Коротко о себе»."],
        ["skills_text", "Не указаны ключевые навыки и технологии."],
    ];

    for (const [fieldName, message] of fieldChecks) {
        if (!getFirstFilledValue(form, fieldName)) {
            warnings.push(message);
            focusFieldNames.push(fieldName);
        }
    }

    if (!warnings.length) {
        return null;
    }

    return {
        items: warnings,
        firstField: form.querySelector(`[name='${focusFieldNames[0]}']`) || null,
        title: "Профиль можно усилить перед сохранением",
        description: "Сохранить профиль всё равно можно, но без этих полей система будет хуже адаптировать резюме под вакансии.",
        confirmText: "Окей, сохранить",
        cancelText: "Внести информацию",
    };
}

function isHhUrl(url) {
    try {
        const parsed = new URL(url);
        const host = (parsed.hostname || "").toLowerCase();
        return host === "hh.ru" || host.endsWith(".hh.ru");
    } catch (error) {
        return false;
    }
}

function validateVacancyForm(form) {
    if (!isVacancyForm(form)) {
        return true;
    }

    const titleField = form.querySelector("[name='title']");
    const urlField = form.querySelector("[name='source_url']");
    const textField = form.querySelector("[name='raw_text']");
    const title = titleField?.value.trim() || "";
    const url = urlField?.value.trim() || "";
    const rawText = textField?.value.trim() || "";

    [titleField, urlField, textField].forEach((field) => field?.setCustomValidity(""));

    if (!url && !rawText) {
        textField?.setCustomValidity("Укажите ссылку на hh.ru или вставьте текст вакансии.");
        textField?.reportValidity();
        textField?.focus();
        return false;
    }

    if (rawText && !title) {
        titleField?.setCustomValidity("Если вставляете текст вакансии вручную, укажите её название.");
        titleField?.reportValidity();
        titleField?.focus();
        return false;
    }

    if (url && !rawText && !isHhUrl(url)) {
        textField?.setCustomValidity("Для ссылок не с hh.ru вставьте текст вакансии в поле ниже.");
        textField?.reportValidity();
        textField?.focus();
        return false;
    }

    return true;
}

function validateSoftWarnings(form, submitter) {
    if (submitter?.hasAttribute("formnovalidate")) {
        return true;
    }

    if (form.dataset.warningConfirmed === "1") {
        form.dataset.warningConfirmed = "0";
        return true;
    }

    const payload = getProfileWarningPayload(form);
    if (!payload) {
        return true;
    }

    showWarningOverlay({
        ...payload,
        onConfirm: () => {
            hideWarningOverlay();
            form.dataset.warningConfirmed = "1";
            window.setTimeout(() => form.requestSubmit(submitter || form.querySelector("[type='submit']")), 0);
        },
        onCancel: () => {
            hideWarningOverlay();
            payload.firstField?.focus();
        },
    });
    return false;
}

function validateTrimmedInstruction(form) {
    if (!form?.hasAttribute("data-trim-instruction-form")) {
        return true;
    }

    const instructionField = form.querySelector("[name='instruction']");
    if (!instructionField) {
        return true;
    }

    const trimmedValue = instructionField.value.trim();
    if (trimmedValue) {
        instructionField.setCustomValidity("");
        return true;
    }

    instructionField.setCustomValidity("Напишите, что именно нужно поменять.");
    instructionField.reportValidity();
    instructionField.focus();
    return false;
}

document.addEventListener("submit", (event) => {
    const submitForm = event.target.closest("form");
    if (submitForm?.hasAttribute("data-clear-analysis-cache")) {
        clearAnalysisSnapshot();
    }

    if (!validateVacancyForm(submitForm)) {
        event.preventDefault();
        return;
    }

    if (!validateSoftWarnings(submitForm, event.submitter)) {
        event.preventDefault();
        return;
    }

    const form = event.target.closest("[data-loading-form]");
    if (!form) {
        return;
    }

    if (event.submitter?.hasAttribute("formnovalidate")) {
        showLoadingOverlay(form.dataset.loadingMessages);
        return;
    }

    if (!validateTrimmedInstruction(form)) {
        event.preventDefault();
        return;
    }

    if (!form.checkValidity()) {
        return;
    }

    const buttons = form.querySelectorAll("button, input[type='submit']");

    form.classList.add("is-loading");
    form.setAttribute("aria-busy", "true");
    buttons.forEach((button) => {
        button.disabled = true;
    });

    showLoadingOverlay(form.dataset.loadingMessages);
});

document.addEventListener("input", (event) => {
    const field = event.target;
    if (!(field instanceof HTMLTextAreaElement || field instanceof HTMLInputElement)) {
        return;
    }
    if (field.name === "instruction" || field.name === "title" || field.name === "source_url" || field.name === "raw_text") {
        field.setCustomValidity("");
    }
});

document.addEventListener("click", (event) => {
    const cancelButton = event.target.closest("[data-warning-cancel]");
    if (cancelButton) {
        warningOverlayState?.onCancel?.();
        return;
    }

    const confirmButton = event.target.closest("[data-warning-confirm]");
    if (confirmButton) {
        warningOverlayState?.onConfirm?.();
    }
});

document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-loading-link]");
    if (!link) {
        return;
    }
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
    }
    if (link.target && link.target !== "_self") {
        return;
    }
    showLoadingOverlay(link.dataset.loadingMessages);
});

function isResultPage() {
    return window.location.pathname === "/result";
}

function clearAnalysisSnapshot() {
    try {
        window.localStorage.removeItem(analysisCacheKey);
    } catch (error) {
        // Если браузер ограничил localStorage, страница просто работает без кэша.
    }
}

function restoreAnalysisSnapshotIfNeeded() {
    if (!isResultPage() || window.location.search) {
        return;
    }

    let snapshot = "";
    try {
        snapshot = window.localStorage.getItem(analysisCacheKey) || "";
    } catch (error) {
        return;
    }
    const main = document.querySelector("main.page-content");
    if (!snapshot || !main) {
        return;
    }

    main.innerHTML = snapshot;
}

function saveAnalysisSnapshotIfNeeded() {
    if (!isResultPage()) {
        return;
    }

    const params = new URLSearchParams(window.location.search);
    if (!params.has("vacancy_id")) {
        return;
    }

    const main = document.querySelector("main.page-content");
    if (!main) {
        return;
    }

    try {
        window.localStorage.setItem(analysisCacheKey, main.innerHTML);
    } catch (error) {
        return;
    }
    window.history.replaceState({}, document.title, "/result");
}

window.addEventListener("pageshow", hideLoadingOverlay);
window.addEventListener("pagehide", hideLoadingOverlay);

prepareSoftProfileFields();
restoreAnalysisSnapshotIfNeeded();
saveAnalysisSnapshotIfNeeded();
