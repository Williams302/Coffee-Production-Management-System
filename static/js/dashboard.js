const canvas = document.getElementById("harvest-chart");

if (canvas && typeof Chart !== "undefined") {
    const colors = {
        Cherry: "#954b3e",
        Parchment: "#8a6b35",
        "Green Beans": "#35644d",
        Unclassified: "#60656b",
    };
    const datasets = JSON.parse(canvas.dataset.series).map(series => ({
        ...series,
        backgroundColor: colors[series.label],
        borderRadius: 3,
    }));

    canvas.parentElement.classList.add("is-ready");
    new Chart(canvas, {
        type: "bar",
        data: { labels: JSON.parse(canvas.dataset.labels), datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { title: { display: true, text: "Harvest date" } },
                y: { beginAtZero: true, title: { display: true, text: "Weight (kg)" } },
            },
        },
    });
}
