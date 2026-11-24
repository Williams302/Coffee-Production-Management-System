const plotSelect = document.getElementById("plot_id");
const plotVariety = document.getElementById("plot-variety");

if (plotSelect && plotVariety) {
    plotSelect.addEventListener("change", () => {
        plotVariety.textContent = plotSelect.value
            ? plotSelect.selectedOptions[0].dataset.variety
            : "Choose a plot";
    });
}
