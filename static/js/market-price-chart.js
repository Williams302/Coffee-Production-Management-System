const marketCanvas = document.getElementById("market-price-chart");

if (marketCanvas && typeof Chart !== "undefined") {
    const series = JSON.parse(marketCanvas.dataset.series);
    const dayInMilliseconds = 86400000;
    const colors = ["#35644d", "#954b3e", "#806321", "#375c82", "#70507a"];
    const dashes = [[], [6, 3], [2, 3]];

    marketCanvas.parentElement.classList.add("is-ready");
    new Chart(marketCanvas, {
        type: "line",
        data: {
            datasets: series.map(({ label, points }, index) => ({
                label,
                data: points.map(([date, price]) => ({
                    x: Date.parse(date) / dayInMilliseconds,
                    y: Number(price),
                    date,
                    price,
                })),
                borderColor: colors[index % colors.length],
                backgroundColor: colors[index % colors.length],
                borderDash: dashes[Math.floor(index / colors.length) % dashes.length],
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: "linear",
                    title: { display: true, text: "Price date" },
                    ticks: {
                        precision: 0,
                        callback: day => new Date(day * dayInMilliseconds).toISOString().slice(0, 10),
                    },
                },
                y: { beginAtZero: true, title: { display: true, text: "Price (UGX/kg)" } },
            },
            plugins: {
                legend: { display: true },
                tooltip: {
                    callbacks: {
                        title: items => items[0].raw.date,
                        label: item => item.dataset.label + " · UGX " +
                            BigInt(item.raw.price).toLocaleString("en-UG") + "/kg",
                    },
                },
            },
        },
    });
}
