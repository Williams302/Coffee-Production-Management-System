const salesCanvas = document.getElementById("sales-chart");

if (salesCanvas && typeof Chart !== "undefined") {
    const trend = JSON.parse(salesCanvas.dataset.trend);
    salesCanvas.parentElement.classList.add("is-ready");
    new Chart(salesCanvas, {
        type: "bar",
        data: {
            labels: trend.map(([date]) => date),
            datasets: [{
                label: "Sales value (UGX)",
                data: trend.map(([, value]) => Number(value)),
                backgroundColor: "#35644d",
                borderRadius: 3,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                tooltip: {
                    callbacks: {
                        label: item => "UGX " + BigInt(trend[item.dataIndex][1]).toLocaleString("en-UG"),
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: "Sale date" } },
                y: { beginAtZero: true, title: { display: true, text: "Value (UGX)" } },
            },
        },
    });
}
