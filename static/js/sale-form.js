const saleForm = document.getElementById("sale-form");

if (saleForm) {
    const quantity = saleForm.elements.quantity_kg;
    const price = saleForm.elements.unit_price_ugx;
    const status = saleForm.elements.payment_status;
    const received = saleForm.elements.received_ugx;
    const totalOutput = document.getElementById("sale-total");
    const balanceOutput = document.getElementById("sale-balance");
    const maxInteger = 9223372036854775807n;

    function updateSale() {
        const partial = status.value === "Partially Paid";
        received.disabled = !partial;
        received.required = partial;
        totalOutput.textContent = "—";
        balanceOutput.textContent = "—";

        const match = /^(\d{1,19})(?:\.(\d{1,3}))?$/.exec(quantity.value);
        if (!match || !/^\d{1,19}$/.test(price.value)) return;
        const grams = BigInt(match[1]) * 1000n + BigInt((match[2] || "").padEnd(3, "0"));
        const unitPrice = BigInt(price.value);
        if (grams === 0n || grams > maxInteger || unitPrice > maxInteger) return;
        const total = (grams * unitPrice + 500n) / 1000n;
        if (total > maxInteger) return;

        totalOutput.textContent = total.toLocaleString("en-UG");
        if (status.value === "Paid") {
            balanceOutput.textContent = "0";
        } else if (status.value === "Pending" && total > 0n) {
            balanceOutput.textContent = total.toLocaleString("en-UG");
        } else if (partial && /^\d{1,19}$/.test(received.value)) {
            const paid = BigInt(received.value);
            if (paid > 0n && paid < total) {
                balanceOutput.textContent = (total - paid).toLocaleString("en-UG");
            }
        }
    }

    saleForm.addEventListener("input", updateSale);
    updateSale();
}
