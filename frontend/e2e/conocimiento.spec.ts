// See `./fixtures` for why the rate limiter is reset rather than raised.
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Conocimiento (Feature 006, TASK-604): the library of what the assistant knows.
 *
 * Runs against the real stack with the indexing worker running (`make worker`):
 * a source goes from "En cola" to "Listo" for real. Not covered here, and why:
 * a PDF needs a real PDF to read, and reading a website needs the public
 * internet; both are covered by the backend suites and by the milestone.
 */

async function owner(page: Page): Promise<void> {
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

test.describe("Conocimiento", () => {
  test("agregar, priorizar, editar y ver lo que lee el asistente", async ({ page }) => {
    await owner(page);
    await page.goto("/knowledge");
    await expect(page.getByRole("heading", { name: "Conocimiento" })).toBeVisible();

    const write = page.getByRole("form", { name: "Escribir" });
    await write.getByLabel("Tipo").selectOption("policy");
    await write.getByLabel("Título").fill("Envíos");
    await write.getByLabel("Contenido").fill("Hacemos envíos a todo Lima en 24 horas por 10 soles.");
    await write.getByRole("button", { name: "Agregar" }).click();

    const source = page.getByTestId("knowledge-source").filter({ hasText: "Envíos" });
    await expect(source.getByTestId("knowledge-status")).toHaveText("Listo", { timeout: 30_000 });

    // Priority: what the business trusts when two sources disagree.
    await source.getByLabel("Prioridad").selectOption("50");
    await expect(source.getByLabel("Prioridad")).toHaveValue("50");

    // Edit the text: it is indexed again, and the passages show the new version.
    await source.getByRole("button", { name: "Editar" }).click();
    await expect(source.getByLabel("Contenido")).toHaveValue(/24 horas/);
    await source.getByLabel("Contenido").fill("Hacemos envíos a todo Lima en 48 horas. Gratis.");
    await source.getByRole("button", { name: "Guardar" }).click();
    await expect(source.getByTestId("knowledge-status")).toHaveText("Listo", { timeout: 30_000 });
    await source.getByRole("button", { name: "Ver qué lee el asistente" }).click();
    await expect(source.getByTestId("knowledge-chunks")).toContainText("48 horas");
    await expect(source.getByTestId("knowledge-chunks")).not.toContainText("24 horas");

    // An address that is not a website is refused before anything is read.
    const web = page.getByRole("form", { name: "Desde mi web" });
    await web.getByLabel("Dirección de tu web").fill("localhost");
    await web.getByRole("button", { name: "Leer mi web" }).click();
    await expect(web.getByRole("alert")).toContainText("dominio");

    // Removing it takes it out of what the assistant knows.
    page.once("dialog", (dialog) => void dialog.accept());
    await source.getByRole("button", { name: "Eliminar" }).click();
    await expect(page.getByTestId("knowledge-source")).toHaveCount(0);
  });
});
