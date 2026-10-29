/**
 * Asistente by layers (Feature 017, Fase C of the 004): the screens a business
 * uses to configure its assistant, end to end against the real stack.
 *
 * - TASK-438: what is saved in "Información del negocio" reaches the prompt.
 * - TASK-440: a rule switched off disappears from the prompt preview.
 * - TASK-467: a situation is created as a draft, tried in the simulator and
 *   activated.
 * - TASK-441: an image is uploaded to the library.
 * - TASK-468: the wizard offers the rubro's situations and activates one.
 */

import { expect, test } from "./fixtures";
import { readyToTest } from "./ready";

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test.describe("Asistente por capas", () => {
  test.beforeEach(async ({ page }) => {
    await readyToTest(page);
  });

  test("el menú reúne todo el asistente y el objetivo y la derivación se editan aquí", async ({
    page,
  }) => {
    // Feature 018, HU-04 and HU-05.
    await page.goto("/asistente/comportamiento");
    const menu = page.getByRole("navigation", { name: "Secciones del asistente" });
    await menu.getByRole("link", { name: /Productos/ }).click();
    await expect(page).toHaveURL(/\/catalog/);
    await expect(page.getByRole("heading", { name: "Catálogo" })).toBeVisible();
    await expect(menu.getByRole("link", { name: /Productos/ })).toHaveAttribute("aria-current", "page");
    await menu.getByRole("link", { name: /Conocimiento/ }).click();
    await expect(page).toHaveURL(/\/knowledge/);
    await menu.getByRole("link", { name: /Comportamiento/ }).click();

    await page.getByLabel("Objetivo del asistente").selectOption("vender");
    await page.getByLabel("Pregunta por algo que no vendes").uncheck();
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByText("Guardado", { exact: true }).first()).toBeVisible({ timeout: 15_000 });
    await page.reload();
    await expect(page.getByLabel("Objetivo del asistente")).toHaveValue("vender");
    await expect(page.getByLabel("Pregunta por algo que no vendes")).not.toBeChecked();
    await expect(page.getByLabel("Pide hablar con una persona")).toBeChecked();
    if (process.env.E2E_SHOTS) {
      await page.screenshot({ path: `${process.env.E2E_SHOTS}/asistente-${test.info().project.name}.png` });
    }
  });

  test("una zona de entrega guardada llega al prompt del asistente", async ({ page }) => {
    await page.goto("/asistente/informacion");
    await page.getByRole("button", { name: "Entregas" }).click();
    await page.getByLabel("Hacemos delivery").check();
    await page.getByRole("button", { name: "Agregar zona" }).click();
    await page.getByLabel("Zona").fill("Miraflores");
    await page.getByLabel("Costo").fill("8.00");
    await page.getByLabel("Tiempo").fill("24 h");
    await page.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByText("Guardado", { exact: true }).first()).toBeVisible({ timeout: 15_000 });

    await page.goto("/asistente/proceso");
    await page.getByLabel("Etapa").selectOption("DATOS");
    await page.getByRole("button", { name: "Ver prompt" }).click();
    await expect(page.getByTestId("prompt-preview")).toContainText("Miraflores");
  });

  test("apagar una regla la saca de la vista previa del prompt", async ({ page }) => {
    // A rule without placeholders: one whose value is missing in the example
    // facts is dropped from the preview anyway.
    const text = "Si envió una imagen y ESTE TURNO dice que NO es un comprobante";
    await page.goto("/asistente/proceso");
    await page.getByLabel("Etapa").selectOption("COBRO");
    await page.getByRole("button", { name: "Ver prompt" }).click();
    await expect(page.getByTestId("prompt-preview")).toContainText(text);

    const row = page.getByTestId("rule").filter({ hasText: text });
    await row.getByRole("switch").click();
    await expect(row.getByRole("switch")).toHaveAttribute("aria-checked", "false");

    await page.getByRole("button", { name: "Ver prompt" }).click();
    await expect(page.getByTestId("prompt-preview")).not.toContainText(text);
  });

  test("una situación nace en borrador, se prueba en el simulador y se activa", async ({ page }) => {
    await page.goto("/asistente/situaciones");
    await page.getByRole("button", { name: "Nueva situación" }).click();
    await page.getByLabel("Título").fill("Si piden factura");
    await page.getByLabel("Cuándo aplica", { exact: true }).fill("El cliente pide factura con RUC");
    await page.getByLabel("Qué hace el asistente").fill("Pide su RUC y razón social.");
    await page.getByLabel("Tenerla en cuenta siempre").check();
    await page.getByRole("button", { name: "Guardar" }).click();

    const card = page.getByTestId("situation-card").filter({ hasText: "Si piden factura" });
    await expect(card.getByText("Borrador", { exact: true })).toBeVisible();

    await card.getByRole("link", { name: "Probar en el simulador" }).click();
    await expect(page.getByText("Probando la situación «Si piden factura»")).toBeVisible();
    await page.getByPlaceholder(/Escribe/).fill("Hola, ¿dan factura?");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("debug-situations")).toContainText("Si piden factura (borrador)", {
      timeout: 30_000,
    });

    await page.goto("/asistente/situaciones");
    await page
      .getByTestId("situation-card")
      .filter({ hasText: "Si piden factura" })
      .getByRole("button", { name: "Activar" })
      .click();
    await expect(
      page.getByTestId("situation-card").filter({ hasText: "Si piden factura" }).getByText("Activa", { exact: true }),
    ).toBeVisible();
  });

  // The file goes to the database (STORAGE_BACKEND=postgres) and the card previews it
  // through a signed link, so a broken preview here means the link or the endpoint.
  test("una imagen se sube a la biblioteca", async ({ page }) => {
    await page.goto("/asistente/multimedia");
    await page.getByTestId("media-file").setInputFiles({ name: "qr.png", mimeType: "image/png", buffer: PNG });
    await page.getByLabel("Título").fill("QR de Yape");
    await expect(page.getByLabel("Clave")).toHaveValue("qr_de_yape");
    await page.getByLabel("Cuándo usarlo").fill("Cuando el cliente va a pagar");
    await page.getByRole("button", { name: "Subir" }).click();
    await expect(page.getByTestId("media-card").filter({ hasText: "QR de Yape" })).toBeVisible({
      timeout: 15_000,
    });
  });

  // Feature 020: the wizard ends with the rubro's template, applied in one step; its situations are
  // then the business's own. One the business already made from the old suggestions is not copied.
  test("el wizard termina aplicando la plantilla del rubro", async ({ page }) => {
    await page.goto("/onboarding?paso=6");
    const template = page.getByTestId("rubro-template");
    await expect(template).toBeVisible({ timeout: 15_000 });
    await template.getByLabel("En cuánto tiempo emiten la factura").fill("72 horas");
    await template.getByRole("button", { name: "Aplicar plantilla" }).click();
    await expect(page.getByText("Plantilla aplicada")).toBeVisible();

    await page.goto("/asistente");
    await expect(page.getByText("Activa tu primera situación")).toBeVisible();
    await page.goto("/asistente/situaciones");
    await expect(
      page.getByTestId("situation-card").filter({ hasText: "Si piden factura" }).getByText("Activa", { exact: true }),
    ).toBeVisible();
  });
});
