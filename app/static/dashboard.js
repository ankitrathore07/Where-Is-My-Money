(() => {
  "use strict";

  const formatSignedDollars = (value) => {
    if (!Number.isFinite(value)) return "No data";
    const sign = value < 0 ? "-" : "+";
    const dollars = Math.floor(Math.abs(value) / 100);
    const cents = Math.abs(value) % 100;
    return `${sign}$${dollars.toLocaleString("en-US")}.${String(cents).padStart(2, "0")}`;
  };

  const color = (styles, name, fallback) => styles.getPropertyValue(name).trim() || fallback;

  const initializeCharts = () => {
    const root = document.querySelector("[data-dashboard-root]");
    const dataElement = document.getElementById("dashboard-chart-data");
    const netWorthCanvas = document.getElementById("net-worth-chart");
    const cashFlowCanvas = document.getElementById("income-spending-chart");
    if (!root || !dataElement || !netWorthCanvas || !cashFlowCanvas || typeof Chart !== "function") {
      return;
    }

    try {
      const data = JSON.parse(dataElement.textContent || "");
      const netWorth = data.net_worth;
      const cashFlow = data.cash_flow;
      if (
        !Array.isArray(netWorth?.labels) ||
        !Array.isArray(netWorth?.values) ||
        !Array.isArray(cashFlow?.labels) ||
        !Array.isArray(cashFlow?.income) ||
        !Array.isArray(cashFlow?.spending)
      ) {
        return;
      }

      const styles = getComputedStyle(root);
      const line = color(styles, "--dashboard-line", "#c7f56b");
      const income = color(styles, "--dashboard-income", "#459d80");
      const spending = color(styles, "--dashboard-spending", "#e6a24f");
      const grid = color(styles, "--dashboard-grid", "#dbe3d7");
      const muted = color(styles, "--dashboard-muted", "#63766d");
      const reducedMotion =
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const animation = reducedMotion ? false : { duration: 350 };
      const tooltip = {
        callbacks: {
          label(context) {
            return `${context.dataset.label}: ${formatSignedDollars(context.parsed.y)}`;
          },
        },
      };
      const sharedOptions = {
        animation,
        maintainAspectRatio: false,
        responsive: true,
        plugins: { tooltip },
        scales: {
          x: { grid: { display: false }, ticks: { color: muted } },
          y: {
            grid: { color: grid },
            ticks: { color: muted, callback: (value) => formatSignedDollars(Number(value)) },
          },
        },
      };

      new Chart(netWorthCanvas, {
        type: "line",
        data: {
          labels: netWorth.labels,
          datasets: [{ backgroundColor: "rgba(199, 245, 107, 0.2)", borderColor: line, data: netWorth.values, fill: true, label: "Net worth", pointBackgroundColor: line, pointRadius: 3, spanGaps: false, tension: 0.32 }],
        },
        options: {
          ...sharedOptions,
          plugins: { ...sharedOptions.plugins, legend: { display: false } },
          scales: {
            ...sharedOptions.scales,
            x: { ...sharedOptions.scales.x, ticks: { color: "#d6e8e1" } },
            y: { ...sharedOptions.scales.y, ticks: { color: "#d6e8e1", callback: (value) => formatSignedDollars(Number(value)) } },
          },
        },
      });
      new Chart(cashFlowCanvas, {
        type: "bar",
        data: {
          labels: cashFlow.labels,
          datasets: [
            { backgroundColor: income, borderRadius: 4, data: cashFlow.income, label: "Income" },
            { backgroundColor: spending, borderRadius: 4, data: cashFlow.spending, label: "Spending" },
          ],
        },
        options: sharedOptions,
      });
      root.classList.add("dashboard-charts-ready");
    } catch {
      // The semantic fallback tables stay visible when chart enhancement fails.
    }
  };

  document.addEventListener("DOMContentLoaded", initializeCharts);
})();
