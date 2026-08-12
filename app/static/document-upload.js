(() => {
  "use strict";

  const configNode = document.getElementById("document-category-config");
  const form = document.getElementById("document-upload-form");
  if (!configNode || !form) return;

  const config = JSON.parse(configNode.textContent);
  const categoryByKey = new Map(
    config.categories.map((category) => [category.key, category]),
  );
  /**
   * @typedef {object} QueueItem
   * @property {string} id
   * @property {string} signature
   * @property {File | null} file
   * @property {string} categoryKey
   * @property {string} state
   * @property {string} message
   * @property {HTMLTableRowElement | null} row
   * @property {HTMLSelectElement | null} select
   * @property {HTMLSpanElement | null} status
   * @property {HTMLTableCellElement | null} actionCell
   * @property {HTMLButtonElement | null} remove
   */
  /** @type {Map<string, QueueItem>} */
  const queue = new Map();
  const fileInput = document.getElementById("document-files");
  const dropZone = document.getElementById("document-drop-zone");
  const queueBody = document.getElementById("document-queue-body");
  const queueWrap = document.querySelector(".document-queue-wrap");
  const processButton = document.getElementById("process-documents");
  const liveStatus = document.getElementById("document-live-status");
  const csrfToken = document.getElementById("document-csrf-token");
  const pageAlert = document.getElementById("document-page-alert");
  let processingQueue = false;
  let nextId = 1;

  const suffixOf = (name) => {
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot).toLowerCase();
  };
  const signatureOf = (file) =>
    [file.name, file.size, file.lastModified, file.type].join("|");
  const plural = (count) => `${count} ${count === 1 ? "file" : "files"}`;
  const readyCount = () =>
    [...queue.values()].filter((item) => item.state === "ready").length;

  function compatibility(item) {
    const suffix = suffixOf(item.file.name);
    if (![".csv", ".pdf", ".png", ".jpg", ".jpeg"].includes(suffix)) {
      return {
        state: "invalid",
        message: "Choose a CSV, PDF, PNG, or JPEG file.",
      };
    }
    if (!item.categoryKey) {
      return { state: "needs-category", message: "Choose a category." };
    }
    const category = categoryByKey.get(item.categoryKey);
    if (!category || category.key === "unlisted") {
      return {
        state: "unsupported",
        message: "Remove this file or choose a supported category.",
      };
    }
    if (!category.supported) {
      return {
        state: "unsupported",
        message: "Recognized, but processing is not available yet.",
      };
    }
    if (!category.accepted_suffixes.includes(suffix)) {
      return {
        state: "invalid",
        message: `${category.label} does not support ${suffix || "this format"}.`,
      };
    }
    if (item.file.size > category.max_bytes) {
      return {
        state: "invalid",
        message: `This file exceeds the ${Math.floor(category.max_bytes / 1048576)} MiB limit.`,
      };
    }
    return { state: "ready", message: "Ready to process." };
  }

  const announce = (message) => {
    liveStatus.textContent = message;
  };
  const humanSize = (bytes) =>
    bytes < 1048576
      ? `${Math.max(1, Math.round(bytes / 1024))} KB`
      : `${(bytes / 1048576).toFixed(1)} MB`;

  function refreshBatch() {
    const count = readyCount();
    processButton.textContent = `Process ${plural(count)}`;
    processButton.disabled = processingQueue || count === 0;
    for (const retry of queueBody.querySelectorAll(".document-retry")) {
      retry.disabled = processingQueue;
    }
    queueWrap.hidden = queue.size === 0;
  }

  function setRetentionControls(disabled) {
    for (const control of form.querySelectorAll(
      'input[name="retention_choice"]',
    )) {
      control.disabled = disabled;
    }
  }

  async function runSerialized(operation) {
    if (processingQueue) return;
    processingQueue = true;
    pageAlert.hidden = true;
    const retentionChoice = form.elements.retention_choice.value;
    setRetentionControls(true);
    refreshBatch();
    let outcome = "continue";
    try {
      outcome = await operation(retentionChoice);
    } finally {
      processingQueue = false;
      setRetentionControls(false);
      refreshBatch();
      const summary = outcome === "stop" ? "Processing stopped" : "Processing complete";
      announce(`${summary}. ${plural(readyCount())} ready to process.`);
    }
  }

  function setItemState(item, state, message) {
    item.state = state;
    item.message = message;
    item.status.textContent = message;
    item.status.dataset.state = state;
    refreshBatch();
  }

  function setPendingControls(item, disabled) {
    item.select.disabled = disabled;
    item.remove.disabled = disabled;
    item.row.setAttribute("aria-busy", disabled ? "true" : "false");
  }

  function restoreRemoveAction(item) {
    item.actionCell.replaceChildren(item.remove);
    item.remove.disabled = false;
  }

  function markRetryable(item, message) {
    setPendingControls(item, false);
    setItemState(item, "retryable", message);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "document-retry";
    retry.textContent = "Retry";
    retry.setAttribute("aria-label", `Retry ${item.file.name}`);
    retry.disabled = processingQueue;
    retry.addEventListener("click", () =>
      runSerialized((retentionChoice) => processItem(item, retentionChoice)),
    );
    item.actionCell.replaceChildren(retry, item.remove);
    announce(`${item.file.name}: ${message}`);
  }

  function completeItem(item, href, label, message) {
    const filename = item.file.name;
    setPendingControls(item, false);
    setItemState(item, "complete", message || "Ready for review.");
    item.select.disabled = true;
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    item.actionCell.replaceChildren(link);
    item.file = null;
    announce(`${filename}: ${item.message}`);
  }

  function showSessionAlert() {
    pageAlert.textContent =
      "Your session could not be verified. Reload or sign in, then process the remaining files.";
    pageAlert.hidden = false;
  }

  async function processItem(item, retentionChoice) {
    setPendingControls(item, true);
    setItemState(item, "processing", "Processing\u2026");
    announce(`Processing ${item.file.name}`);

    const body = new FormData();
    body.append("document", item.file, item.file.name);
    body.append("category_key", item.categoryKey);
    body.append("retention_choice", retentionChoice);
    body.append("csrf_token", csrfToken.value);

    let response;
    try {
      response = await fetch(config.endpoint, {
        method: "POST",
        body,
        credentials: "same-origin",
        headers: { "X-CSRF-Token": csrfToken.value },
      });
    } catch (_error) {
      markRetryable(item, "The upload was interrupted. Retry this file.");
      return "continue";
    }

    const contentType = response.headers.get("content-type") || "";
    if (response.redirected || response.status === 401 || response.status === 403) {
      setPendingControls(item, false);
      restoreRemoveAction(item);
      setItemState(item, "ready", "Waiting for a verified session.");
      showSessionAlert();
      return "stop";
    }

    if (!contentType.includes("application/json")) {
      markRetryable(item, "The server could not process this file. Retry it.");
      return "continue";
    }

    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      markRetryable(item, "The server returned an invalid result. Retry this file.");
      return "continue";
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      markRetryable(item, "The server returned an invalid result. Retry this file.");
      return "continue";
    }
    if (!response.ok) {
      if (response.status >= 500) {
        markRetryable(item, "The server could not process this file. Retry it.");
      } else {
        const message =
          typeof payload.message === "string" && payload.message
            ? payload.message
            : "This file could not be processed.";
        setPendingControls(item, false);
        restoreRemoveAction(item);
        setItemState(item, "failed", message);
        announce(`${item.file.name}: ${message}`);
      }
      return "continue";
    }

    if (
      payload.ok !== true ||
      typeof payload.message !== "string" ||
      !payload.message ||
      typeof payload.next_url !== "string" ||
      !payload.next_url ||
      typeof payload.next_label !== "string" ||
      !payload.next_label
    ) {
      markRetryable(item, "The server returned an invalid result. Retry this file.");
      return "continue";
    }

    let nextUrl;
    try {
      nextUrl = new URL(payload.next_url, window.location.origin);
    } catch (_error) {
      markRetryable(item, "The review link was invalid. Retry this file.");
      return "continue";
    }
    if (nextUrl.origin !== window.location.origin) {
      markRetryable(item, "The review link was invalid. Retry this file.");
      return "continue";
    }
    completeItem(
      item,
      `${nextUrl.pathname}${nextUrl.search}`,
      payload.next_label,
      payload.message,
    );
    return "continue";
  }

  async function processQueue(event) {
    event.preventDefault();
    await runSerialized(async (retentionChoice) => {
      const batchItemIds = [...queue.values()]
        .filter((item) => item.state === "ready")
        .map((item) => item.id);
      for (const itemId of batchItemIds) {
        const item = queue.get(itemId);
        if (!item || item.state !== "ready") continue;
        const action = await processItem(item, retentionChoice);
        if (action === "stop") return "stop";
      }
      return "continue";
    });
  }

  function refreshItem(item) {
    const result = compatibility(item);
    item.state = result.state;
    item.message = result.message;
    item.status.textContent = result.message;
    item.status.dataset.state = result.state;
    refreshBatch();
  }

  function removeItem(id) {
    const item = queue.get(id);
    if (!item) return;
    const nextRow = item.row.nextElementSibling;
    const previousRow = item.row.previousElementSibling;
    const filename = item.file.name;
    queue.delete(id);
    item.row.remove();
    item.file = null;
    refreshBatch();
    const focusTarget =
      nextRow?.querySelector("select") ||
      previousRow?.querySelector("select") ||
      dropZone;
    focusTarget.focus();
    announce(`${filename} removed. ${plural(queue.size)} remain in the queue.`);
  }

  function createRow(item) {
    const row = document.createElement("tr");
    row.dataset.itemId = item.id;

    const fileCell = document.createElement("td");
    const filename = document.createElement("strong");
    const metadata = document.createElement("span");
    filename.textContent = item.file.name;
    metadata.className = "muted visually-explained";
    metadata.textContent = humanSize(item.file.size);
    fileCell.append(filename, metadata);

    const categoryCell = document.createElement("td");
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Document category for ${item.file.name}`);
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = "Choose a category";
    select.append(prompt);
    for (const category of config.categories) {
      const option = document.createElement("option");
      option.value = category.key;
      option.textContent = category.label;
      select.append(option);
    }
    select.addEventListener("change", () => {
      item.categoryKey = select.value;
      restoreRemoveAction(item);
      refreshItem(item);
    });
    categoryCell.append(select);

    const statusCell = document.createElement("td");
    const statusText = document.createElement("span");
    statusText.className = "document-status";
    statusCell.append(statusText);

    const actionCell = document.createElement("td");
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "document-remove";
    remove.setAttribute("aria-label", `Remove ${item.file.name}`);
    remove.textContent = "\u00d7";
    remove.addEventListener("click", () => removeItem(item.id));
    actionCell.append(remove);

    row.append(fileCell, categoryCell, statusCell, actionCell);
    item.row = row;
    item.select = select;
    item.status = statusText;
    item.actionCell = actionCell;
    item.remove = remove;
    queueBody.append(row);
    refreshItem(item);
  }

  function addFiles(files) {
    let added = 0;
    let duplicate = false;
    let limited = false;
    const signatures = new Set(
      [...queue.values()].map((item) => item.signature),
    );
    for (const file of files) {
      const signature = signatureOf(file);
      if (signatures.has(signature)) {
        duplicate = true;
        continue;
      }
      if (queue.size >= config.max_files) {
        limited = true;
        break;
      }
      const item = {
        id: `document-${nextId++}`,
        signature,
        file,
        categoryKey: "",
        state: "needs-category",
        message: "Choose a category.",
        row: null,
      };
      queue.set(item.id, item);
      signatures.add(signature);
      createRow(item);
      added += 1;
    }
    if (limited) {
      announce(
        `Some files were not added because the queue has a ${config.max_files}-file limit.`,
      );
    } else if (duplicate) {
      announce(`A selected file is already in the queue. ${plural(added)} added.`);
    } else {
      announce(`${plural(added)} added. ${plural(queue.size)} in the queue.`);
    }
    fileInput.value = "";
    refreshBatch();
  }

  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => addFiles(fileInput.files));
  for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    });
  }
  dropZone.addEventListener("dragleave", () =>
    dropZone.classList.remove("is-dragging"),
  );
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
    addFiles(event.dataTransfer.files);
  });
  form.addEventListener("submit", processQueue);
})();
