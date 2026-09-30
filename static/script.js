let currentThreadId = localStorage.getItem("travel_thread_id") || null;
let latestAnswerMarkdown = "";

function setPrompt(text) {
    document.getElementById("userInput").value = text;
}

function setLoading(isLoading) {
    const sendBtn = document.getElementById("sendBtn");
    const btnText = document.getElementById("btnText");
    const btnLoader = document.getElementById("btnLoader");

    if (!sendBtn) return;

    sendBtn.disabled = isLoading;

    if (isLoading) {
        if (btnText) btnText.classList.add("hidden");
        if (btnLoader) btnLoader.classList.remove("hidden");
    } else {
        if (btnText) btnText.classList.remove("hidden");
        if (btnLoader) btnLoader.classList.add("hidden");
    }
}

function showError(message) {
    const errorBox = document.getElementById("errorBox");

    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
}

function hideError() {
    const errorBox = document.getElementById("errorBox");

    errorBox.classList.add("hidden");
    errorBox.textContent = "";
}

function renderMarkdown(element, content) {
    if (!element) return;

    // Always coerce to a string so marked() never receives an array/object.
    let text = content;
    if (Array.isArray(text)) {
        text = text
            .map(block => (typeof block === "string" ? block : (block && block.text) || ""))
            .join("");
    } else if (text && typeof text === "object") {
        text = text.text || JSON.stringify(text, null, 2);
    }
    text = typeof text === "string" ? text : String(text ?? "");

    if (typeof marked !== "undefined") {
        element.innerHTML = marked.parse(text);
    } else {
        element.innerText = text;
    }
}

/* ---------- Pre-approval preview builder ---------- */

function isEmptyValue(value) {
    if (value === null || value === undefined) return true;
    if (typeof value === "string") return !value.trim();
    if (Array.isArray(value)) return value.length === 0;
    if (typeof value === "object") return Object.keys(value).length === 0;
    return false;
}

function escapeCell(value) {
    const text = String(value ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ").trim();
    return text || "-";
}

function formatTrains(trainResults) {
    if (isEmptyValue(trainResults)) return "";

    // Error / status message from the backend
    if (typeof trainResults === "string") {
        return `## Trains\n\n${trainResults}\n`;
    }

    const trains = Array.isArray(trainResults.trains) ? trainResults.trains : [];
    let md = "## Trains\n\n";

    const origin = [trainResults.origin_station, trainResults.origin_code ? `(${trainResults.origin_code})` : ""]
        .filter(Boolean).join(" ");
    const destination = [trainResults.destination_station, trainResults.destination_code ? `(${trainResults.destination_code})` : ""]
        .filter(Boolean).join(" ");

    if (origin || destination) {
        md += `**Verified route:** ${origin || "-"} → ${destination || "-"}`;
        if (trainResults.data_source) md += ` · Source: ${trainResults.data_source}`;
        md += "\n\n";
    }

    if (trains.length === 0) {
        return md + "No trains were returned for this route.\n";
    }

    md += "| Train | Name | Type | Departure | Arrival | Duration | Distance (km) | Running days |\n";
    md += "|---|---|---|---|---|---|---|---|\n";

    trains.forEach(train => {
        md += `| ${escapeCell(train.number)} | ${escapeCell(train.name)} | ${escapeCell(train.type)} | ` +
              `${escapeCell(train.departure)} | ${escapeCell(train.arrival)} | ${escapeCell(train.duration)} | ` +
              `${escapeCell(train.distance_km)} | ${escapeCell(train.running_days)} |\n`;
    });

    return md + "\n";
}

function formatHotels(rawHotels) {
    if (isEmptyValue(rawHotels)) return "";

    const raw = typeof rawHotels === "string" ? rawHotels : JSON.stringify(rawHotels, null, 2);

    // Try to render Tavily-style JSON nicely
    try {
        const parsed = JSON.parse(raw);
        const list = Array.isArray(parsed) ? parsed : parsed.results;

        if (Array.isArray(list) && list.length > 0) {
            let md = "## Hotels\n\n";

            list.slice(0, 8).forEach(item => {
                if (!item || typeof item !== "object") return;

                const title = item.title || item.name || "Hotel result";
                const snippet = String(item.content || item.snippet || item.description || "")
                    .replace(/\s+/g, " ")
                    .trim()
                    .slice(0, 300);

                md += item.url ? `- **[${title}](${item.url})**` : `- **${title}**`;
                if (snippet) md += `\n  ${snippet}`;
                md += "\n";
            });

            return md + "\n";
        }
    } catch (e) {
        // not JSON - fall through
    }

    // Plain text / unknown structure: keep everything, collapsed
    return `## Hotels\n\n<details><summary>View hotel search results</summary>\n\n\`\`\`\n${raw}\n\`\`\`\n\n</details>\n\n`;
}

function formatPlainSection(title, content) {
    if (isEmptyValue(content)) return "";
    const text = typeof content === "string" ? content : JSON.stringify(content, null, 2);
    return `## ${title}\n\n${text.trim()}\n\n`;
}

function buildDisplayMarkdown(data) {
    return typeof data.answer === "string"
        ? data.answer
        : String(data.answer ?? "");
}

/* ---------- Status widgets ---------- */

function showAgentStatus(selectedAgents = []) {
    const agentStatus = document.getElementById("agentStatus");

    if (!agentStatus) return;

    const agentLabels = {
        flight_agent: "✈ Flights",
        train_agent: "🚆 Trains",
        hotel_agent: "⌂ Hotels",
        weather_agent: "☁ Weather",
        budget_agent: "₹ Budget",
        itinerary_agent: "🗺 Itinerary"
    };

    agentStatus.innerHTML = "";

    selectedAgents.forEach(agent => {
        const badge = document.createElement("span");
        badge.className = "agent-badge";
        badge.textContent = agentLabels[agent] || agent;
        agentStatus.appendChild(badge);
    });

    if (selectedAgents.length > 0) {
        agentStatus.classList.remove("hidden");
    } else {
        agentStatus.classList.add("hidden");
    }
}

function showTripConstraints(constraints = {}) {
    const constraintsBox = document.getElementById("tripConstraints");

    if (!constraintsBox) return;

    constraintsBox.innerHTML = "";

    const fields = [
        ["destination", "Destination"],
        ["origin", "Origin"],
        ["duration", "Duration"],
        ["travel_date", "Travel Date"],
        ["budget", "Budget"],
        ["num_travelers", "Travelers"],
        ["transportation_preference", "Transport"],
        ["travel_style", "Travel Style"]
    ];

    fields.forEach(([key, label]) => {
        const value = constraints[key];

        if (!value) return;

        const item = document.createElement("div");
        item.className = "constraint-item";

        item.innerHTML = `
            <span class="constraint-label">${label}</span>
            <span class="constraint-value">${value}</span>
        `;

        constraintsBox.appendChild(item);
    });

    if (Array.isArray(constraints.special_preferences)) {
        constraints.special_preferences.forEach(preference => {
            if (!preference) return;

            const item = document.createElement("div");
            item.className = "constraint-item";

            item.innerHTML = `
                <span class="constraint-label">Preference</span>
                <span class="constraint-value">${preference}</span>
            `;

            constraintsBox.appendChild(item);
        });
    }

    if (constraintsBox.children.length > 0) {
        constraintsBox.classList.remove("hidden");
    } else {
        constraintsBox.classList.add("hidden");
    }
}

function showApprovalPanel(data) {
    const approvalPanel = document.getElementById("approvalPanel");
    const approvalRequest = document.getElementById("approvalRequest");
    const feedbackInput = document.getElementById("feedbackInput");

    if (!approvalPanel) return;

    if (approvalRequest) {
        approvalRequest.textContent =
            data.approval_request ||
            "Please review the generated itinerary before finalizing.";
    }

    if (feedbackInput) {
        feedbackInput.value = "";
    }

    approvalPanel.classList.remove("hidden");

    approvalPanel.scrollIntoView({
        behavior: "smooth",
        block: "center"
    });
}

function hideApprovalPanel() {
    const approvalPanel = document.getElementById("approvalPanel");

    if (approvalPanel) {
        approvalPanel.classList.add("hidden");
    }
}

function updateApprovalStatus(approved) {
    const approvalStatus = document.getElementById("approvalStatus");

    if (!approvalStatus) return;

    if (approved) {
        approvalStatus.textContent = "✓ Plan approved";
        approvalStatus.classList.remove("hidden");
    } else {
        approvalStatus.textContent = "↻ Revision requested";
        approvalStatus.classList.remove("hidden");
    }
}

function showResult(data) {
    const displayMarkdown = buildDisplayMarkdown(data);
    latestAnswerMarkdown = displayMarkdown;

    const resultSection = document.getElementById("resultSection");
    const resultBox = document.getElementById("resultBox");
    const threadInfo = document.getElementById("threadInfo");

    renderMarkdown(resultBox, displayMarkdown);

    if (threadInfo) {
        threadInfo.textContent = `Thread ID: ${data.thread_id}`;
    }

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

    resultSection.classList.remove("hidden");

    resultSection.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}

async function sendMessage() {
    hideError();

    const input = document.getElementById("userInput");
    const message = input.value.trim();

    if (!message) {
        showError("Please enter your travel request first.");
        return;
    }

    setLoading(true);
    hideApprovalPanel();

    try {
        const response = await fetch("/api/travel", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                message: message,
                thread_id: currentThreadId
            })
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(
                data.error || "Something went wrong."
            );
        }

        currentThreadId = data.thread_id;

        localStorage.setItem(
            "travel_thread_id",
            currentThreadId
        );

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

    const feedbackInput =
        document.getElementById("feedbackInput");

    const feedback =
        feedbackInput?.value.trim() || "";

    const approveBtn =
        document.getElementById("approveBtn");

    const reviseBtn =
        document.getElementById("reviseBtn");

    if (approveBtn) approveBtn.disabled = true;
    if (reviseBtn) reviseBtn.disabled = true;

    try {
        const response = await fetch(
            "/api/travel/resume",
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    thread_id: currentThreadId,
                    approved: approved,
                    feedback: feedback
                })
            }
        );

        const data = await response.json();

        if (!response.ok || !data.success) {
            throw new Error(
                data.error ||
                "Could not resume the travel planning workflow."
            );
        }

        currentThreadId = data.thread_id;

        localStorage.setItem(
            "travel_thread_id",
            currentThreadId
        );

        showResult(data);

        if (!data.requires_approval) {
            updateApprovalStatus(approved);
        }

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
    const feedbackInput =
        document.getElementById("feedbackInput");

    if (!feedbackInput?.value.trim()) {
        showError(
            "Please provide feedback before requesting a revision."
        );
        return;
    }

    submitApproval(false);
}

function copyResult() {
    const resultBox = document.getElementById("resultBox");
    const text = resultBox.innerText;

    if (!text) {
        return;
    }

    navigator.clipboard.writeText(text)
        .then(() => {
            const copyBtn =
                document.querySelector(".copy-btn");

            if (!copyBtn) return;

            const oldText = copyBtn.textContent;

            copyBtn.textContent = "Copied!";

            setTimeout(() => {
                copyBtn.textContent = oldText;
            }, 1400);
        })
        .catch(() => {
            showError("Could not copy result.");
        });
}

function downloadPDF() {
    const pdfContent =
        document.getElementById("pdfContent");

    if (!latestAnswerMarkdown || !pdfContent) {
        showError(
            "No travel plan available to download."
        );
        return;
    }

    const downloadBtn =
        document.querySelector(".download-btn");

    if (!downloadBtn) return;

    const oldText = downloadBtn.textContent;

    downloadBtn.textContent =
        "Preparing PDF...";

    downloadBtn.disabled = true;

    const options = {
        margin: 0.5,
        filename: "voyanta-travel-plan.pdf",
        image: {
            type: "jpeg",
            quality: 0.98
        },
        html2canvas: {
            scale: 2,
            useCORS: true,
            backgroundColor: "#ffffff"
        },
        jsPDF: {
            unit: "in",
            format: "a4",
            orientation: "portrait"
        },
        pagebreak: {
            mode: [
                "avoid-all",
                "css",
                "legacy"
            ]
        }
    };

    html2pdf()
        .set(options)
        .from(pdfContent)
        .save()
        .then(() => {
            downloadBtn.textContent = oldText;
            downloadBtn.disabled = false;
        })
        .catch(() => {
            downloadBtn.textContent = oldText;
            downloadBtn.disabled = false;
            showError("Could not download PDF.");
        });
}

document.addEventListener(
    "keydown",
    function(event) {
        if (
            event.ctrlKey &&
            event.key === "Enter"
        ) {
            sendMessage();
        }
    }
);