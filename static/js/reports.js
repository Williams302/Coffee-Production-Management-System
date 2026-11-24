const reportPeriod = document.getElementById("report-period");
if (reportPeriod) {
    const dateFields = document.querySelectorAll("[data-report-date]");
    function updateDateFields() {
        for (const field of dateFields) {
            const visible = field.dataset.reportDate === "anchor"
                ? ["daily", "weekly"].includes(reportPeriod.value)
                : reportPeriod.value === "custom";
            field.hidden = !visible;
            field.querySelector("input").disabled = !visible;
        }
    }
    reportPeriod.addEventListener("change", updateDateFields);
    updateDateFields();
}

const printReport = document.getElementById("print-report");
if (printReport) {
    printReport.addEventListener("click", () => window.print());
}

if (typeof Chart !== "undefined") {
    const colors = ["#35644d", "#954b3e", "#806321", "#60656b"];
    for (const canvas of document.querySelectorAll("[data-report-chart]")) {
        const chart = JSON.parse(canvas.dataset.reportChart);
        canvas.parentElement.classList.add("is-ready");
        new Chart(canvas, {
            type: chart.type,
            data: {
                labels: chart.labels,
                datasets: chart.datasets.map((series, index) => ({
                    label: series.label,
                    data: series.data.map(value => Number.isFinite(Number(value)) ? Number(value) : null),
                    backgroundColor: colors[index % colors.length],
                    borderColor: colors[index % colors.length],
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { title: { display: true, text: chart.xTitle } },
                    y: { beginAtZero: true, title: { display: true, text: chart.yTitle } },
                },
                plugins: {
                    legend: { display: true },
                    tooltip: {
                        callbacks: {
                            label: item => item.dataset.label + " · " +
                                chart.datasets[item.datasetIndex].data[item.dataIndex],
                        },
                    },
                },
            },
        });
    }
}
