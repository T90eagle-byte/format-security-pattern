const form = document.querySelector("#ingest-form");
const fileInput = document.querySelector("#file-input");
const fileLabel = document.querySelector("#file-label");
const submitButton = document.querySelector("#submit-button");
const formMessage = document.querySelector("#form-message");
const resultPanel = document.querySelector("#result-panel");
const decisionBadge = document.querySelector("#decision-badge");
const auditWarning = document.querySelector("#audit-warning");
const presentationLabelData = document.querySelector("#presentation-labels");

let presentationLabels = {};
if (presentationLabelData) {
    try {
        presentationLabels = JSON.parse(presentationLabelData.textContent || "{}");
    } catch (_error) {
        presentationLabels = {};
    }
}

const presentationFields = {
    decision: "decision",
    reason: "reason",
    validation_status: "validation_status",
};

const displayValue = (value, category = null) => {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "boolean") return value ? "Да" : "Нет";
    if (category) {
        return presentationLabels[category]?.[String(value)] || String(value);
    }
    return String(value);
};

if (fileInput && fileLabel) {
    fileInput.addEventListener("change", () => {
        fileLabel.textContent = fileInput.files?.[0]?.name || "Выберите файл";
    });
}

if (form) {
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!fileInput.files?.length) return;

        submitButton.disabled = true;
        formMessage.className = "form-message is-loading";
        formMessage.textContent = "Файл проходит проверку…";

        try {
            const response = await fetch("/ingest", {
                method: "POST",
                body: new FormData(form),
            });
            const payload = await response.json();
            if (!response.ok) {
                const detail = typeof payload.detail === "string"
                    ? payload.detail
                    : "Сервис временно недоступен";
                throw new Error(detail);
            }

            document.querySelectorAll("[data-field]").forEach((element) => {
                const field = element.dataset.field;
                element.textContent = displayValue(payload[field], presentationFields[field]);
            });

            const decision = String(payload.decision || "").toLowerCase();
            decisionBadge.textContent = displayValue(payload.decision, "decision");
            decisionBadge.className = `decision-badge decision--${decision}`;
            resultPanel.className = `card result-panel result-panel--${decision}`;
            resultPanel.hidden = false;
            auditWarning.hidden = payload.audit_stored !== false;

            formMessage.className = "form-message is-success";
            formMessage.textContent = "Обработка завершена";
            resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
            formMessage.className = "form-message is-error";
            formMessage.textContent = error.message || "Сервис временно недоступен";
        } finally {
            submitButton.disabled = false;
        }
    });
}

const testScenarioData = document.querySelector("#test-object-scenarios");
const testResultPanel = document.querySelector("#test-result-panel");
const testDecisionBadge = document.querySelector("#test-decision-badge");
const expectedResult = document.querySelector("#expected-result");
const actualResult = document.querySelector("#actual-result");
const scenarioOutcome = document.querySelector("#scenario-outcome");

let testScenarios = [];
if (testScenarioData) {
    try {
        testScenarios = JSON.parse(testScenarioData.textContent || "[]");
    } catch (_error) {
        testScenarios = [];
    }
}

const findScenario = (scenarioId) => (
    testScenarios.find((scenario) => scenario.id === scenarioId)
);

const scenarioResultText = (decision, reason) => (
    `${displayValue(decision, "decision")} / ${displayValue(reason, "reason")}`
);

const createScenarioFile = (scenario) => new File(
    [scenario.content],
    scenario.filename,
    { type: scenario.mime_type }
);

document.querySelectorAll(".js-send-scenario").forEach((button) => {
    button.addEventListener("click", async () => {
        const scenario = findScenario(button.dataset.scenarioId);
        if (!scenario) return;

        const card = button.closest("[data-scenario-card]");
        const message = card?.querySelector(".js-scenario-message");
        button.disabled = true;
        if (message) {
            message.className = "form-message is-loading js-scenario-message";
            message.textContent = "Объект проходит проверку…";
        }

        const formData = new FormData();
        formData.append("file", createScenarioFile(scenario));

        try {
            const response = await fetch("/ingest", {
                method: "POST",
                body: formData,
            });
            const payload = await response.json();
            if (!response.ok) {
                const detail = typeof payload.detail === "string"
                    ? payload.detail
                    : "Сервис временно недоступен";
                throw new Error(detail);
            }

            testResultPanel.querySelectorAll("[data-test-field]").forEach((element) => {
                const field = element.dataset.testField;
                element.textContent = displayValue(payload[field], presentationFields[field]);
            });

            const decision = String(payload.decision || "").toLowerCase();
            testDecisionBadge.textContent = displayValue(payload.decision, "decision");
            testDecisionBadge.className = `decision-badge decision--${decision}`;
            testResultPanel.className = `card result-panel result-panel--${decision}`;
            expectedResult.textContent = scenarioResultText(
                scenario.expected.decision,
                scenario.expected.reason
            );
            actualResult.textContent = scenarioResultText(
                payload.decision,
                payload.reason
            );

            const reasonMatches = (payload.reason || null) === scenario.expected.reason;
            const decisionMatches = payload.decision === scenario.expected.decision;
            const scenarioMatches = decisionMatches && reasonMatches;
            scenarioOutcome.className = scenarioMatches
                ? "form-message is-success"
                : "form-message is-error";
            scenarioOutcome.textContent = scenarioMatches
                ? "Сценарий выполнен ожидаемо"
                : "Фактический результат отличается от ожидаемого";
            testResultPanel.hidden = false;

            if (message) {
                message.className = "form-message is-success js-scenario-message";
                message.textContent = "Обработка завершена";
            }
            testResultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
            if (message) {
                message.className = "form-message is-error js-scenario-message";
                message.textContent = error.message || "Сервис временно недоступен";
            }
        } finally {
            button.disabled = false;
        }
    });
});

document.querySelectorAll(".js-download-scenario").forEach((button) => {
    button.addEventListener("click", () => {
        const scenario = findScenario(button.dataset.scenarioId);
        if (!scenario) return;

        const blob = new Blob([scenario.content], { type: scenario.mime_type });
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = scenario.filename;
        link.click();
        URL.revokeObjectURL(objectUrl);
    });
});

document.querySelectorAll(".js-date").forEach((element) => {
    const rawValue = element.getAttribute("datetime");
    if (!rawValue) return;
    const date = new Date(rawValue);
    if (!Number.isNaN(date.valueOf())) {
        element.textContent = new Intl.DateTimeFormat("ru-RU", {
            dateStyle: "short",
            timeStyle: "medium",
        }).format(date);
    }
});

document.querySelectorAll(".js-size").forEach((element) => {
    const bytes = Number(element.dataset.bytes || 0);
    if (!Number.isFinite(bytes)) return;
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
    }
    element.textContent = `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
});
