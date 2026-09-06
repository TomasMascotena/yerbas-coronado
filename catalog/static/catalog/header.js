(() => {
    const toggle = document.querySelector(".site-search-toggle");
    const form = document.querySelector(".site-search");
    const input = document.querySelector("#site-search");

    if (!toggle || !form || !input) {
        return;
    }

    const mobileQuery = window.matchMedia("(max-width: 40rem)");

    function setOpen(open, { focus = false } = {}) {
        form.classList.toggle("site-search--open", open);
        toggle.setAttribute("aria-expanded", String(open));
        toggle.setAttribute("aria-label", open ? "Cerrar buscador" : "Abrir buscador");
        if (open && focus) {
            input.focus();
        }
    }

    toggle.addEventListener("click", () => {
        const open = toggle.getAttribute("aria-expanded") !== "true";
        setOpen(open, { focus: open });
    });

    form.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && mobileQuery.matches) {
            setOpen(false);
            toggle.focus();
        }
    });

    mobileQuery.addEventListener("change", () => setOpen(false));
})();
