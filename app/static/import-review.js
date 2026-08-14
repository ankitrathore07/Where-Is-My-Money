(() => {
  "use strict";

  const root = document.querySelector(".import-review");
  if (!root) return;

  const rows = Array.from(root.querySelectorAll("[data-review-row]"));
  const pageSize = Number(root.dataset.pageSize) || 50;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const message = root.querySelector("[data-review-message]");
  let page = 0;

  const selectedRows = () => rows.filter((row) => row.querySelector("[data-row-select]").checked);
  const updateSelectionCount = () => {
    root.querySelector("[data-selected-count]").textContent = String(selectedRows().length);
  };
  const showMessage = (text) => { message.textContent = text; };

  function renderPage() {
    rows.forEach((row, index) => { row.hidden = Math.floor(index / pageSize) !== page; });
    root.querySelector("[data-page-label]").textContent = `Page ${page + 1} of ${pageCount}`;
    root.querySelector("[data-page-previous]").disabled = page === 0;
    root.querySelector("[data-page-next]").disabled = page >= pageCount - 1;
  }

  root.querySelector("[data-page-previous]").addEventListener("click", () => {
    page = Math.max(0, page - 1);
    renderPage();
  });
  root.querySelector("[data-page-next]").addEventListener("click", () => {
    page = Math.min(pageCount - 1, page + 1);
    renderPage();
  });
  root.querySelector("[data-select-page]").addEventListener("click", () => {
    rows.filter((row) => !row.hidden).forEach((row) => { row.querySelector("[data-row-select]").checked = true; });
    updateSelectionCount();
  });
  root.querySelector("[data-clear-selection]").addEventListener("click", () => {
    rows.forEach((row) => { row.querySelector("[data-row-select]").checked = false; });
    updateSelectionCount();
  });
  root.querySelector("[data-select-matching]").addEventListener("click", () => {
    const selected = selectedRows();
    if (!selected.length) {
      showMessage("Select one row first, then select its matching counterparty and direction.");
      return;
    }
    const group = selected[0].dataset.reviewGroup;
    rows.filter((row) => row.dataset.reviewGroup === group).forEach((row) => {
      row.querySelector("[data-row-select]").checked = true;
    });
    updateSelectionCount();
    showMessage("Selected matching rows with the same counterparty and money direction.");
  });
  rows.forEach((row) => row.querySelector("[data-row-select]").addEventListener("change", updateSelectionCount));

  const optionByName = (select, name) => Array.from(select.options).find(
    (option) => option.textContent.trim().toLocaleLowerCase() === name.trim().toLocaleLowerCase()
  );

  function renderChips(editor) {
    const select = editor.querySelector("[data-row-tags]");
    const chips = editor.querySelector("[data-tag-chips]");
    chips.replaceChildren();
    Array.from(select.selectedOptions).forEach((option) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "tag-chip";
      chip.textContent = `${option.textContent} ×`;
      chip.setAttribute("aria-label", `Remove ${option.textContent} tag`);
      chip.addEventListener("click", () => {
        option.selected = false;
        renderChips(editor);
      });
      chips.append(chip);
    });
  }

  function selectTypedTag(editor) {
    const input = editor.querySelector("[data-tag-input]");
    const select = editor.querySelector("[data-row-tags]");
    const option = optionByName(select, input.value);
    if (!option) {
      editor.querySelector("[data-tag-error]").textContent = "Choose an existing tag or create a new one.";
      return false;
    }
    option.selected = true;
    input.value = "";
    editor.querySelector("[data-tag-error]").textContent = "";
    renderChips(editor);
    return true;
  }

  async function createTypedTag(editor) {
    const input = editor.querySelector("[data-tag-input]");
    const name = input.value.trim();
    const error = editor.querySelector("[data-tag-error]");
    if (!name) {
      error.textContent = "Enter a tag name first.";
      return;
    }
    const form = new FormData();
    form.set("name", name);
    const response = await fetch(root.dataset.inlineTagUrl, {
      method: "POST",
      body: form,
      headers: { "X-CSRF-Token": root.dataset.csrfToken },
    });
    const payload = await response.json();
    if (!response.ok) {
      error.textContent = payload.detail || "The tag could not be created.";
      return;
    }
    root.querySelectorAll("[data-row-tags]").forEach((select) => {
      if (!Array.from(select.options).some((option) => option.value === String(payload.id))) {
        select.add(new Option(payload.name, payload.id));
      }
    });
    root.querySelectorAll("[data-tag-input]").forEach((tagInput) => {
      const list = document.getElementById(tagInput.getAttribute("list"));
      if (list) list.append(new Option(payload.name));
    });
    const created = optionByName(editor.querySelector("[data-row-tags]"), payload.name);
    created.selected = true;
    input.value = "";
    error.textContent = "";
    renderChips(editor);
  }

  root.querySelectorAll("[data-tag-editor]").forEach((editor) => {
    editor.classList.add("tag-editor-enhanced");
    renderChips(editor);
    editor.querySelector("[data-add-tag]").addEventListener("click", () => selectTypedTag(editor));
    editor.querySelector("[data-create-tag]").addEventListener("click", () => createTypedTag(editor));
    editor.querySelector("[data-tag-input]").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        selectTypedTag(editor);
      }
    });
  });

  root.querySelector("[data-apply-bulk]").addEventListener("click", () => {
    const selected = selectedRows();
    if (!selected.length) {
      showMessage("Select at least one row before applying a category or tags.");
      return;
    }
    const category = root.querySelector("[data-bulk-category]").value;
    const tags = Array.from(root.querySelector("[data-bulk-tags]").selectedOptions);
    selected.forEach((row) => {
      if (category) row.querySelector("[data-row-category]").value = category;
      const rowTags = row.querySelector("[data-row-tags]");
      tags.forEach((tag) => {
        const option = Array.from(rowTags.options).find((candidate) => candidate.value === tag.value);
        if (option) option.selected = true;
      });
      renderChips(row.querySelector("[data-tag-editor]"));
    });
    showMessage(`Applied the selected values to ${selected.length} row${selected.length === 1 ? "" : "s"}.`);
  });

  renderPage();
  updateSelectionCount();
})();
