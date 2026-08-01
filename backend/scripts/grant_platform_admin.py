"""Grant or revoke platform-admin rights.

    make admin EMAIL=giannier18@gmail.com
    make admin EMAIL=alguien@ejemplo.com ARGS=--revoke

This is deliberately a script and not an endpoint. An API that can make
somebody a platform admin is an API that can be used to make somebody a
platform admin - and that role reads every business on the platform, connects
their numbers and extends their trials. The only way in is a person with
database access, which is exactly the bar it should clear.

`is_platform_admin` is read from the database on every request rather than
from the token, so both directions take effect immediately: no sign-out, and
no waiting fifteen minutes for an access token to expire.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.user import User


def main(*, email: str, revoke: bool) -> int:
    normalised = email.strip().lower()
    session_factory = get_session_factory()

    with session_factory() as session:
        # `users.email` is CITEXT, so this matches however it was typed.
        user = session.execute(select(User).where(User.email == normalised)).scalar_one_or_none()
        if user is None:
            print(f"No existe ninguna cuenta con el correo {email!r}.")
            print("Regístrate primero en el panel y vuelve a correr esto.")
            return 1

        wanted = not revoke
        if user.is_platform_admin == wanted:
            state = "ya es" if wanted else "ya no es"
            print(f"{user.email} {state} admin de plataforma. Nada que hacer.")
            return 0

        user.is_platform_admin = wanted
        session.commit()

    if wanted:
        print(f"{email} ahora es admin de plataforma.")
        print("Entra al panel y vas a llegar a /admin. Toma efecto de inmediato:")
        print("el permiso se lee de la base en cada request, no del token.")
    else:
        print(f"{email} ya no es admin de plataforma. Toma efecto de inmediato.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Correo de la cuenta.")
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Quitar el permiso en vez de otorgarlo.",
    )
    args = parser.parse_args()
    raise SystemExit(main(email=args.email, revoke=args.revoke))
