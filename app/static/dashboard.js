(() => {
  "use strict";

  const formatDollars = (value, signed = false) => {
    if (!Number.isFinite(value)) return "No data";
    const sign = value < 0 ? "-" : signed ? "+" : "";
    const dollars = Math.floor(Math.abs(value) / 100);
    const cents = Math.abs(value) % 100;
    return `${sign}$${dollars.toLocaleString("en-US")}.${String(cents).padStart(2, "0")}`;
  };

  const color = (styles, name, fallback) => styles.getPropertyValue(name).trim() || fallback;
  const parsePayload = (id) => {
    const element = document.getElementById(id);
    if (!element) return null;
    try {
      return JSON.parse(element.textContent || "");
    } catch {
      return null;
    }
  };

  const initializeCharts = () => {
    const root = document.querySelector("[data-dashboard-root]");
    if (!root || typeof Chart !== "function") return;

    const styles = getComputedStyle(root);
    const line = color(styles, "--dashboard-line", "#c7f56b");
    const income = color(styles, "--dashboard-income", "#459d80");
    const spending = color(styles, "--dashboard-spending", "#e6a24f");
    const grid = color(styles, "--dashboard-grid", "#dbe3d7");
    const muted = color(styles, "--dashboard-muted", "#63766d");
    const violet = color(styles, "--dashboard-violet", "#7b70bd");
    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const animation = reducedMotion ? false : { duration: 350 };
    const tooltip = (signed = false) => ({
      callbacks: {
        label(context) {
          const value = context.parsed?.y ?? context.parsed?.x ?? context.parsed;
          return `${context.dataset.label}: ${formatDollars(Number(value), signed)}`;
        },
      },
    });
    const sharedOptions = {
      animation,
      maintainAspectRatio: false,
      responsive: true,
      plugins: { tooltip: tooltip(true) },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted } },
        y: {
          grid: { color: grid },
          ticks: { color: muted, callback: (value) => formatDollars(Number(value), true) },
        },
      },
    };

    const dashboard = parsePayload("dashboard-chart-data");
    const netWorthCanvas = document.getElementById("net-worth-chart");
    const cashFlowCanvas = document.getElementById("income-spending-chart");
    if (
      dashboard &&
      netWorthCanvas &&
      cashFlowCanvas &&
      Array.isArray(dashboard.net_worth?.labels) &&
      Array.isArray(dashboard.net_worth?.values) &&
      Array.isArray(dashboard.cash_flow?.labels) &&
      Array.isArray(dashboard.cash_flow?.income) &&
      Array.isArray(dashboard.cash_flow?.spending)
    ) {
      try {
        new Chart(netWorthCanvas, {
          type: "line",
          data: {
            labels: dashboard.net_worth.labels,
            datasets: [{ backgroundColor: "rgba(199, 245, 107, 0.2)", borderColor: line, data: dashboard.net_worth.values, fill: true, label: "Net worth", pointBackgroundColor: line, pointRadius: 3, spanGaps: false, tension: 0.32 }],
          },
          options: {
            ...sharedOptions,
            plugins: { ...sharedOptions.plugins, legend: { display: false } },
            scales: {
              ...sharedOptions.scales,
              x: { ...sharedOptions.scales.x, ticks: { color: "#d6e8e1" } },
              y: { ...sharedOptions.scales.y, ticks: { color: "#d6e8e1", callback: (value) => formatDollars(Number(value), true) } },
            },
          },
        });
        new Chart(cashFlowCanvas, {
          type: "bar",
          data: {
            labels: dashboard.cash_flow.labels,
            datasets: [
              { backgroundColor: income, borderRadius: 4, data: dashboard.cash_flow.income, label: "Income" },
              { backgroundColor: spending, borderRadius: 4, data: dashboard.cash_flow.spending, label: "Spending" },
            ],
          },
          options: sharedOptions,
        });
        root.classList.add("dashboard-charts-ready");
      } catch {
        // The semantic fallback tables stay visible when chart enhancement fails.
      }
    }

    const spendingData = parsePayload("spending-chart-data");
    const categoryCanvas = document.getElementById("spending-category-chart");
    const merchantCanvas = document.getElementById("spending-merchant-chart");
    const palette = [spending, income, violet, "#3676a8", "#ba5b75", "#8b7338", "#4f867f"];
    if (
      spendingData &&
      categoryCanvas &&
      Array.isArray(spendingData.categories?.labels) &&
      Array.isArray(spendingData.categories?.values)
    ) {
      try {
        new Chart(categoryCanvas, {
          type: "doughnut",
          data: {
            labels: spendingData.categories.labels,
            datasets: [{
              backgroundColor: spendingData.categories.labels.map((_, index) => palette[index % palette.length]),
              borderColor: "#ffffff",
              borderWidth: 2,
              data: spendingData.categories.values,
              label: "Spending",
            }],
          },
          options: { animation, maintainAspectRatio: false, responsive: true, plugins: { tooltip: tooltip(), legend: { position: "bottom", labels: { color: muted, boxWidth: 12 } } } },
        });
        root.classList.add("spending-category-chart-ready");
      } catch {
        // The semantic fallback table stays visible when chart enhancement fails.
      }
    }
    if (
      spendingData &&
      merchantCanvas &&
      Array.isArray(spendingData.merchants?.labels) &&
      Array.isArray(spendingData.merchants?.values)
    ) {
      try {
        new Chart(merchantCanvas, {
          type: "bar",
          data: {
            labels: spendingData.merchants.labels,
            datasets: [{ backgroundColor: violet, borderRadius: 4, data: spendingData.merchants.values, label: "Spending" }],
          },
          options: {
            animation,
            indexAxis: "y",
            maintainAspectRatio: false,
            responsive: true,
            plugins: { tooltip: tooltip(), legend: { display: false } },
            scales: {
              x: { grid: { color: grid }, ticks: { color: muted, callback: (value) => formatDollars(Number(value)) } },
              y: { grid: { display: false }, ticks: { color: muted } },
            },
          },
        });
        root.classList.add("spending-merchant-chart-ready");
      } catch {
        // The semantic fallback table stays visible when chart enhancement fails.
      }
    }
  };

  document.addEventListener("DOMContentLoaded", initializeCharts);
})();
