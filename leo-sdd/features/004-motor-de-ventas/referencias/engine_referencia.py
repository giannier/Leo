"""Implementación de referencia de compute_stage / funnel_for (plantillas.md §1).

No es el código final: sirve para que TASK-410 tenga la tabla de casos esperados ya validada.
Ejecutar:  python3 engine_referencia.py   → "N casos OK"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
ANUNCIO_VIGENCIA = timedelta(days=7)


@dataclass
class Camino:
    clave: str
    tipo: str  # venta | asesoria


@dataclass
class Options:
    calificacion_activa: bool = False
    caminos: list[Camino] = field(default_factory=list)

    def camino(self, clave: str | None) -> Camino | None:
        return next((c for c in self.caminos if c.clave == clave), None)


@dataclass
class Pedido:
    estado: str = "borrador"
    items: int = 1
    completo: bool = True


@dataclass
class Facts:
    saludo_enviado: bool = True
    entrada_anuncio_en: datetime | None = None
    camino: str | None = None
    vio_productos: bool = False  # hecho guardado: buscar/ver producto con resultados o lista de productos
    metodos_pago_enviados: bool = False
    derivado_hasta: datetime | None = None
    pedido: Pedido | None = None
    datos_completos: bool = False
    pago_estado: str | None = None  # None | por_verificar | rechazado | confirmado


def camino_efectivo(o: Options, f: Facts) -> Camino | None:
    """El camino elegido; si no hay y ya vio productos o tiene pedido, el primer camino de venta (igual que v21)."""
    if f.camino:
        return o.camino(f.camino)
    if f.vio_productos or f.pedido is not None:
        return next((c for c in o.caminos if c.tipo == "venta"), None)
    return None


def compute_stage(o: Options, f: Facts, now: datetime = NOW) -> str:
    if f.pago_estado == "confirmado":
        return "CERRADO"
    if f.pago_estado in ("por_verificar", "rechazado"):
        return "VERIFICACION"
    if f.derivado_hasta and f.derivado_hasta > now:
        return "DERIVADO"
    if not f.saludo_enviado:
        return "INICIO"
    p = f.pedido
    if p and p.estado == "borrador" and p.items > 0:
        if not p.completo:
            return "PEDIDO"
        if not f.datos_completos:
            return "DATOS"
        return "COBRO"
    camino = camino_efectivo(o, f)
    if camino and camino.tipo == "asesoria":
        return "ASESORIA"
    anuncio_vigente = f.entrada_anuncio_en is not None and now - f.entrada_anuncio_en <= ANUNCIO_VIGENCIA
    if camino is None and anuncio_vigente and not f.vio_productos:
        return "ANUNCIO"
    if o.calificacion_activa and camino is None:
        return "CALIFICAR"
    return "IDENTIFICAR"


FUNNEL = {
    "INICIO": "NUEVO", "CALIFICAR": "NUEVO", "ANUNCIO": "NUEVO", "IDENTIFICAR": "ATENDIDO",
    "PEDIDO": "NEGOCIACION", "DATOS": "NEGOCIACION", "VERIFICACION": "EN_VERIFICACION",
    "CERRADO": "GANADO", "ASESORIA": "DERIVADO", "DERIVADO": "DERIVADO",
}


def funnel_for(stage: str, f: Facts) -> str:
    if stage == "COBRO":
        return "CUENTAS_ENVIADAS" if f.metodos_pago_enviados else "NEGOCIACION"
    return FUNNEL[stage]


# ---------------------------------------------------------------- casos
CERT = Options(True, [Camino("certificacion", "venta"), Camino("programa", "asesoria")])
TIENDA = Options(False, [])
ANUNCIO_HOY = NOW - timedelta(hours=1)
PAQ_INCOMPLETO = Pedido(completo=False)
PAQ_COMPLETO = Pedido()

CASOS: list[tuple[str, Options, Facts, str, str]] = [
    # --- los 18 escenarios de Capacita v21 (harness21.js), traducidos
    ("v21-01 conversación nueva", CERT, Facts(saludo_enviado=False), "INICIO", "NUEVO"),
    ("v21-02 saludó, sin camino", CERT, Facts(), "CALIFICAR", "NUEVO"),
    ("v21-03 viene de anuncio", CERT, Facts(entrada_anuncio_en=ANUNCIO_HOY), "ANUNCIO", "NUEVO"),
    ("v21-04 camino por botón", CERT, Facts(camino="certificacion"), "IDENTIFICAR", "ATENDIDO"),
    ("v21-05 camino escrito (elegir_camino)", CERT, Facts(camino="certificacion"), "IDENTIFICAR", "ATENDIDO"),
    ("v21-06 sin camino pero vio paquetes", CERT, Facts(vio_productos=True), "IDENTIFICAR", "ATENDIDO"),
    ("v21-07 camino programa", CERT, Facts(camino="programa"), "ASESORIA", "DERIVADO"),
    ("v21-08 programa y luego certifica", CERT, Facts(camino="certificacion"), "IDENTIFICAR", "ATENDIDO"),
    ("v21-09 paquete 5 con 2 temas", CERT, Facts(pedido=PAQ_INCOMPLETO), "PEDIDO", "NEGOCIACION"),
    ("v21-10 pedido completo sin datos", CERT, Facts(pedido=PAQ_COMPLETO), "DATOS", "NEGOCIACION"),
    ("v21-11 pedido completo con datos", CERT, Facts(pedido=PAQ_COMPLETO, datos_completos=True), "COBRO", "NEGOCIACION"),
    ("v21-12 métodos de pago enviados", CERT, Facts(pedido=PAQ_COMPLETO, datos_completos=True, metodos_pago_enviados=True), "COBRO", "CUENTAS_ENVIADAS"),
    ("v21-13 un producto suelto sin datos", CERT, Facts(pedido=Pedido()), "DATOS", "NEGOCIACION"),
    ("v21-14 pago en verificación", CERT, Facts(pedido=Pedido(estado="por_confirmar"), pago_estado="por_verificar"), "VERIFICACION", "EN_VERIFICACION"),
    ("v21-15 pago rechazado", CERT, Facts(pedido=PAQ_COMPLETO, datos_completos=True, pago_estado="rechazado"), "VERIFICACION", "EN_VERIFICACION"),
    ("v21-16 pago confirmado", CERT, Facts(pedido=Pedido(estado="confirmado"), pago_estado="confirmado"), "CERRADO", "GANADO"),
    # v21 dejaba la etapa (IDENTIFICAR) y solo el embudo en DERIVADO; en Leo la etapa es DERIVADO (C-04)
    ("v21-17 derivado a mitad de venta", CERT, Facts(camino="certificacion", derivado_hasta=NOW + timedelta(hours=3)), "DERIVADO", "DERIVADO"),
    ("v21-18 derivado con pago en verificación", CERT, Facts(pago_estado="por_verificar", derivado_hasta=NOW + timedelta(hours=3)), "VERIFICACION", "EN_VERIFICACION"),
    # --- casos nuevos de Leo
    ("tienda: sin calificación va a IDENTIFICAR", TIENDA, Facts(), "IDENTIFICAR", "ATENDIDO"),
    ("tienda: anuncio sin calificación", TIENDA, Facts(entrada_anuncio_en=ANUNCIO_HOY), "ANUNCIO", "NUEVO"),
    ("anuncio de hace 8 días ya no cuenta", CERT, Facts(entrada_anuncio_en=NOW - timedelta(days=8)), "CALIFICAR", "NUEVO"),
    ("tienda: anuncio y ya vio productos", TIENDA, Facts(entrada_anuncio_en=ANUNCIO_HOY, vio_productos=True), "IDENTIFICAR", "ATENDIDO"),
    ("anuncio + pedido: manda el pedido", TIENDA, Facts(entrada_anuncio_en=ANUNCIO_HOY, pedido=Pedido(completo=False)), "PEDIDO", "NEGOCIACION"),
    ("pausa de derivación vencida", CERT, Facts(camino="certificacion", derivado_hasta=NOW - timedelta(minutes=1)), "IDENTIFICAR", "ATENDIDO"),
    ("contra entrega confirmado por cliente", TIENDA, Facts(pedido=Pedido(estado="por_confirmar"), pago_estado="por_verificar"), "VERIFICACION", "EN_VERIFICACION"),
    ("contra entrega rechazado vuelve a COBRO", TIENDA, Facts(pedido=PAQ_COMPLETO, datos_completos=True), "COBRO", "NEGOCIACION"),
    ("nuevo pedido después de CERRADO", TIENDA, Facts(pedido=Pedido(completo=False)), "PEDIDO", "NEGOCIACION"),
    ("pedido descartado vuelve a IDENTIFICAR", TIENDA, Facts(pedido=Pedido(estado="descartado")), "IDENTIFICAR", "ATENDIDO"),
    ("borrador vacío no cuenta como pedido", TIENDA, Facts(pedido=Pedido(items=0, completo=False)), "IDENTIFICAR", "ATENDIDO"),
    ("asesoría derivada y pausa vencida: sigue en ASESORIA", CERT, Facts(camino="programa", derivado_hasta=NOW - timedelta(hours=1)), "ASESORIA", "DERIVADO"),
    ("desde ASESORIA cambia a certificar (elegir_camino)", CERT, Facts(camino="certificacion"), "IDENTIFICAR", "ATENDIDO"),
    ("rechazo vigente con pedido completo", CERT, Facts(pedido=PAQ_COMPLETO, datos_completos=True, pago_estado="rechazado"), "VERIFICACION", "EN_VERIFICACION"),
    # al cambiar el pedido, OrderService apaga rechazo_vigente → FactsLoader da pago_estado=None
    ("tras rechazo cambia el pedido y queda incompleto", CERT, Facts(pedido=PAQ_INCOMPLETO, pago_estado=None), "PEDIDO", "NEGOCIACION"),
    ("asesoría con pedido abierto: manda el pedido", CERT, Facts(camino="programa", pedido=PAQ_INCOMPLETO), "PEDIDO", "NEGOCIACION"),
]

if __name__ == "__main__":
    fallas = 0
    for nombre, o, f, etapa, embudo in CASOS:
        e = compute_stage(o, f)
        u = funnel_for(e, f)
        ok = (e, u) == (etapa, embudo)
        fallas += not ok
        print(("OK  " if ok else "XX  ") + f"{nombre:45} -> {e} / {u}" + ("" if ok else f"   (esperado {etapa}/{embudo})"))
    print(f"\n{len(CASOS) - fallas} casos OK" + (f", {fallas} FALLAS" if fallas else ""))
    raise SystemExit(1 if fallas else 0)
