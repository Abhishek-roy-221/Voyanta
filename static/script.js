let currentThreadId = localStorage.getItem("travel_thread_id") || null;
let latestAnswerMarkdown = "";

const $ = (id) => document.getElementById(id);

function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
}

function setPrompt(text) {
    $("userInput").value = text;
    $("userInput").focus();
}

function setLoading(isLoading) {
    const sendBtn = $("sendBtn");
    if (!sendBtn) return;
    sendBtn.disabled = isLoading;
    $("btnText")?.classList.toggle("hidden", isLoading);
    $("btnLoader")?.classList.toggle("hidden", !isLoading);
}

function showError(message) {
    const box = $("errorBox");
    box.textContent = message;
    box.classList.remove("hidden");
}

function hideError() {
    const box = $("errorBox");
    box.classList.add("hidden");
    box.textContent = "";
}

function renderMarkdown(element, content) {
    if (!element) return;

    let text = content;
    if (Array.isArray(text)) {
        text = text.map((b) => (typeof b === "string" ? b : (b && b.text) || "")).join("");
    } else if (text && typeof text === "object") {
        text = text.text || JSON.stringify(text, null, 2);
    }
    text = typeof text === "string" ? text : String(text ?? "");

    if (typeof marked !== "undefined") {
        element.innerHTML = marked.parse(text);
    } else {
        element.innerText = text;
    }

    // Wrap every table so wide ones scroll inside the card, not the page.
    element.querySelectorAll("table").forEach((table) => {
        const wrap = document.createElement("div");
        wrap.className = "table-scroll";
        table.parentNode.insertBefore(wrap, table);
        wrap.appendChild(table);
    });
}

function buildDisplayMarkdown(data) {
    return typeof data.answer === "string" ? data.answer : String(data.answer ?? "");
}

/* ---------- Status widgets ---------- */

function showAgentStatus(selectedAgents = []) {
    const box = $("agentStatus");
    if (!box) return;

    const labels = {
        flight_agent: "Flights",
        train_agent: "Trains",
        hotel_agent: "Hotels",
        weather_agent: "Weather",
        budget_agent: "Budget",
        itinerary_agent: "Itinerary"
    };

    box.innerHTML = selectedAgents
        .map((a) => `<span class="agent-badge">${esc(labels[a] || a)}</span>`)
        .join("");
    box.classList.toggle("hidden", selectedAgents.length === 0);
}

function showTripConstraints(constraints = {}) {
    const box = $("tripConstraints");
    if (!box) return;

    const fields = [
        ["destination", "Destination"], ["origin", "Origin"], ["duration", "Duration"],
        ["travel_date", "Travel Date"], ["budget", "Budget"], ["num_travelers", "Travelers"],
        ["transportation_preference", "Transport"], ["travel_style", "Travel Style"]
    ];

    const items = [];
    fields.forEach(([key, label]) => {
        if (constraints[key]) items.push([label, constraints[key]]);
    });
    if (Array.isArray(constraints.special_preferences)) {
        constraints.special_preferences.forEach((p) => p && items.push(["Preference", p]));
    }

    // Values come from LLM output, so they are escaped before going into innerHTML.
    box.innerHTML = items
        .map(([l, v]) => `<div class="constraint-item"><span class="constraint-label">${esc(l)}</span><span class="constraint-value">${esc(v)}</span></div>`)
        .join("");
    box.classList.toggle("hidden", items.length === 0);
}

function showApprovalPanel(data) {
    const panel = $("approvalPanel");
    if (!panel) return;

    const req = $("approvalRequest");
    if (req) {
        req.textContent = data.approval_request || "Please review the generated itinerary before finalizing.";
    }
    if ($("feedbackInput")) $("feedbackInput").value = "";

    $("approvalStatus")?.classList.add("hidden");
    panel.classList.remove("hidden");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
}

function hideApprovalPanel() {
    $("approvalPanel")?.classList.add("hidden");
}

function updateApprovalStatus(approved) {
    const el = $("approvalStatus");
    if (!el) return;
    el.textContent = approved ? "✓ Plan approved" : "↻ Revision requested";
    el.classList.remove("hidden");
}

function showResult(data) {
    const markdown = buildDisplayMarkdown(data);
    latestAnswerMarkdown = markdown;

    renderMarkdown($("resultBox"), markdown);

    if ($("threadInfo")) $("threadInfo").textContent = `Thread ID: ${data.thread_id}`;

    showAgentStatus(data.selected_agents || []);
    showTripConstraints(data.trip_constraints || {});

    if (data.requires_approval) {
        showApprovalPanel(data);
    } else {
        hideApprovalPanel();
        if (data.approved !== null && data.approved !== undefined) {
            updateApprovalStatus(data.approved);
        }
    }

    const section = $("resultSection");
    section.classList.remove("hidden");
    section.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- API calls (unchanged contracts) ---------- */

async function sendMessage() {
    hideError();

    const message = $("userInput").value.trim();
    if (!message) {
        showError("Please enter your travel request first.");
        return;
    }

    setLoading(true);
    hideApprovalPanel();

    try {
        const response = await fetch("/api/travel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: message, thread_id: currentThreadId })
        });

        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || "Something went wrong.");
        }

        currentThreadId = data.thread_id;
        localStorage.setItem("travel_thread_id", currentThreadId);
        showResult(data);
    } catch (error) {
        showError(error.message);
    } finally {
        setLoading(false);
    }
}

async function submitApproval(approved) {
    hideError();

    if (!currentThreadId) {
        showError("No active travel planning session found.");
        return;
    }

    const feedback = $("feedbackInput")?.value.trim() || "";
    const approveBtn = $("approveBtn");
    const reviseBtn = $("reviseBtn");

    if (approveBtn) approveBtn.disabled = true;
    if (reviseBtn) reviseBtn.disabled = true;

    try {
        const response = await fetch("/api/travel/resume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ thread_id: currentThreadId, approved: approved, feedback: feedback })
        });

        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || "Could not resume the travel planning workflow.");
        }

        currentThreadId = data.thread_id;
        localStorage.setItem("travel_thread_id", currentThreadId);
        showResult(data);

        if (!data.requires_approval) updateApprovalStatus(approved);
    } catch (error) {
        showError(error.message);
    } finally {
        if (approveBtn) approveBtn.disabled = false;
        if (reviseBtn) reviseBtn.disabled = false;
    }
}

function approvePlan() {
    submitApproval(true);
}

function requestRevision() {
    if (!$("feedbackInput")?.value.trim()) {
        showError("Please provide feedback before requesting a revision.");
        return;
    }
    submitApproval(false);
}

/* ---------- Copy / PDF ---------- */

function copyResult() {
    const text = $("resultBox").innerText;
    if (!text) return;

    navigator.clipboard.writeText(text)
        .then(() => {
            const btn = document.querySelector(".copy-btn");
            if (!btn) return;
            const old = btn.textContent;
            btn.textContent = "Copied!";
            setTimeout(() => { btn.textContent = old; }, 1400);
        })
        .catch(() => showError("Could not copy result."));
}

function downloadPDF() {
    const pdfContent = $("pdfContent");
    if (!latestAnswerMarkdown || !pdfContent) {
        showError("No travel plan available to download.");
        return;
    }

    const btn = document.querySelector(".download-btn");
    if (!btn) return;
    const old = btn.textContent;
    btn.textContent = "Preparing PDF...";
    btn.disabled = true;

    const done = () => { btn.textContent = old; btn.disabled = false; };

    html2pdf()
        .set({
            margin: 0.5,
            filename: "voyanta-travel-plan.pdf",
            image: { type: "jpeg", quality: 0.98 },
            html2canvas: { scale: 2, useCORS: true, backgroundColor: "#ffffff" },
            jsPDF: { unit: "in", format: "a4", orientation: "portrait" },
            pagebreak: { mode: ["avoid-all", "css", "legacy"] }
        })
        .from(pdfContent)
        .save()
        .then(done)
        .catch(() => { done(); showError("Could not download PDF."); });
}

document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "Enter") sendMessage();
});