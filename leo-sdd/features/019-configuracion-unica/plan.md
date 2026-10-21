# 019 · Una sola línea de configuración (propuesta, 2026-10-21)

Estado: **propuesta para ratificar**. Nace de una prueba real con un negocio de tecnología (servicios de precio
fijo, servicios por cotización con precio referencial, y pronto un producto SaaS). Giannier encontró que la
configuración pide lo mismo varias veces, no deja crear categorías, y está llena de ejemplos de cursos y
certificaciones (el flujo de Capacita v21 con el que se empezó).

## 1. Qué se encontró (con evidencia)

### 1.1 El mismo dato se pide en varios lugares, y a veces no se usa
| Dato | Dónde se pide hoy | Quién lo lee |
|---|---|---|
| Web | Asistente de inicio, paso 1 ("Página web o red social") → `business_profiles.website` | **nadie**: el agente no lee ese campo |
| Web | Información → Contacto → `contacto.web` | el agente, para darla al cliente |
| Web | Conocimiento → "Desde mi web" (fuente de conocimiento) | el agente, para responder con su contenido |
| Saludo, reglas de derivación | Paso 2 del asistente y Comportamiento | mismos campos, dos pantallas |
| Pagos, productos, conocimiento | Pasos 3 a 5 del asistente y sus pantallas del Asistente | mismas tablas, dos pantallas |
| Progreso | Lista del asistente (5 ítems) y Resumen del Asistente (7 pasos + 3 extras, "52 %") | dos cuentas distintas del mismo avance |

Conclusión: lo que se escribe en el asistente de inicio **sí** se usa (son las mismas tablas), con una excepción
grave: la web del paso 1 es un dato muerto. Y la web hay que darla tres veces para que haga las tres cosas.

### 1.2 Categorías
- Escribir una categoría en el editor de producto sí crea una categoría administrada (`product_categories`).
- Pero el asistente de inicio no pide categoría, la importación no la menciona, y el cliente `api.assistant.saveCategory`
  (renombrar, ordenar, apagar) **no tiene pantalla**. Resultado: `product_categories` con 0 filas y 8 productos sin
  categoría. Los modelos buscan por categoría ("hosting"), y antes de la corrección del 2026-10-20 eso hacía que
  la búsqueda no encontrara nada.

### 1.3 Lenguaje y ejemplos de un solo rubro
Cursos y certificaciones aparece como si fuera lo normal, en pantallas de cualquier negocio:
- Editor de producto: "Ej.: Certificado en SIAF", categoría "Ej.: Certificaciones", etiquetas "oferta, verano, SIAF",
  y el campo "Paquete de N selecciones" ("5 certificaciones a elegir") visible para todos.
- Saludo del asistente de inicio: "Bienvenido a Escuela Capacita".
- Descripción de la herramienta de búsqueda: "productos, servicios o certificados".
- Rubro "servicios": "Un servicio de cotización no tiene precio en el chat", que contradice un servicio con
  precio referencial.
- Biblioteca de campañas: "Nuevo curso o programa" (esa solo sale para el rubro instituto, bien).

### 1.4 "Cómo vendes" obliga a elegir una plantilla por negocio
Las tres plantillas (Venta de productos, Servicios con cotización, Cursos y certificaciones) las elige el dueño
por nombre y valen para todo el negocio. Pero un negocio real mezcla: servicios de precio fijo (hosting), servicios
con precio referencial por cotización (página web desde S/ 800), cotización pura (sistemas a medida,
automatizaciones) y, después, planes SaaS con precio y características.
La plataforma **ya sabe** distinguir esto por producto (`modo_venta` = precio fijo o se cotiza, con "precio
referencial" opcional); lo que falla es que la decisión parece del negocio y no del producto, y que el rubro "otros"
(donde cae "tecnología") sugiere "Venta de productos" y no tiene reglas propias. No existe un rubro de tecnología.

### 1.5 Otras incoherencias detectadas
- La derivación para cotizar puede registrarse con motivo `cotizacion` (lo que dice la herramienta de pedidos)
  o `asesoria` (lo que decía la plantilla de servicios): el CRM solo reconocía `asesoria`.
- Un precio se muestra sin periodo ("S/ 110.00" para "Hosting anual"): no hay dato de periodicidad (mes, año,
  único), que un producto SaaS necesita.

## 2. Principios

1. **Se pide una vez y se usa en todas partes.** Cada dato tiene un solo lugar donde se escribe.
2. **Un solo recorrido.** El asistente de inicio y el Asistente son las mismas pantallas, en orden guiado la primera
   vez y como menú después. Un solo progreso.
3. **El dueño describe su negocio con palabras de su negocio**, no con nombres de plantillas. La plantilla se deduce
   de lo que carga y se puede cambiar en "Avanzado".
4. **La venta se decide por producto**: precio fijo, se cotiza (con o sin precio referencial), con periodo, paquete.
5. **Ejemplos neutros, o del rubro elegido.** Nada de cursos para quien no vende cursos.

## 3. El recorrido propuesto

1. **Tu negocio**: nombre, rubro (se agrega "Tecnología y software"), moneda, zona y **la web, una sola vez**.
   Con la web: "Leer mi web ahora" (la lee y la guarda como conocimiento) y propone, para confirmar, la descripción
   del negocio y el contacto (correo, teléfono y web) que encuentre, sin modelo de lenguaje (sin costo por mensaje).
2. **Qué vendes**: productos con nombre, descripción (lo que el agente cuenta), categoría (con "crear" en el
   mismo campo), **tipo** (precio fijo / se cotiza con precio referencial) y **periodo** (único, mes, año).
   La plantilla se elige sola según lo cargado, y se muestra "Cómo venderá tu agente" con una línea por tipo.
3. **Cómo te pagan** (igual que hoy).
4. **Cómo atiende tu agente**: nombre, tono, objetivo (vender por defecto), saludo con ejemplo del rubro.
5. **Lo que debe saber**: muestra lo ya leído de la web y deja agregar documentos y respuestas frecuentes.
6. **Pruébalo y enciéndelo**: simulador, situaciones sugeridas del rubro y conexión de WhatsApp, con **una sola
   lista de progreso**.

## 4. Dueño de cada dato (una sola vez)
| Dato | Se escribe en | Se usa en |
|---|---|---|
| Nombre, rubro, moneda, zona, horario | Tu negocio | prompt del agente, plantilla sugerida |
| Web | Tu negocio (`contacto.web` pasa a ser el único campo; `business_profiles.website` se retira) | contacto que da el agente y fuente de conocimiento |
| Descripción del negocio | Tu negocio (prellenada desde la web) | prompt |
| Correo y teléfono de contacto | Información → Contacto (prellenados desde la web y la cuenta) | derivaciones |
| Productos, categorías, tipo, periodo | Productos | búsquedas, pedidos, derivación para cotizar |
| Plantilla | se deduce; ajustable en "Avanzado" | motor de ventas |

## 5. Fases

**Fase 1 · Correcciones (días, sin decisiones de costo)**
1. La web: un solo campo; `profile.website` migra a `contacto.web`; el paso 1 ofrece "Leer mi web".
2. Categorías: campo "Categoría" con "crear" en el alta de producto (asistente y Catálogo), columna en la importación,
   y pantalla "Categorías" (renombrar, ordenar, apagar) en Productos.
3. Ejemplos neutros (editor, saludo, etiquetas, descripción de herramienta); "Paquete de N selecciones" solo
   si la plantilla es Cursos y certificaciones.
4. Rubro "servicios" y "otros": reglas que admiten precio referencial ("desde S/ X, se cotiza"); nuevo rubro
   "Tecnología y software" que sugiere Servicios.
5. CRM: `cotizacion` y `asesoria` dejan el lead en "Interesado"; la plantilla de servicios usa `cotizacion`.
6. Una sola lista de progreso.

**Fase 2 · Producto SaaS y precios con periodo**
7. Columna `periodo` en productos (único, mes, año): el agente dice "S/ 110 al año". El campo "características"
   se cubre con descripción en lista.
8. "Precio referencial" visible para el agente como "desde S/ X, se cotiza".

**Fase 3 · Una plantilla que se adapta**
9. Decidir si Venta de productos y Servicios se funden en una sola ("Ventas") que se comporta según el
   tipo de cada producto, y Cursos y certificaciones queda como especialización del rubro educativo.
   Es un cambio del motor y se hace con sus evals.

**Fase 4 · Autollenado con IA (opcional)**
10. Proponer productos y descripciones a partir de la web con un modelo de lenguaje.
    Tiene costo por uso y es decisión de Giannier.

## 6. Decisiones que necesito de Giannier
1. ¿Fase 1 completa ahora?
2. Autollenado desde la web **sin modelo** (gratis) en Fase 1 y con modelo (costo) solo en Fase 4: ¿de acuerdo?
3. ¿Se agrega el rubro "Tecnología y software" (y cuáles otros: cafetería, etc.)?
4. ¿Se funden las plantillas (Fase 3)?

## 7. Cómo se sabrá que quedó bien
- Un negocio nuevo de tecnología llega a "Prueba tu agente" sin escribir nada dos veces y sin ver un solo ejemplo
  de cursos.
- La web se escribe una vez y aparece como contacto y como conocimiento.
- Una categoría se crea y se renombra desde la pantalla, y el agente la usa.
- Un servicio de precio fijo se agrega al pedido; uno por cotización con precio referencial se explica "desde S/ 800"
  y se deriva con resumen.
- Un solo porcentaje de avance.
