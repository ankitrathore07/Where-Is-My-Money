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
  let nextId = 1;

  const suffixOf = (name) => {
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot).toLowerCase();
  };
  const signatureOf = (file) =>
    [file.name, file.size, file.lastModified, file.type].join("|");
  const plural = (count) => `${count} ${count === 1 ? "file" : "files"}`;

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
    const readyCount = [...queue.values()].filter(
      (item) => item.state === "ready",
    ).length;
    processButton.textContent = `Process ${plural(readyCount)}`;
    processButton.disabled = readyCount === 0;
    queueWrap.hidden = queue.size === 0;
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
  const preventSubmit = (event) => event.preventDefault();
  form.addEventListener("submit", preventSubmit);
})();
