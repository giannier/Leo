// See `./fixtures` for why the rate limiter is reset rather than raised.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { Browser, Page } from "@playwright/test";

import { expect, psqlCommand, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Bandeja (Feature 007, TASK-708): two people of the same business on one
 * conversation, each in their own browser.
 *
 * What is set up from outside, said plainly:
 *
 * - The second person (an operator) and the customer's conversation are inserted
 *   with `psql`. Inviting through email is TASK-023's spec; a real customer message
 *   needs a provider webhook, which is Feature 002's. The operator reuses the
 *   owner's password hash, so no hashing happens in the test.
 * - The business has no connected number, so a message written by a person is
 *   stored and shown but not sent ("sin_numero"); sending is the outbox's business.
 *
 * What is under test is the real panel, the real API and the real live channel
 * (WebSocket over Redis): presence, the takeover lock seen by the other person
 * without reloading, the composer closed for whoever does not hold it, the human
 * message arriving live, and the return to the assistant.
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
  // `-At` prints the command tag ("INSERT 0 1") after a returned value.
  return stdout.trim().split("\n")[0] ?? "";
}

async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Correo electrónico").fill(email);
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL("/", { timeout: 15_000 });
}

async function owner(page: Page): Promise<{ email: string; password: string; name: string }> {
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
  await login(page, user.email, user.password);
  return { email: user.email, password: user.password, name: user.fullName };
}

/** An operator of the owner's business, and a WhatsApp conversation waiting. */
async function seed(ownerEmail: string): Promise<{ operatorEmail: string }> {
  const operatorEmail = `op-${Date.now()}@example.com`;
  await psql(`
    do $$
    declare t uuid; o uuid; c uuid; v uuid;
    begin
      select tu.tenant_id into strict t from tenant_users tu
        join users u on u.id = tu.user_id where u.email = '${ownerEmail}';
      insert into users (email, password_hash, full_name, email_verified_at)
        select '${operatorEmail}', password_hash, 'Olga Operadora', now()
        from users where email = '${ownerEmail}' returning id into o;
      insert into tenant_users (tenant_id, user_id, role) values (t, o, 'operator');

      insert into contacts (tenant_id, phone_e164, profile_name, last_inbound_at)
        values (t, '+51955500708', 'Carla Cliente', now()) returning id into c;
      insert into conversations (tenant_id, channel, contact_id, last_message_at, unread_count)
        values (t, 'whatsapp', c, now(), 1) returning id into v;
      insert into messages (tenant_id, conversation_id, direction, author, content, provider_message_id)
        values (t, v, 'inbound', 'customer', 'Hola, ¿tienen la mochila en azul?', 'wamid.e2e708' || extract(epoch from now()));
    end $$;
  `);
  return { operatorEmail };
}

async function secondBrowser(browser: Browser): Promise<Page> {
  const context = await browser.newContext();
  return context.newPage();
}

test.describe("Bandeja con dos personas", () => {
  test("presencia, toma exclusiva en vivo, mensaje humano y devolución", async ({ page, browser }) => {
    const me = await owner(page);
    const { operatorEmail } = await seed(me.email);

    // The owner opens the inbox from the menu and the conversation.
    await page.getByRole("link", { name: "Bandeja" }).first().click();
    await expect(page).toHaveURL(/\/bandeja/);
    await expect(page.getByTestId("live-status")).toHaveText("En vivo");
    const list = page.getByTestId("inbox-list");
    await expect(list.getByText("Carla Cliente")).toBeVisible();
    await list.getByText("Carla Cliente").click();
    await expect(page.getByTestId("thread")).toContainText("¿tienen la mochila en azul?");
    await expect(page.getByTestId("thread-holder")).toHaveText(/La atiende el asistente/);

    // The operator, in another browser, opens the same conversation.
    const other = await secondBrowser(browser);
    await login(other, operatorEmail, me.password);
    await other.goto("/bandeja");
    await expect(other.getByTestId("live-status")).toHaveText("En vivo");
    // An operator answers; the settings are for whoever manages the business.
    await expect(other.getByRole("button", { name: "Ajustes" })).toHaveCount(0);
    await other.getByTestId("inbox-list").getByText("Carla Cliente").click();
    await expect(other.getByTestId("thread")).toContainText("¿tienen la mochila en azul?");

    // Each one sees that the other is looking.
    await expect(page.getByTestId("thread-holder")).toContainText("También la está viendo: Olga Operadora", {
      timeout: 20_000,
    });

    // The owner takes it; the operator sees it at once, without reloading, and
    // cannot write. An operator cannot take it from someone else.
    await page.getByRole("button", { name: "Tomar conversación" }).click();
    await expect(page.getByTestId("thread-holder")).toContainText("La tienes tú");
    await expect(other.getByTestId("thread-holder")).toContainText(`La tiene ${me.name}`);
    await expect(other.getByRole("button", { name: "Tomar conversación" })).toHaveCount(0);
    await expect(other.getByRole("button", { name: "Tomarla yo" })).toHaveCount(0);
    await expect(other.getByLabel("Mensaje")).toBeDisabled();
    await expect(page.getByTestId("thread")).toContainText(`${me.name} tomó la conversación`);

    // The owner answers; the operator sees the message arrive live.
    await page.getByLabel("Mensaje").fill("Sí, la tenemos en azul. ¿Te la separo?");
    await page.getByRole("button", { name: "Enviar" }).click();
    await expect(page.getByTestId("thread")).toContainText("¿Te la separo?");
    await expect(other.getByTestId("thread")).toContainText("¿Te la separo?");

    // Back to the assistant: both panels show it, and the operator may write again.
    await page.getByRole("button", { name: "Devolver al asistente" }).click();
    await expect(page.getByTestId("thread-holder")).toContainText("La atiende el asistente");
    await expect(other.getByTestId("thread-holder")).toContainText("La atiende el asistente");
    await expect(other.getByLabel(/Mensaje/)).toBeEnabled();

    // Internal notes are for the team.
    await other.getByText(/Notas internas/).click();
    await other.getByLabel("Nueva nota").fill("Clienta frecuente, prefiere envío.");
    await other.getByRole("button", { name: "Guardar nota" }).click();
    await expect(other.getByText("Clienta frecuente, prefiere envío.")).toBeVisible();

    await other.context().close();
  });
});

test.describe("Ajustes de la bandeja", () => {
  test("quien administra fija la auto-devolución y el aviso de cola", async ({ page }) => {
    await owner(page);
    await page.goto("/bandeja");
    await page.getByRole("button", { name: "Ajustes" }).click();
    const form = page.getByRole("form", { name: "Ajustes de la bandeja" });
    await expect(form.getByLabel(/vuelve al asistente/)).toHaveValue("30");
    await form.getByLabel(/vuelve al asistente/).selectOption("never");
    await form.getByLabel(/avisar por correo/).selectOption("5");
    await form.getByRole("button", { name: "Guardar ajustes" }).click();
    await expect(form.getByText("Ajustes guardados.")).toBeVisible();

    await page.reload();
    await page.getByRole("button", { name: "Ajustes" }).click();
    await expect(page.getByLabel(/vuelve al asistente/)).toHaveValue("never");
    await expect(page.getByLabel(/avisar por correo/)).toHaveValue("5");
  });
});
