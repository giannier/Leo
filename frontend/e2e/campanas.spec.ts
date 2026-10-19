// See `./fixtures` for why the rate limiter is reset rather than raised.
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import { promisify } from "node:util";

import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Campaigns (Feature 011, TASK-1108): templates from the library and by hand, contacts
 * imported with their declaration and tagged, a campaign to a tag with its estimate, and the
 * settings. Meta's approval and the connected number are inserted with `psql` (they come from
 * YCloud); the sending itself is the backend suite's (`test_campaigns.py`).
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

/** The connected number's credentials, encrypted with the API's key (when the run has it). */
async function sealedCredentials(): Promise<string | null> {
  const keyFile = process.env.E2E_FERNET_KEY_FILE;
  if (!keyFile) return null;
  const key = readFileSync(keyFile, "utf8").trim();
  const { stdout } = await run(process.env.E2E_PYTHON ?? "../.venv/bin/python", [
    "-c",
    "import sys; from cryptography.fernet import Fernet; " +
      "print(Fernet(sys.argv[1].encode()).encrypt(sys.argv[2].encode()).decode())",
    key,
    JSON.stringify({ waba_id: "e2e-waba", phone_number_id: "112233445566", api_key: "yc_live_e2e_key" }),
  ]);
  return stdout.trim();
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

function tenantOf(email: string): string {
  return `(select tu.tenant_id from tenant_users tu join users u on u.id = tu.user_id where u.email = '${email}')`;
}

test.describe("Campañas", () => {
  test("plantillas, contactos con permiso, una campaña a una etiqueta y los ajustes", async ({ page }) => {
    const email = await owner(page);
    const project = test.info().project.name;

    // No approved template yet: the builder says what to do first.
    await page.goto("/campanas");
    await expect(page.getByRole("heading", { name: "Campañas", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "Nueva campaña" }).click();
    await expect(page.getByText("Primero necesitas una plantilla aprobada")).toBeVisible();
    await page.getByRole("button", { name: "Volver" }).click();

    // Templates: from the library, and one by hand with a variable chip.
    await page.getByRole("button", { name: "Plantillas" }).click();
    const library = page.getByTestId("template-library");
    await library.getByRole("listitem").filter({ hasText: "Aviso de promoción" }).getByRole("button", { name: "Usar" }).click();
    await expect(page.getByText("«Aviso de promoción» se agregó a tus plantillas.")).toBeVisible();
    const list = page.getByTestId("template-list");
    await expect(list.getByText("Aviso de promoción")).toBeVisible();
    await expect(list.getByText("Borrador")).toBeVisible();

    await page.getByRole("button", { name: "Nueva plantilla" }).click();
    await page.getByLabel("Nombre").fill("Torta del mes");
    await page.getByLabel("Mensaje").fill("{{nombre}} tenemos torta.");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByText(/no puede empezar ni terminar con una variable/)).toBeVisible();
    await page.getByLabel("Mensaje").fill("Hola , esta semana la torta de chocolate tiene 20% de descuento.");
    await page.getByLabel("Mensaje").evaluate((el: HTMLTextAreaElement) => el.setSelectionRange(5, 5));
    await page.getByRole("button", { name: "+ {{nombre}}" }).click();
    await expect(page.getByTestId("template-preview")).toContainText("Hola Ana, esta semana");
    if (shots) await page.screenshot({ path: `${shots}/campanas-plantilla-${project}.png`, fullPage: true });
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByText("Plantilla guardada.")).toBeVisible();
    await expect(list.getByText("Torta del mes")).toBeVisible();

    // Contacts: an import with its declaration, then a tag by hand.
    await page.getByRole("button", { name: "Contactos" }).click();
    await expect(page.getByTestId("optin-count")).toHaveText("0");
    const suffix = Date.now().toString().slice(-6);
    await page.locator('input[type="file"]').setInputFiles({
      name: "clientes.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(`nombre;telefono\nRosa Flores;96${suffix}1\nPedro;96${suffix}2\n`),
    });
    await page.getByLabel("Etiquetas para estos contactos (opcional)").fill("feria");
    // Without the declaration the button stays off.
    await expect(page.getByRole("button", { name: "Importar" })).toBeDisabled();
    await page.getByLabel(/Declaro que estas personas aceptaron/).check();
    await page.getByRole("button", { name: "Importar" }).click();
    await expect(page.getByText("2 contacto(s) importados")).toBeVisible();
    await expect(page.getByTestId("optin-count")).toHaveText("2");
    const rosa = page.getByTestId("contact-list").getByRole("listitem").filter({ hasText: "Rosa Flores" });
    await expect(rosa.getByText("Acepta promociones · importado")).toBeVisible();
    await rosa.getByLabel("Nueva etiqueta").fill("VIP");
    await rosa.getByLabel("Nueva etiqueta").press("Enter");
    await expect(rosa.getByText("vip")).toBeVisible();
    await page.getByLabel("Etiqueta", { exact: true }).selectOption("vip");
    await expect(page.getByTestId("contact-list").getByRole("listitem")).toHaveCount(1);
    if (shots) await page.screenshot({ path: `${shots}/campanas-contactos-${project}.png`, fullPage: true });

    // Meta approves the library template (a webhook in real life).
    await psql(`update message_templates set status = 'approved' where tenant_id = ${tenantOf(email)} and label = 'Aviso de promoción'`);

    // A campaign to the VIP tag: one person, an estimate in dollars, a draft.
    await page.getByRole("button", { name: "Campañas", exact: true }).click();
    await page.getByRole("button", { name: "Nueva campaña" }).click();
    await page.getByLabel("Nombre de la campaña").fill("VIP de octubre");
    const estimate = page.getByTestId("campaign-estimate");
    await expect(estimate).toContainText("2");
    await page.locator("label").filter({ hasText: /^vip$/ }).click();
    await expect(estimate).toContainText("1");
    await expect(estimate).toContainText("US$ 0.08");
    if (shots) await page.screenshot({ path: `${shots}/campanas-nueva-${project}.png`, fullPage: true });

    const sealed = await sealedCredentials();
    if (!sealed) {
      await page.getByRole("button", { name: "Guardar borrador" }).click();
      await expect(page.getByTestId("campaign-list").getByText("VIP de octubre")).toBeVisible();
    } else {
      await psql(`
        insert into whatsapp_numbers (tenant_id, phone_e164, display_name, provider, status, credentials_enc)
        values (${tenantOf(email)}, '+5190${suffix}9', 'E2E', 'ycloud', 'connected', convert_to('${sealed}', 'UTF8'))
      `);
      await page.getByRole("button", { name: "Enviar" }).click();
      const dialog = page.getByRole("dialog", { name: "Confirmar envío" });
      await expect(dialog).toContainText("Se enviará a 1 personas");
      await dialog.getByRole("button", { name: "Confirmar" }).click();
      await expect(page.getByText("«VIP de octubre» está programada.")).toBeVisible();
      const card = page.getByTestId("campaign-list").getByRole("listitem").filter({ hasText: "VIP de octubre" });
      await expect(card.getByText("Programada", { exact: true })).toBeVisible();
      await card.getByRole("button", { name: "Pausar" }).click();
      await expect(card.getByText("En pausa", { exact: true })).toBeVisible();
      await expect(card.getByText("La pausaste tú")).toBeVisible();
      if (shots) await page.screenshot({ path: `${shots}/campanas-lista-${project}.png`, fullPage: true });
      await card.getByRole("button", { name: "Reanudar" }).click();
      await expect(card.getByText("Programada", { exact: true })).toBeVisible();
      page.once("dialog", (prompt) => void prompt.accept());
      await card.getByRole("button", { name: "Cancelar" }).click();
      await expect(card.getByText("Cancelada", { exact: true })).toBeVisible();
    }

    // Settings: stop asking for permission after a sale.
    await page.getByRole("button", { name: "Ajustes" }).click();
    const ask = page.getByLabel("Preguntar después de una venta confirmada");
    await expect(ask).toBeChecked();
    await ask.uncheck();
    await page.getByRole("button", { name: "Guardar ajustes" }).click();
    await expect(page.getByText("Guardado.")).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("Preguntar después de una venta confirmada")).not.toBeChecked();
  });
});
