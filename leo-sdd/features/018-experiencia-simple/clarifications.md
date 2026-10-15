# Clarificaciones — 018 Experiencia simple

## C-01: Alcance acordado (Giannier, 2026-10-15)
"Te lo dejo a tu mejor criterio": igualar y mejorar lo que Ventor ofrece, simple de usar. Sin copiloto ni
diagnóstico con IA por ahora (costo del LLM: cada negocio configura por formulario), sin login con Google
(datos personales), sin dependencias nuevas. El número real se conecta al final, después de esta feature.
**Registrado por:** Claude — ratificado en el chat

## C-02: Qué no se copia de Ventor
- El editor de reglas en texto libre: Leo ya usa capas y situaciones estructuradas (017); el texto libre no se
  puede probar ni apagar regla por regla.
- Nombres de modelos en pantalla: el nivel de inteligencia se muestra como Mejor / Medio / Económico.
- Muchos canales antes de tener calidad en WhatsApp.
**Registrado por:** Claude — por ratificar

## C-03: Precio de oferta y variantes quedan fuera (lectura conservadora)
Tocan dinero (qué precio vale) y las líneas del pedido. Las ofertas siguen por Promociones (monto fijo,
porcentaje, NxM, precio por cantidad), que el motor de precios ya aplica sin acumular.
**Registrado por:** Claude — por ratificar

## C-04: Las fotos del producto son recursos multimedia
`media_assets.product_id` ya existe y `buscar_productos`/`ver_producto` ya ofrecen esos recursos al modelo. No
se crea otra tabla; se agrega el orden. La columna `products.image_key` (sin uso) se deja como está.
**Registrado por:** Claude — por ratificar
