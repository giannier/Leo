// See `./fixtures` for why the rate limiter is reset rather than raised.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { Page } from "@playwright/test";

import { expect, psqlCommand, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * CRM (Feature 012, TASK-1205): the board, moving a lead, the day's list, a note, assigning,
 * losing with a reason, opening the conversation, and adding a stage.
 *
 * Leads come from real WhatsApp conversations moving through the sales process, which needs a
 * provider webhook (Feature 002); they are inserted with `psql` here, as the inbox test does.
 * How the process moves them is the backend suite's (`test_crm_leads.py`).
 */

const run = promisify(execFile);

async function psql(sql: string): Promise<string> {
  const { stdout } = await run(
    ...psqlCommand([
      "-v",
      "ON_ERROR_STOP=1",
      "-At",
      "-c",
      sql,
    ]),
  );
  return stdout.trim().split("\n")[0] ?? "";
}

async function owner(page: Page): Promise<string> {
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

/** Two leads: Carla, interested with an order of S/ 450; Diego, new and idle for five days. */
async function seed(ownerEmail: string): Promise<void> {
  const suffix = Date.now().toString().slice(-6);
  await psql(`
    do $$
    declare t uuid; c1 uuid; c2 uuid; v1 uuid; v2 uuid;
    begin
      select tu.tenant_id into strict t from tenant_users tu
        join users u on u.id = tu.user_id where u.email = '${ownerEmail}';
      insert into contacts (tenant_id, phone_e164, profile_name, last_inbound_at)
        values (t, '+5195${suffix}1', 'Carla Cliente', now()) returning id into c1;
      insert into contacts (tenant_id, phone_e164, profile_name, last_inbound_at)
        values (t, '+5195${suffix}2', 'Diego Dudoso', now()) returning id into c2;
      insert into conversations (tenant_id, channel, contact_id, last_message_at)
        values (t, 'whatsapp', c1, now()) returning id into v1;
      insert into conversations (tenant_id, channel, contact_id, last_message_at)
        values (t, 'whatsapp', c2, now()) returning id into v2;
      insert into messages (tenant_id, conversation_id, direction, author, content, provider_message_id)
        values (t, v1, 'inbound', 'customer', 'Quiero el paquete de 5', 'wamid.crm' || '${suffix}');
      insert into leads (tenant_id, contact_id, conversation_id, stage_id, amount_estimated, moneda, product_interest)
        select t, c1, v1, id, 450.00, 'PEN', 'Paquete 5 certificaciones'
        from pipeline_stages where tenant_id = t and key = 'interesado';
      insert into leads (tenant_id, contact_id, conversation_id, stage_id, last_activity_at, stage_entered_at)
        select t, c2, v2, id, now() - interval '5 days', now() - interval '5 days'
        from pipeline_stages where tenant_id = t and key = 'nuevo';
    end $$;
  `);
}

test.describe("CRM", () => {
  test("tablero, mover, hoy, nota, asignar, perder con motivo y una etapa nueva", async ({ page }) => {
    const email = await owner(page);
    await page.goto("/crm");
    await expect(page.getByRole("heading", { name: "CRM" })).toBeVisible();
    const board = page.getByTestId("crm-board");
    await expect(board.getByRole("region")).toHaveCount(5);
    await seed(email);
    await page.reload();

    const interested = page.getByTestId("column-interesado");
    await expect(interested.getByText("Carla Cliente")).toBeVisible();
    await expect(interested.getByText("S/ 450.00")).toBeVisible();
    await expect(page.getByTestId("crm-tiles")).toContainText("S/ 450.00");

    // Move with "Mover a" (works on every device).
    await interested.getByLabel("Mover Carla Cliente a").selectOption({ label: "Mover a Por pagar" });
    await expect(page.getByTestId("column-por_pagar").getByText("Carla Cliente")).toBeVisible();

    // The day's list: Diego has been idle for five days.
    await page.getByRole("tab", { name: /Hoy/ }).click();
    const today = page.getByTestId("crm-today");
    await expect(today.getByText("Diego Dudoso")).toBeVisible();
    await expect(today.getByText(/hace 5 días/)).toBeVisible();
    await page.getByRole("tab", { name: "Tablero" }).click();

    // The lead's panel: a note, assign to me, the history.
    await page.getByTestId("column-por_pagar").getByText("Carla Cliente").click();
    const drawer = page.getByRole("dialog", { name: "Lead" });
    await drawer.getByLabel("Nueva nota").fill("Llamar el lunes");
    await drawer.getByRole("button", { name: "Anotar" }).click();
    await expect(drawer.getByTestId("lead-notes")).toContainText("Llamar el lunes");
    const me = await drawer.getByLabel("Asignado a").locator("option").nth(1).textContent();
    await drawer.getByLabel("Asignado a").selectOption({ index: 1 });
    await expect(drawer.getByLabel("Asignado a")).not.toHaveValue("");
    await expect(drawer.getByTestId("lead-history")).toContainText("Por pagar");

    // Opening the conversation lands on it in the inbox.
    await drawer.getByRole("link", { name: "Abrir conversación" }).click();
    await expect(page).toHaveURL(/\/bandeja\?c=/);
    await expect(page.getByTestId("thread")).toContainText("Quiero el paquete de 5");

    // Lose Diego with a reason.
    await page.goto("/crm");
    await page
      .getByTestId("column-nuevo")
      .getByLabel("Mover Diego Dudoso a")
      .selectOption({ label: "Mover a Perdido" });
    const reasons = page.getByRole("dialog", { name: "Motivo de pérdida" });
    await reasons.getByLabel(/Por qué se perdió/).selectOption({ label: "Precio" });
    await reasons.getByRole("button", { name: "Marcar perdido" }).click();
    await expect(page.getByTestId("column-perdido").getByText("Diego Dudoso")).toBeVisible();

    // Assigned to me: the filter finds it.
    await page.getByLabel("Asignados").selectOption("me");
    await expect(page.getByTestId("column-por_pagar").getByText("Carla Cliente")).toBeVisible();
    expect(me).toBeTruthy();
    await page.getByLabel("Asignados").selectOption("");

    // A stage of the business's own, after "Interesado".
    await page.getByRole("button", { name: "Configurar embudo" }).click();
    const rows = page.getByTestId("stage-rows");
    await rows.locator("li").nth(1).getByRole("button", { name: "+ después" }).click();
    await page.getByLabel("Nombre de la etapa 3").fill("Cotización enviada");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByTestId("column-cotizacion_enviada")).toBeVisible();
    // On a computer a card can also be dragged (on a phone, "Mover a" is the way).
    if (test.info().project.name === "chromium") {
      // Bring both columns into view first: if the board scrolls mid-drag, the drag starts on another card.
      await page.getByTestId("column-ganado").scrollIntoViewIfNeeded();
      await page
        .getByTestId("column-por_pagar")
        .getByTestId("lead-card")
        .filter({ hasText: "Carla Cliente" })
        .dragTo(page.getByTestId("column-ganado"), { sourcePosition: { x: 24, y: 14 } });
      await expect(page.getByTestId("column-ganado").getByText("Carla Cliente")).toBeVisible();
    }
    if (process.env.E2E_SHOTS) {
      await page.screenshot({ path: `${process.env.E2E_SHOTS}/crm-${test.info().project.name}.png`, fullPage: true });
    }
  });
});
