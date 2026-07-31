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

from collections.abc import Sequence
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


NUMBER_CONNECTED_SUBJECT = "Tu numero de WhatsApp ya esta conectado en Leo"


def build_number_connected_email(
    *,
    to: str,
    tenant_name: str,
    phone_e164: str,
    panel_url: str,
) -> EmailMessage:
    """Sent when an admin finishes connecting a number (TASK-018).

    The customer filed a request and then waited on a human being. Without
    this message the only way to learn the work is done is to keep opening
    the panel, which is exactly the anxiety the request status was meant to
    remove.
    """
    safe_tenant = escape(tenant_name)
    safe_phone = escape(phone_e164)
    safe_url = escape(panel_url, quote=True)

    html_body = f"""\
<p>Hola,</p>
<p>
  Ya conectamos el numero <strong>{safe_phone}</strong> a la cuenta de
  <strong>{safe_tenant}</strong>. Tu agente puede empezar a responder.
</p>
<p><a href="{safe_url}">Ir a mi panel</a></p>
<p>
  Te recomendamos enviar un mensaje de prueba desde otro telefono para
  confirmar que todo funciona como esperas.
</p>
<p>El equipo de Leo</p>
"""

    text_body = f"""\
Hola,

Ya conectamos el numero {phone_e164} a la cuenta de {tenant_name}.
Tu agente puede empezar a responder.

Ir a mi panel:
{panel_url}

Te recomendamos enviar un mensaje de prueba desde otro telefono para
confirmar que todo funciona como esperas.

El equipo de Leo
"""

    return EmailMessage(
        to=(to,),
        subject=NUMBER_CONNECTED_SUBJECT,
        html=html_body,
        text=text_body,
    )


NUMBER_REJECTED_SUBJECT = "No pudimos conectar tu numero de WhatsApp"


def build_number_rejected_email(
    *,
    to: str,
    tenant_name: str,
    phone_e164: str,
    reason: str,
    panel_url: str,
) -> EmailMessage:
    """Sent when an admin rejects a connection request (TASK-018).

    The reason is the whole message. A rejection the customer cannot act on
    just becomes a support ticket asking what to change, so it is quoted here
    rather than left behind a link they have to go and find.
    """
    safe_tenant = escape(tenant_name)
    safe_phone = escape(phone_e164)
    safe_reason = escape(reason)
    safe_url = escape(panel_url, quote=True)

    html_body = f"""\
<p>Hola,</p>
<p>
  Revisamos la solicitud para conectar el numero <strong>{safe_phone}</strong>
  de <strong>{safe_tenant}</strong> y no pudimos completarla.
</p>
<p><strong>Motivo:</strong> {safe_reason}</p>
<p>
  Puedes corregir los datos y volver a enviar la solicitud desde tu panel.
</p>
<p><a href="{safe_url}">Corregir y reenviar</a></p>
<p>El equipo de Leo</p>
"""

    text_body = f"""\
Hola,

Revisamos la solicitud para conectar el numero {phone_e164} de {tenant_name}
y no pudimos completarla.

Motivo: {reason}

Puedes corregir los datos y volver a enviar la solicitud desde tu panel:
{panel_url}

El equipo de Leo
"""

    return EmailMessage(
        to=(to,),
        subject=NUMBER_REJECTED_SUBJECT,
        html=html_body,
        text=text_body,
    )


TRIAL_NOTICE_SUBJECTS = {
    "trial_ending_3d": "A tu prueba de Leo le quedan 3 dias",
    "trial_ending_1d": "Manana termina tu prueba de Leo",
    "trial_ended": "Tu prueba de Leo termino",
}


def build_trial_notice_email(
    *,
    kind: str,
    to: str,
    tenant_name: str,
    panel_url: str,
) -> EmailMessage:
    """The day -3 / -1 / 0 warnings of Clarification 004.

    Three separate messages rather than one with a countdown in it. What the
    customer has to do differs at each point: at -3 there is still time to
    finish evaluating, at -1 the decision is now, and at 0 the agent has
    already stopped and the message has to say so plainly instead of implying
    there is time left.
    """
    safe_tenant = escape(tenant_name)
    safe_url = escape(panel_url, quote=True)

    if kind == "trial_ending_3d":
        lead_html = (
            f"A la prueba gratuita de <strong>{safe_tenant}</strong> le quedan "
            "<strong>3 dias</strong>. Si tu agente ya esta respondiendo como "
            "esperas, este es un buen momento para elegir un plan y no cortar "
            "la atencion."
        )
        lead_text = (
            f"A la prueba gratuita de {tenant_name} le quedan 3 dias. Si tu agente "
            "ya esta respondiendo como esperas, este es un buen momento para "
            "elegir un plan y no cortar la atencion."
        )
        cta = "Ver planes"
    elif kind == "trial_ending_1d":
        lead_html = (
            f"<strong>Manana</strong> termina la prueba gratuita de "
            f"<strong>{safe_tenant}</strong>. Cuando termine, tu agente deja de "
            "responder automaticamente, pero no pierdes nada: tus datos, tu "
            "catalogo y tus conversaciones se quedan donde estan."
        )
        lead_text = (
            f"Manana termina la prueba gratuita de {tenant_name}. Cuando termine, "
            "tu agente deja de responder automaticamente, pero no pierdes nada: "
            "tus datos, tu catalogo y tus conversaciones se quedan donde estan."
        )
        cta = "Elegir un plan"
    else:
        lead_html = (
            f"La prueba gratuita de <strong>{safe_tenant}</strong> termino, asi que "
            "tu agente dejo de responder automaticamente. Los mensajes que te "
            "escriban siguen llegando a tu bandeja y puedes contestarlos a mano "
            "cuando quieras."
        )
        lead_text = (
            f"La prueba gratuita de {tenant_name} termino, asi que tu agente dejo "
            "de responder automaticamente. Los mensajes que te escriban siguen "
            "llegando a tu bandeja y puedes contestarlos a mano cuando quieras."
        )
        cta = "Reactivar mi agente"

    html_body = f"""\
<p>Hola,</p>
<p>{lead_html}</p>
<p><a href="{safe_url}">{cta}</a></p>
<p>El equipo de Leo</p>
"""

    text_body = f"""\
Hola,

{lead_text}

{cta}:
{panel_url}

El equipo de Leo
"""

    return EmailMessage(
        to=(to,),
        subject=TRIAL_NOTICE_SUBJECTS[kind],
        html=html_body,
        text=text_body,
    )


WIZARD_REMINDER_SUBJECT = "Te falta poco para dejar listo tu agente en Leo"


def build_wizard_reminder_email(
    *,
    to: str,
    tenant_name: str,
    pending: Sequence[str],
    panel_url: str,
) -> EmailMessage:
    """The single nudge for an abandoned checklist (§jobs).

    It names what is actually missing. "Completa tu configuracion" tells
    somebody who already thought they were finished nothing at all, and the
    reason a checklist stalls is usually one item the customer did not realise
    was still open.
    """
    safe_tenant = escape(tenant_name)
    safe_url = escape(panel_url, quote=True)
    items_html = "".join(f"<li>{escape(item)}</li>" for item in pending)
    items_text = "\n".join(f"- {item}" for item in pending)

    html_body = f"""\
<p>Hola,</p>
<p>
  Dejaste la configuracion de <strong>{safe_tenant}</strong> a medio camino.
  Te falta esto:
</p>
<ul>{items_html}</ul>
<p><a href="{safe_url}">Continuar donde lo dejaste</a></p>
<p>El equipo de Leo</p>
"""

    text_body = f"""\
Hola,

Dejaste la configuracion de {tenant_name} a medio camino. Te falta esto:

{items_text}

Continuar donde lo dejaste:
{panel_url}

El equipo de Leo
"""

    return EmailMessage(
        to=(to,),
        subject=WIZARD_REMINDER_SUBJECT,
        html=html_body,
        text=text_body,
    )
