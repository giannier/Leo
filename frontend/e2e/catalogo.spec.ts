// See `./fixtures` for why the rate limiter is reset rather than raised.
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Catálogo (Feature 018, TASK-1802): create a product with tags and photos,
 * find it by tag, duplicate it, switch the copy on and remove it.
 */

// A real 1x1 PNG: the server sniffs the content, not the name.
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

async function register(page: Page): Promise<void> {
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
}

test.describe("Catálogo", () => {
  test("crea un producto con etiquetas y foto, lo encuentra, lo duplica y borra la copia", async ({
    page,
  }) => {
    await register(page);
    await page.goto("/catalog");
    await expect(page.getByRole("heading", { name: "Catálogo" })).toBeVisible();
    await expect(page.getByText("Aún no tienes productos")).toBeVisible();

    await page.getByRole("button", { name: "Agregar producto" }).click();
    const editor = page.getByRole("dialog", { name: "Nuevo producto" });
    await editor.getByLabel("Nombre").fill("Certificado SIAF");
    await editor.getByLabel("Precio (S/)").fill("120");
    await editor.getByLabel("Categoría").fill("Certificaciones");
    const tags = editor.getByLabel("Etiquetas");
    await tags.fill("siaf");
    await tags.press("Enter");
    await tags.fill("oferta");
    await tags.press("Enter");
    await editor.getByRole("button", { name: "Crear producto" }).click();

    // The panel stays open on the new product to add its photos.
    const edit = page.getByRole("dialog", { name: "Editar producto" });
    await expect(edit.getByRole("status")).toHaveText("Guardado");
    await edit.locator("#product-photo-upload").setInputFiles({
      name: "siaf.png",
      mimeType: "image/png",
      buffer: PNG,
    });
    await expect(edit.getByTestId("product-photos").locator("img")).toHaveCount(1);
    await expect(edit.getByText("Principal", { exact: true })).toBeVisible();
    if (process.env.E2E_SHOTS) {
      await page.screenshot({ path: `${process.env.E2E_SHOTS}/editor-${test.info().project.name}.png` });
    }
    await edit.getByRole("button", { name: "Cerrar", exact: true }).first().click();

    const list = page.getByTestId("catalog-list");
    await expect(list.getByText("Certificado SIAF")).toBeVisible();
    await expect(list.getByText("S/ 120.00")).toBeVisible();
    await expect(list.locator("img")).toHaveCount(1);
    if (process.env.E2E_SHOTS) {
      await page.screenshot({ path: `${process.env.E2E_SHOTS}/catalogo-${test.info().project.name}.png` });
    }

    // Search by tag.
    await page.getByLabel("Buscar").fill("oferta");
    await expect(page.getByText("1 de 1 productos")).toBeVisible();
    await page.getByLabel("Buscar").fill("");

    await list.getByRole("button", { name: "Duplicar" }).click();
    await expect(list.getByText("Certificado SIAF (copia)")).toBeVisible();
    await expect(list.getByText("Inactivo")).toBeVisible();

    // Filter by a tag from a row.
    await list.getByRole("button", { name: "#siaf" }).first().click();
    await expect(page.getByText("Con la etiqueta")).toBeVisible();
    await expect(page.getByText("2 de 2 productos")).toBeVisible();
    await page.getByRole("button", { name: "quitar" }).click();

    await page.getByRole("button", { name: "inactivos" }).click();
    const copy = page.getByTestId("catalog-list").locator("li", { hasText: "(copia)" });
    await copy.getByRole("button", { name: "Activar" }).click();
    await expect(page.getByText("Nada coincide con tu búsqueda")).toBeVisible();
    await page.getByRole("button", { name: "todos" }).click();

    page.once("dialog", (dialog) => void dialog.accept());
    await page
      .getByTestId("catalog-list")
      .getByRole("button", { name: "Borrar Certificado SIAF (copia)" })
      .click();
    await expect(page.getByText("1 de 1 productos")).toBeVisible();
  });
});
