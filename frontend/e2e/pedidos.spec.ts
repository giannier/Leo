// See `./fixtures` for why the rate limiter is reset rather than raised.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Pedidos (TASK-432): the operator's side of a sale.
 *
 * Runs against the **real stack**, and says plainly what is set up from outside:
 *
 * - The orders are inserted with `psql`, the way the TASK-422 spec writes the
 *   simulator's buttons. Producing them by talking to the agent would call a
 *   paid model (the dev stack runs `LLM_PROFILE=openai`), and whether a model
 *   builds an order well is the evals' business. What is under test here is what
 *   the panel does with an order that exists: list it, show it, decide on it.
 * - The receipt's *image* is not drawn: the seeded proof points at an object
 *   that is not in storage, so the screen shows its "no pudimos cargar la
 *   imagen" state. The fields the system read from the receipt, and the flags on
 *   them, are what is asserted.
 * - The three simulator orders go through the real API: confirm, reject and
 *   status are the real endpoints and the notice to the customer is the real
 *   `aviso_cliente`. The fourth order is a WhatsApp one whose 24 h window is
 *   closed, so no provider is ever called (C-09).
 *
 * Needs the stack up (`make up`) and Postgres reachable through Docker.
 */

const run = promisify(execFile);
const POSTGRES_CONTAINER = process.env.E2E_POSTGRES_CONTAINER ?? "leo-dev-postgres-1";

async function psql(sql: string): Promise<string> {
  const { stdout } = await run("docker", [
    "exec",
    POSTGRES_CONTAINER,
    "psql",
    "-U",
    process.env.E2E_POSTGRES_USER ?? "leo",
    "-d",
    process.env.E2E_POSTGRES_DB ?? "leo",
    "-v",
    "ON_ERROR_STOP=1",
    "-At",
    "-c",
    sql,
  ]);
  return stdout.trim();
}

async function signedIn(page: import("@playwright/test").Page): Promise<string> {
  const user = freshIdentity();
  await page.goto("/register");
  await page.getByLabel("Tu nombre").fill(user.fullName);
  await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Crear mi cuenta" }).click();
  const message = await waitForEmail(user.email);
  await page.goto(verificationPath(message));
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL("/", { timeout: 15_000 });
  return user.email;
}

/**
 * Four orders for the business of `email`:
 *
 * 1. simulator, prepaid, receipt read as 450.00 against a 500.00 total (flagged);
 * 2. simulator, cash on delivery, no receipt;
 * 3. simulator, prepaid, receipt without flags (the one that gets rejected);
 * 4. WhatsApp, cash on delivery, 24 h window closed.
 */
async function seedOrders(email: string): Promise<void> {
  await psql(`
    do $$
    declare
      t uuid; sbx_contact uuid; sbx_conv uuid; wa_contact uuid; wa_conv uuid;
      o1 uuid; o2 uuid; o3 uuid; o4 uuid;
    begin
      select tu.tenant_id into strict t from tenant_users tu
        join users u on u.id = tu.user_id where u.email = '${email}';

      insert into contacts (tenant_id, phone_e164, profile_name, custom_data)
        values (t, '+51987654628', 'Ana Pérez Soto', '{"nombres": "Ana Pérez Soto"}') returning id into sbx_contact;
      insert into conversations (tenant_id, channel, contact_id)
        values (t, 'sandbox', sbx_contact) returning id into sbx_conv;
      insert into contacts (tenant_id, phone_e164, profile_name, custom_data)
        values (t, '+51912345001', 'Luis Rojas', '{"nombres": "Luis Rojas", "direccion": "Av. Lima 123"}') returning id into wa_contact;
      insert into conversations (tenant_id, channel, contact_id)
        values (t, 'whatsapp', wa_contact) returning id into wa_conv;

      insert into orders (tenant_id, conversation_id, contact_id, numero, estado, modalidad_pago, moneda,
                          subtotal, total, is_sandbox, datos_cliente)
        values (t, sbx_conv, sbx_contact, 1, 'por_confirmar', 'previo', 'PEN', 500, 500, true,
                '{"nombres": "Ana Pérez Soto", "telefono": "+51987654628"}') returning id into o1;
      insert into order_items (tenant_id, order_id, nombre, precio_unitario, cantidad, subtotal)
        values (t, o1, 'Paquete 5 certificaciones', 500, 1, 500);
      insert into payment_proofs (tenant_id, order_id, conversation_id, storage_key, monto, moneda,
                                  codigo_operacion, entidad, destinatario, confianza, observaciones)
        values (t, o1, sbx_conv, 'e2e/none.jpg', 450, 'PEN', 'OP-1001', 'Yape', 'Academia Capacita', 0.82,
                '{monto_distinto}');

      insert into orders (tenant_id, conversation_id, contact_id, numero, estado, modalidad_pago, moneda,
                          subtotal, total, is_sandbox, datos_cliente)
        values (t, sbx_conv, sbx_contact, 2, 'por_confirmar', 'contra_entrega', 'PEN', 60, 60, true,
                '{"nombres": "Ana Pérez Soto"}') returning id into o2;
      insert into order_items (tenant_id, order_id, nombre, precio_unitario, cantidad, subtotal)
        values (t, o2, 'Polo básico', 30, 2, 60);

      insert into orders (tenant_id, conversation_id, contact_id, numero, estado, modalidad_pago, moneda,
                          subtotal, total, is_sandbox, datos_cliente)
        values (t, sbx_conv, sbx_contact, 3, 'por_confirmar', 'previo', 'PEN', 120, 120, true,
                '{"nombres": "Ana Pérez Soto"}') returning id into o3;
      insert into order_items (tenant_id, order_id, nombre, precio_unitario, cantidad, subtotal)
        values (t, o3, 'Gorra', 120, 1, 120);
      insert into payment_proofs (tenant_id, order_id, conversation_id, storage_key, monto, moneda,
                                  codigo_operacion, entidad, confianza)
        values (t, o3, sbx_conv, 'e2e/none2.jpg', 120, 'PEN', 'OP-1003', 'Plin', 0.95);

      insert into orders (tenant_id, conversation_id, contact_id, numero, estado, modalidad_pago, moneda,
                          subtotal, total, is_sandbox, tipo_entrega, zona_envio, datos_cliente)
        values (t, wa_conv, wa_contact, 4, 'por_confirmar', 'contra_entrega', 'PEN', 45, 45, false,
                'delivery', 'Miraflores',
                '{"nombres": "Luis Rojas", "telefono": "+51912345001", "direccion": "Av. Lima 123"}') returning id into o4;
      insert into order_items (tenant_id, order_id, nombre, precio_unitario, cantidad, subtotal)
        values (t, o4, 'Mochila', 45, 1, 45);
    end $$;
  `);
}

/** A WhatsApp order whose conversation the assistant handed to a person (TASK-443). */
async function seedHandedOver(email: string): Promise<string> {
  // `psql -At` prints the command tag ("INSERT 0 1") after the returned id.
  const out = await psql(`
    with t as (
      select tu.tenant_id as id from tenant_users tu join users u on u.id = tu.user_id
       where u.email = '${email}'
    ), c as (
      insert into contacts (tenant_id, phone_e164, profile_name, custom_data)
        select id, '+51912345777', 'Rosa Díaz', '{"nombres": "Rosa Díaz"}' from t returning id, tenant_id
    ), conv as (
      insert into conversations (tenant_id, channel, contact_id)
        select tenant_id, 'whatsapp', id from c returning id, tenant_id, contact_id
    ), st as (
      insert into conversation_state (conversation_id, tenant_id, template_key, template_version,
                                      stage_key, funnel_code, facts)
        select id, tenant_id, 'productos', 2, 'DERIVADO', 'DERIVADO',
               jsonb_build_object('saludo_enviado', true, 'derivado', jsonb_build_object(
                 'motivo', 'queja', 'resumen', 'Su pedido llegó incompleto',
                 'hasta', to_char((now() + interval '3 hours') at time zone 'UTC',
                                  'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')))
          from conv returning conversation_id
    )
    insert into orders (tenant_id, conversation_id, contact_id, numero, estado, modalidad_pago, moneda,
                        subtotal, total, is_sandbox, datos_cliente)
      select conv.tenant_id, conv.id, conv.contact_id, 9, 'por_confirmar', 'contra_entrega', 'PEN',
             45, 45, false, '{"nombres": "Rosa Díaz"}'
        from conv, st returning id;
  `);
  return out.split("\n")[0].trim();
}

test.describe("Pedidos", () => {
  test("la lista filtra, busca y cuenta lo que espera revisión", async ({ page }) => {
    const email = await signedIn(page);
    await seedOrders(email);

    await page.goto("/pedidos");
    await expect(page.getByRole("heading", { name: "Pedidos", level: 1 })).toBeVisible();

    // The simulator's orders are not sales: hidden until asked for.
    // The table from `md` up, cards on a phone: the role query only sees the one drawn.
    const table = page;
    await expect(table.getByRole("link", { name: "#4" })).toBeVisible();
    await expect(table.getByRole("link", { name: "#1" })).toHaveCount(0);

    await page.getByLabel("Incluir pedidos de prueba").check();
    await expect(table.getByRole("link", { name: "#1" })).toBeVisible();
    await expect(table.getByRole("link", { name: "#2" })).toBeVisible();
    await expect(table.getByRole("link", { name: "#3" })).toBeVisible();

    // A receipt that arrived, with what looks off about it, is visible in the row.
    const row1 = page
      .locator("tbody tr, ul li")
      .filter({ hasText: "#1" })
      .filter({ visible: true });
    await expect(row1).toContainText("Con comprobante");
    await expect(row1).toContainText("El monto no coincide");
    await expect(row1).toContainText("Ana P.");
    await expect(row1).toContainText("+51 9•• ••• 628");
    await expect(row1).toContainText("S/ 500.00");

    // Search by the customer's name.
    await page.getByLabel("Buscar").fill("Luis");
    await expect(table.getByRole("link", { name: "#4" })).toBeVisible();
    await expect(table.getByRole("link", { name: "#1" })).toHaveCount(0);
    await page.getByLabel("Buscar").fill("Nadie");
    await expect(page.getByText("Ningún pedido coincide con esos filtros")).toBeVisible();
    await page.getByRole("button", { name: "Quitar filtros" }).click();
    await expect(table.getByRole("link", { name: "#4" })).toBeVisible();
    await expect(table.getByRole("link", { name: "#1" })).toHaveCount(0);

    // A state chip narrows the list; no simulator order is `confirmado`.
    await page.getByRole("button", { name: /^Confirmado/ }).click();
    await expect(page.getByText("Ningún pedido coincide con esos filtros")).toBeVisible();
    await page.getByRole("button", { name: "Quitar filtros" }).click();
    await expect(table.getByRole("link", { name: "#4" })).toBeVisible();

    // The summary counts what waits for a person (the real order, not the simulator's).
    await expect(page.getByTestId("stat-pendientes")).toHaveText("1");
    await expect(page.getByTestId("stat-conversiones")).toHaveText("0");
  });

  test("confirmar un pedido del simulador cambia su estado y muestra aviso enviado", async ({
    page,
  }) => {
    const email = await signedIn(page);
    await seedOrders(email);

    await page.goto("/pedidos");
    await page.getByLabel("Incluir pedidos de prueba").check();
    await page.getByRole("link", { name: "#1" }).click();
    await expect(page.getByRole("heading", { name: "Pedido #1" })).toBeVisible();
    await expect(page.getByTestId("order-state")).toHaveText("Por confirmar");

    // What the operator needs in order to decide.
    await expect(page.getByText("Paquete 5 certificaciones")).toBeVisible();
    await expect(page.getByTestId("order-total")).toHaveText("S/ 500.00");
    const observations = page.getByTestId("proof-observations");
    await expect(observations).toContainText("El monto no coincide");
    await expect(observations).toContainText("El pedido es de S/ 500.00");
    const proof = page.getByTestId("proof");
    await expect(proof).toContainText("S/ 450.00");
    await expect(proof).toContainText("OP-1001");
    await expect(proof).toContainText("Yape");
    await expect(proof).toContainText("82 %");
    await expect(page.getByText("Pedido del modo prueba")).toBeVisible();

    await page.getByRole("button", { name: "Confirmar", exact: true }).click();
    await page.getByLabel("Siguiente paso para el cliente").fill("Te enviamos tus certificados en 48 h");
    await page.getByRole("button", { name: "Confirmar pedido" }).click();

    await expect(page.getByTestId("order-state")).toHaveText("Confirmado");
    await expect(page.getByTestId("order-outcome")).toContainText("Aviso enviado al cliente");
    await expect(page.getByText("Te enviamos tus certificados en 48 h").first()).toBeVisible();
    await expect(page.getByText("Pedido confirmado", { exact: true })).toBeVisible();

    // And it can be moved along, but not confirmed twice.
    await expect(page.getByRole("button", { name: "Confirmar", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Marcar en curso" }).click();
    await expect(page.getByTestId("order-state")).toHaveText("En curso");
    await page.getByRole("button", { name: "Marcar completado" }).click();
    await expect(page.getByTestId("order-state")).toHaveText("Completado");
    await expect(page.getByRole("button", { name: "Marcar en curso" })).toHaveCount(0);
  });

  test("rechazar pide el motivo y devuelve el pedido a borrador", async ({ page }) => {
    const email = await signedIn(page);
    await seedOrders(email);

    await page.goto("/pedidos");
    await page.getByLabel("Incluir pedidos de prueba").check();
    await page.getByRole("link", { name: "#3" }).click();
    await expect(page.getByRole("heading", { name: "Pedido #3" })).toBeVisible();

    await page.getByRole("button", { name: "Rechazar", exact: true }).click();
    await page.getByLabel("Motivo del rechazo").fill("Mal");
    await page.getByRole("button", { name: "Rechazar pedido" }).click();
    await expect(page.getByText(/al menos 5 caracteres/)).toBeVisible();
    await expect(page.getByTestId("order-state")).toHaveText("Por confirmar");

    await page.getByLabel("Motivo del rechazo").fill("El monto no coincide con el total");
    await page.getByRole("button", { name: "Rechazar pedido" }).click();
    await expect(page.getByTestId("order-state")).toHaveText("Borrador");
    await expect(page.getByTestId("order-outcome")).toContainText("Pedido rechazado.");
    await expect(page.getByTestId("order-outcome")).toContainText("Aviso enviado al cliente");
    await expect(page.getByText("Fue rechazado")).toBeVisible();
  });

  test("con la ventana de 24 h cerrada avisa y no envía nada por WhatsApp", async ({ page }) => {
    const email = await signedIn(page);
    await seedOrders(email);

    await page.goto("/pedidos");
    await page.getByRole("link", { name: "#4" }).click();
    await expect(page.getByRole("heading", { name: "Pedido #4" })).toBeVisible();

    await expect(page.getByText("La ventana de 24 h con este cliente está cerrada")).toBeVisible();
    await expect(page.getByText("Av. Lima 123")).toBeVisible();
    await expect(page.getByText("Delivery · Miraflores")).toBeVisible();
    await expect(page.getByText("no lleva comprobante")).toBeVisible();

    await page.getByRole("button", { name: "Confirmar", exact: true }).click();
    await page.getByRole("button", { name: "Confirmar pedido" }).click();

    await expect(page.getByTestId("order-state")).toHaveText("Confirmado");
    await expect(page.getByTestId("order-outcome")).toContainText(
      "La ventana de 24 h está cerrada: el aviso no se envió por WhatsApp",
    );
  });

  test("un pedido derivado muestra la pausa y se reactiva el asistente", async ({ page }) => {
    const email = await signedIn(page);
    const orderId = await seedHandedOver(email);

    await page.goto(`/pedidos/${orderId}`);
    const banner = page.getByTestId("handover");
    await expect(banner).toContainText("Queja: Su pedido llegó incompleto");
    await expect(page.getByText(/El asistente está pausado hasta/)).toBeVisible();

    await page.getByRole("button", { name: "Reactivar asistente" }).click();
    await expect(banner).toHaveCount(0);
  });
});
