"""Copy for the transactional emails this feature sends.

The recipients are Peruvian business owners, so the message bodies are the
one place in this codebase written in Spanish - neutral and professional, no
regional slang. Everything around them (identifiers, docstrings, log lines)
stays English like the rest of the project.

Templates are built in Python rather than through a template engine: two
short messages do not justify a rendering dependency, and keeping the escape
call visible next to the interpolation is easier to audit than a template
whose autoescaping you have to go and check.
"""

from __future__ import annotations

from html import escape

from app.providers.email.base import EmailMessage

VERIFICATION_SUBJECT = "Confirma tu correo para activar tu cuenta en Leo"


def _hours_label(hours: int) -> str:
    return "1 hora" if hours == 1 else f"{hours} horas"


def build_verification_email(
    *,
    to: str,
    full_name: str,
    verification_url: str,
    expires_in_hours: int,
) -> EmailMessage:
    """The "confirm your address" message sent at registration and on resend.

    `full_name` comes straight from the registration form and
    `verification_url` carries a token, so both are escaped before they touch
    the HTML body. The plain-text alternative needs no escaping and exists so
    the message is readable in clients that block HTML - and so the link is
    still copy-pasteable if the button does not render.
    """
    safe_name = escape(full_name)
    safe_url = escape(verification_url, quote=True)
    validity = _hours_label(expires_in_hours)

    html_body = f"""\
<!doctype html>
<html lang="es">
  <body style="font-family: Arial, Helvetica, sans-serif; color: #1f2933;">
    <p>Hola {safe_name}:</p>
    <p>
      Gracias por crear tu cuenta en Leo. Para activarla y empezar tu prueba
      gratuita, confirma tu correo electr&oacute;nico.
    </p>
    <p>
      <a href="{safe_url}"
         style="background:#1f6feb;color:#ffffff;padding:12px 20px;
                border-radius:6px;text-decoration:none;display:inline-block;">
        Confirmar mi correo
      </a>
    </p>
    <p>
      Si el bot&oacute;n no funciona, copia y pega este enlace en tu navegador:<br />
      <a href="{safe_url}">{safe_url}</a>
    </p>
    <p>El enlace vence en {validity} y solo se puede usar una vez.</p>
    <p>Si no creaste esta cuenta, puedes ignorar este mensaje.</p>
    <p>El equipo de Leo</p>
  </body>
</html>
"""

    text_body = f"""\
Hola {full_name}:

Gracias por crear tu cuenta en Leo. Para activarla y empezar tu prueba
gratuita, confirma tu correo electronico abriendo este enlace:

{verification_url}

El enlace vence en {validity} y solo se puede usar una vez.

Si no creaste esta cuenta, puedes ignorar este mensaje.

El equipo de Leo
"""

    return EmailMessage(
        to=(to,),
        subject=VERIFICATION_SUBJECT,
        html=html_body,
        text=text_body,
    )


INVITATION_SUBJECT = "Te invitaron a un equipo en Leo"


def build_invitation_email(
    *,
    to: str,
    tenant_name: str,
    inviter_name: str,
    accept_url: str,
    expires_in_days: int,
) -> EmailMessage:
    """The "join this team" message sent by `POST /tenant/team/invitations`.

    `tenant_name` and `inviter_name` are customer-supplied and `accept_url`
    carries a token, so all three are escaped before reaching the HTML body.
    The plain-text alternative needs no escaping, and exists so the link stays
    usable in clients that block HTML.

    The business and the person are both named on purpose: an invitation that
    says only "you have been invited" is indistinguishable from phishing, and
    the recipient has no way to decide whether to trust it.
    """
    safe_tenant = escape(tenant_name)
    safe_inviter = escape(inviter_name)
    safe_url = escape(accept_url, quote=True)
    validity = f"{expires_in_days} dias" if expires_in_days != 1 else "1 dia"

    html_body = f"""\
<p>Hola,</p>
<p><strong>{safe_inviter}</strong> te invito a unirte al equipo de
<strong>{safe_tenant}</strong> en Leo.</p>
<p><a href="{safe_url}">Aceptar la invitacion</a></p>
<p>El enlace vence en {validity} y solo se puede usar una vez.</p>
<p>Si no esperabas esta invitacion, puedes ignorar este mensaje.</p>
<p>El equipo de Leo</p>
"""

    text_body = f"""\
Hola,

{inviter_name} te invito a unirte al equipo de {tenant_name} en Leo.

Acepta la invitacion aqui:
{accept_url}

El enlace vence en {validity} y solo se puede usar una vez.

Si no esperabas esta invitacion, puedes ignorar este mensaje.

El equipo de Leo
"""

    return EmailMessage(
        to=(to,),
        subject=INVITATION_SUBJECT,
        html=html_body,
        text=text_body,
    )
