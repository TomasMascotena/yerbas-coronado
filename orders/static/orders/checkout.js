(() => {
    const formulario = document.querySelector("[data-checkout-form]");
    const direccion = document.querySelector("[data-direccion-envio]");
    if (!formulario || !direccion) {
        return;
    }

    const actualizarDireccion = () => {
        const modalidad = formulario.querySelector(
            'input[name="modalidad_entrega"]:checked'
        );
        direccion.hidden = Boolean(
            modalidad && modalidad.value !== formulario.dataset.modalidadEnvio
        );
    };

    formulario.querySelectorAll('input[name="modalidad_entrega"]').forEach(
        (opcion) => opcion.addEventListener("change", actualizarDireccion)
    );
    actualizarDireccion();
})();
