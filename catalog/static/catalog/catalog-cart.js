(() => {
    const catalog = document.querySelector(".product-grid");
    const cartLink = document.querySelector(".cart-link");
    const cartCount = document.querySelector(".cart-count");

    if (!catalog || !cartLink || !cartCount) {
        return;
    }

    function updateCartIndicator(data) {
        cartCount.textContent = String(data.cart_quantity);
        cartLink.setAttribute("aria-label", data.cart_label);
    }

    function showStatus(control, message) {
        const status = control?.querySelector(".purchase-control__status");
        if (status) {
            status.textContent = message;
        }
    }

    catalog.addEventListener("submit", async (event) => {
        const form = event.target.closest(".purchase-control form");
        if (!form) {
            return;
        }

        event.preventDefault();
        const control = form.closest(".purchase-control");
        if (control.getAttribute("aria-busy") === "true") {
            return;
        }

        const quantityInput = control.querySelector(".quantity-stepper__input");

        const formData = new FormData(form);
        control.setAttribute("aria-busy", "true");
        control.querySelectorAll("button, input").forEach((field) => {
            field.disabled = true;
        });

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: formData,
                headers: { "X-Requested-With": "XMLHttpRequest" },
                credentials: "same-origin",
            });
            const data = await response.json();
            updateCartIndicator(data);

            if (data.remove_card) {
                control.closest(".product-card")?.remove();
                return;
            }

            let currentControl = control;
            if (data.control_html) {
                control.outerHTML = data.control_html;
                currentControl = catalog.querySelector(
                    `.purchase-control[data-product-id="${control.dataset.productId}"]`
                );
            }

            if (data.out_of_stock) {
                const card = currentControl?.closest(".product-card");
                if (card && !card.querySelector(".availability--unavailable")) {
                    currentControl.insertAdjacentHTML(
                        "beforebegin",
                        '<p class="availability availability--unavailable">Sin stock</p>'
                    );
                }
            }
            showStatus(currentControl, data.message);
        } catch (error) {
            control.removeAttribute("aria-busy");
            control.querySelectorAll("button, input").forEach((field) => {
                field.disabled = false;
            });
            if (quantityInput) {
                quantityInput.disabled = false;
                quantityInput.value = quantityInput.dataset.confirmedValue;
            }
            showStatus(
                control,
                "No pudimos actualizar el carrito. Intentá nuevamente."
            );
        }
    });

    catalog.addEventListener("change", (event) => {
        const input = event.target.closest(".quantity-stepper__input");
        if (!input || input.value === input.dataset.confirmedValue) {
            return;
        }
        const control = input.closest(".purchase-control");
        if (control.getAttribute("aria-busy") !== "true") {
            input.form.requestSubmit();
        }
    });
})();
