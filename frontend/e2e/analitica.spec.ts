// See `./fixtures` for why the rate limiter is reset rather than raised.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Analytics and NPS (Feature 013): the funnel, products and satisfaction in Resultados,
 * turning the survey on, and the NPS card on Home. The sale and the survey answer come from
 * WhatsApp, so they are inserted with `psql`; the flows themselves are the backend suite's.
 */

const run = promisify(execFile);
const POSTGRES_CONTAINER = process.env.E2E_POSTGRES_CONTAINER ?? "leo-dev-postgres-1";
const shots = process.env.E2E_SHOTS;

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

/** One sale of "Torta de chocolate", a customer asking for alfajores, and a 5-star answer. */
async function seed(email: string): Promise<void> {
  const suffix = Date.now().toString().slice(-6);
  await psql(`
    do $$
    declare t uuid; c1 uuid; c2 uuid; v1 uuid; v2 uuid; o uuid; won uuid; fresh uuid;
    begin
      select tu.tenant_id into strict t from tenant_users tu
        join users u on u.id = tu.user_id where u.email = '${email}';
      perform 1;
      insert into pipeline_stages (tenant_id, key, label, position, semantic) values
        (t, 'nuevo', 'Nuevo', 10, 'nuevo'), (t, 'interesado', 'Interesado', 20, 'interesado'),
        (t, 'por_pagar', 'Por pagar', 30, 'por_pagar'), (t, 'ganado', 'Ganado', 40, 'ganado'),
        (t, 'perdido', 'Perdido', 50, 'perdido') on conflict do nothing;
      select id into won from pipeline_stages where tenant_id = t and key = 'ganado';
      select id into fresh from pipeline_stages where tenant_id = t and key = 'nuevo';
      insert into contacts (tenant_id, phone_e164, profile_name, last_inbound_at)
        values (t, '+5194${suffix}1', 'Rosa', now()) returning id into c1;
      insert into contacts (tenant_id, phone_e164, profile_name, last_inbound_at)
        values (t, '+5194${suffix}2', 'Pedro', now()) returning id into c2;
      insert into conversations (tenant_id, channel, contact_id) values (t, 'whatsapp', c1) returning id into v1;
      insert into conversations (tenant_id, channel, contact_id) values (t, 'whatsapp', c2) returning id into v2;
      insert into leads (tenant_id, contact_id, conversation_id, stage_id, status, product_interest, closed_at)
        values (t, c1, v1, won, 'won', 'Torta de chocolate', now());
      insert into leads (tenant_id, contact_id, conversation_id, stage_id, product_interest)
        values (t, c2, v2, fresh, 'Alfajores');
      insert into orders (tenant_id, numero, conversation_id, contact_id, moneda, subtotal, total, estado, confirmed_at)
        values (t, 1, v1, c1, 'PEN', 80, 80, 'confirmado', now()) returning id into o;
      insert into order_items (tenant_id, order_id, nombre, precio_unitario, cantidad, subtotal)
        values (t, o, 'Torta de chocolate', 80, 1, 80);
      insert into csat_surveys (tenant_id, conversation_id, contact_id, trigger, status, score, comment, answered_at)
        values (t, v1, c1, 'ganado', 'answered', 5, 'Muy rica la torta', now());
    end $$;
  `);
}

test.describe("Analítica y NPS", () => {
  test("embudo, productos, satisfacción, la encuesta y el NPS", async ({ page }) => {
    const email = await owner(page);
    const project = test.info().project.name;
    await seed(email);

    await page.goto("/resultados");
    await page.getByRole("button", { name: "Embudo" }).click();
    const funnel = page.getByTestId("funnel");
    await expect(funnel.getByText("Compraron", { exact: true })).toBeVisible();
    await expect(funnel).toContainText("Conversión: 50 %");
    await expect(funnel.getByText("Directo", { exact: true })).toBeVisible();
    if (shots) await page.screenshot({ path: `${shots}/analitica-embudo-${project}.png`, fullPage: true });

    await page.getByRole("button", { name: "Productos" }).click();
    const products = page.getByTestId("products");
    await expect(products.getByText("Torta de chocolate").first()).toBeVisible();
    await expect(products).toContainText("S/ 80.00");
    await expect(products.getByText("Alfajores")).toBeVisible();

    await page.getByRole("button", { name: "Satisfacción" }).click();
    await expect(page.getByTestId("csat-average")).toHaveText("5 / 5");
    await expect(page.getByText("Muy rica la torta")).toBeVisible();
    const survey = page.getByLabel(/Preguntar a mis clientes qué tal los atendimos/);
    await expect(survey).not.toBeChecked();
    await survey.check();
    await expect(page.getByText("Guardado.")).toBeVisible();
    if (shots) await page.screenshot({ path: `${shots}/analitica-satisfaccion-${project}.png`, fullPage: true });
    await page.reload();
    await page.getByRole("button", { name: "Satisfacción" }).click();
    await expect(page.getByLabel(/Preguntar a mis clientes qué tal los atendimos/)).toBeChecked();

    // Forty days after signing up, the owner is asked once about Leo.
    await psql(`update tenants set created_at = now() - interval '40 days'
      where id = (select tu.tenant_id from tenant_users tu join users u on u.id = tu.user_id where u.email = '${email}')`);
    await page.goto("/");
    const card = page.getByRole("region", { name: "Tu opinión sobre Leo" });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "9", exact: true }).click();
    await card.getByLabel("¿Por qué?").fill("Me ahorra tiempo");
    if (shots) await page.screenshot({ path: `${shots}/analitica-nps-${project}.png`, fullPage: true });
    await card.getByRole("button", { name: "Enviar" }).click();
    await expect(page.getByText("¡Gracias! Lo leemos todo.")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: "Tu opinión sobre Leo" })).toHaveCount(0);
  });
});
