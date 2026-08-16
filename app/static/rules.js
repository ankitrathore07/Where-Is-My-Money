(() => {
  "use strict";

  const builder = document.querySelector("[data-rule-builder]");
  if (!builder) return;

  const rows = builder.querySelector("[data-condition-rows]");
  const template = builder.querySelector("[data-condition-template]");
  const addButton = builder.querySelector("[data-add-condition]");
  if (!rows || !template || !addButton) return;
  let nextIndex = Math.max(
    0,
    ...Array.from(rows.querySelectorAll("[data-condition-field]")).map((field) => {
      const match = field.name.match(/_(\d+)$/);
      return match ? Number(match[1]) + 1 : 0;
    }),
  );

  function kindFor(field) {
    if (field === "description" || field === "merchant_key") return "text";
    if (field === "amount_cents") return "amount";
    if (field === "transaction_date") return "date";
    if (field === "direction") return "direction";
    if (field === "account_id") return "account";
    return "provider";
  }

  function operatorKindFor(field) {
    const kind = kindFor(field);
    if (kind === "text" || kind === "amount" || kind === "date") return kind;
    return "identity";
  }

  function refreshRow(row) {
    const field = row.querySelector("[data-condition-field]").value;
    const valueKind = kindFor(field);
    row.querySelectorAll("[data-value-kind]").forEach((group) => {
      const active = group.dataset.valueKind === valueKind;
      group.hidden = !active;
      group.querySelectorAll("input, select").forEach((control) => {
        control.disabled = !active;
      });
    });
    const operator = row.querySelector("[data-condition-operator]");
    const operatorKind = operatorKindFor(field);
    operator.querySelectorAll("optgroup").forEach((group) => {
      group.hidden = group.dataset.operatorKind !== operatorKind;
    });
    const selected = operator.selectedOptions[0];
    if (!selected || selected.parentElement.hidden) {
      const first = operator.querySelector(`optgroup[data-operator-kind="${operatorKind}"] option`);
      if (first) first.selected = true;
    }
  }

  function renumberRows() {
    rows.querySelectorAll("[data-condition-row]").forEach((row, index) => {
      row.querySelectorAll("[data-condition-number]").forEach((number) => {
        number.textContent = String(index + 1);
      });
    });
  }

  function wireRow(row) {
    row.querySelector("[data-condition-field]").addEventListener("change", () => refreshRow(row));
    row.querySelector("[data-remove-condition]").addEventListener("click", () => {
      if (rows.querySelectorAll("[data-condition-row]").length === 1) return;
      row.remove();
      renumberRows();
      addButton.disabled = false;
      addButton.focus();
    });
    refreshRow(row);
  }

  builder.classList.add("rule-builder-enhanced");
  rows.querySelectorAll("[data-condition-row]").forEach(wireRow);
  addButton.addEventListener("click", (event) => {
    event.preventDefault();
    if (rows.querySelectorAll("[data-condition-row]").length >= 20) return;
    const number = rows.querySelectorAll("[data-condition-row]").length + 1;
    const html = template.innerHTML
      .replaceAll("__INDEX__", String(nextIndex++))
      .replaceAll("__NUMBER__", String(number));
    template.insertAdjacentHTML("beforebegin", "");
    const holder = document.createElement("div");
    holder.innerHTML = html.trim();
    const row = holder.firstElementChild;
    rows.append(row);
    wireRow(row);
    row.querySelector("[data-condition-field]").focus();
    if (rows.querySelectorAll("[data-condition-row]").length >= 20) addButton.disabled = true;
  });
})();
