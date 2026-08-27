from cart.exceptions import SesionNoDisponible


def asegurar_session_key(session):
    if session.session_key is None:
        session.create()
    if session.session_key is None:
        raise SesionNoDisponible("No fue posible crear la sesión.")
    return session.session_key
