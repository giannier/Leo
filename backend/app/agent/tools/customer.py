"""`registrar_datos_cliente`: what the customer tells us about themselves (TASK-427).

A global tool: the name can be given at any stage, and the data the template asks
for (`datos_requeridos`) whenever the customer offers them. It accepts **partial**
data and judges each field on its own - the valid ones are saved, the others come
back as `invalidos` with a reason the model can turn into a question - and always
answers `faltan`, computed from the database, so the model asks for exactly what
is still missing.

Where things go:

* The customer's own data (name, DNI, e-mail, phone, address...) go to
  `contacts.custom_data`, validated field by field (`orders.customer_data`).
* `tipo_entrega` and `zona_envio` go to the **order**, checked against what the
  business offers (*Información del negocio -> Entregas*, C-22), and the shipping is
  recalculated. A business that only offers pickup does not make the customer
  choose: the type is set by itself.
* Delivery fields are only accepted when the order needs delivery: something
  physical in it and a business that delivers or lets people pick up. A digital
  product never asks for an address.
* Any change turns `rechazo_vigente` off (C-21), which is how a customer whose
  payment was rejected gets out of VERIFICACION.

The answer names fields, never values: the values are personal data (C-20) and the
reasons are written so that they can be logged.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.registry import ToolContext, ToolResult
from app.models.orders import Order
from app.orders.customer_data import ORDER_FIELDS, InvalidField, validate_field
from app.orders.service import InvalidDelivery, OrderService
from app.process.facts import missing_customer_data
from app.repositories.channel import ContactRepository
from app.repositories.conversation import ConversationRepository

DELIVERY_FIELDS = ("tipo_entrega", "direccion", "referencia", "zona_envio")
DELIVERY_TYPES = ("delivery", "recojo")

_NOT_ASKED = "ese dato no se pide ahora."


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _allowed(context: ToolContext, service: OrderService, order: Order | None) -> set[str]:
    """The fields this call may set: the name, the template's, and delivery's if needed."""
    allowed = {"nombres"}
    if context.options is not None:
        allowed.update(context.options.datos_requeridos)
    if order is not None and service.requires_delivery(order):
        allowed.update(DELIVERY_FIELDS)
    return allowed


async def registrar_datos_cliente(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    """Save the customer's data, partial or not; answer what is saved, invalid and missing."""
    if context.conversation_id is None:
        return ToolResult.error("No hay una conversación abierta.")
    session, tenant_id = context.session, context.tenant_id
    conversation = ConversationRepository(session, tenant_id).get(context.conversation_id)
    if conversation is None or conversation.contact_id is None:
        return ToolResult.error("No se encontró al cliente de esta conversación.")
    contacts = ContactRepository(session, tenant_id)
    contact = contacts.get(conversation.contact_id)
    if contact is None:
        return ToolResult.error("No se encontró al cliente de esta conversación.")

    service = OrderService(session, tenant_id)
    active = service.active_for(conversation.id)
    order = active if active is not None and active.estado == "borrador" else None
    allowed = _allowed(context, service, order)

    invalid: dict[str, str] = {}
    contact_values: dict[str, str] = {}
    delivery: dict[str, str] = {}
    for field, raw in arguments.items():
        if _blank(raw):
            continue
        if field not in allowed:
            invalid[field] = _NOT_ASKED
        elif field == "tipo_entrega":
            kind = str(raw).strip().lower()
            if kind in DELIVERY_TYPES:
                delivery[field] = kind
            else:
                invalid[field] = "tipo_entrega debe ser delivery o recojo."
        elif field in ORDER_FIELDS:
            delivery[field] = str(raw).strip()
        else:
            try:
                contact_values[field] = validate_field(field, raw)
            except InvalidField as exc:
                invalid[field] = str(exc)

    saved: list[str] = []
    changed = False
    if contact_values:
        changed = any(contact.custom_data.get(k) != v for k, v in contact_values.items())
        contacts.merge_custom_data(contact, contact_values)
        saved.extend(contact_values)

    valid_zones: tuple[str, ...] = ()
    fixed_pickup = False
    if order is not None:
        if changed:
            service.customer_data_changed(order)
        # The type first, then the zone: each is checked against the business's
        # offer, and a refused zone must not undo an accepted type.
        if "tipo_entrega" in delivery:
            kind = delivery["tipo_entrega"]
            try:
                if kind != order.tipo_entrega:
                    service.set_delivery(order, tipo_entrega=kind)
                saved.append("tipo_entrega")
            except InvalidDelivery as exc:
                invalid["tipo_entrega"] = f"{exc}."
        if "zona_envio" in delivery:
            zone = delivery["zona_envio"]
            try:
                if zone.casefold() != (order.zona_envio or "").casefold():
                    service.set_delivery(order, zona=zone)
                saved.append("zona_envio")
            except InvalidDelivery as exc:
                invalid["zona_envio"] = f"{exc}."
                valid_zones = exc.valid_zones or valid_zones
        fixed_pickup = _settle_pickup(service, order)

    missing = missing_customer_data(
        contact,
        context.options.datos_requeridos if context.options is not None else (),
        service.entregas(),
        requiere_entrega=order is not None and service.requires_delivery(order),
        tipo_entrega=order.tipo_entrega if order is not None else None,
        zona_envio=order.zona_envio if order is not None else None,
    )
    data: dict[str, Any] = {"guardados": saved, "invalidos": invalid, "faltan": missing}
    if valid_zones:
        data["zonas_validas"] = list(valid_zones)
    if fixed_pickup:
        data["tipo_entrega"] = "recojo"

    text = _describe(saved, invalid, missing, valid_zones, fixed_pickup)
    if not saved and not fixed_pickup:
        if not invalid:
            return ToolResult.error("No recibí ningún dato para guardar.")
        return ToolResult("error", data, f"No se guardó nada. {text}", None)
    return ToolResult.hecho(data=data, texto=text, decir=_say(invalid, missing))


def _settle_pickup(service: OrderService, order: Order) -> bool:
    """A business that only offers pickup does not make the customer choose (C-22)."""
    entregas = service.entregas()
    if (
        entregas is None
        or order.tipo_entrega is not None
        or not entregas.recojo_en_tienda
        or entregas.hace_delivery
        or not service.requires_delivery(order)
    ):
        return False
    service.set_delivery(order, tipo_entrega="recojo")
    return True


def _describe(
    saved: list[str],
    invalid: dict[str, str],
    missing: list[str],
    zones: tuple[str, ...],
    fixed_pickup: bool,
) -> str:
    lines: list[str] = []
    if saved:
        lines.append("Guardado: " + ", ".join(saved) + ".")
    if fixed_pickup:
        lines.append("El negocio solo ofrece recojo en tienda: quedó como recojo.")
    for field, reason in invalid.items():
        lines.append(f"No válido: {field} ({reason})")
    if zones:
        lines.append("Zonas de entrega válidas: " + ", ".join(zones) + ".")
    lines.append(
        "Faltan: " + ", ".join(missing) + "." if missing else "Ya no falta ningún dato del cliente."
    )
    return "\n".join(lines)


def _say(invalid: dict[str, str], missing: list[str]) -> str:
    if invalid:
        return (
            "Hubo datos que no se pudieron guardar: vuelve a pedírselos con tus palabras, sin "
            "repetirlos en voz alta. Di solo lo que quedó guardado."
        )
    if missing:
        return "Pide lo que falta, una cosa o dos a la vez, sin repetir lo que ya te dio."
    return "Ya están todos los datos. Sigue con el siguiente paso del pedido."
