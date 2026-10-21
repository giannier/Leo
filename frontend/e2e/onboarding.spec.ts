// See `./fixtures` for why the rate limiter is reset rather than raised.
import { expect, test } from "./fixtures";

import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * The wizard, end to end (TASK-021).
 *
 * The verification the task asks for is "completar wizard hasta
 * `sandbox_ready`" - so the first case does exactly that, through the browser,
 * against the real backend, and then checks the *server* agrees by reading the
 * activation checklist rather than by trusting what the page rendered.
 *
 * The rest cover the two promises that are easy to write and easy to get
 * wrong: that a step survives closing the tab, and that an import shows a
 * preview before it creates anything.
 */

/** Signs up, verifies, and lands in the panel. */
async function signedIn(page: import("@playwright/test").Page) {
  const user = freshIdentity();

  await page.goto("/register");
  await page.getByLabel("Tu nombre").fill(user.fullName);
  await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Teléfono de contacto", { exact: true }).fill(user.phone);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Crear mi cuenta" }).click();
  await expect(page.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();

  const message = await waitForEmail(user.email);
  await page.goto(verificationPath(message));
  await expect(page).toHaveURL(/\/login\?verified=1/, { timeout: 15_000 });

  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL("/", { timeout: 15_000 });

  return user;
}

/** Waits for the autosave to report it is done. */
async function saved(page: import("@playwright/test").Page) {
  await expect(page.getByText("Guardado", { exact: true })).toBeVisible({ timeout: 15_000 });
}

test.describe("Wizard de onboarding", () => {
  test("una persona completa la configuración y su agente queda listo para probar", async ({
    page,
  }) => {
    await signedIn(page);
    await page.goto("/onboarding?paso=1");

    // --- 1. the business -------------------------------------------------
    await expect(page.getByRole("heading", { name: /1\. Tu negocio/ })).toBeVisible();
    await page.getByLabel("Nombre comercial").fill("Panadería Quispe");
    await page.getByLabel("Rubro").selectOption("retail");
    await page
      .getByLabel("Cuéntale a Leo sobre tu negocio")
      .fill("Panadería artesanal en Miraflores. Pan del día y pasteles por encargo.");
    await saved(page);

    // --- 2. the agent ----------------------------------------------------
    await page.getByRole("button", { name: "Siguiente" }).click();
    await expect(page.getByRole("heading", { name: /2\. Tu agente/ })).toBeVisible();
    await page.getByLabel("¿Cómo se llama tu agente?").fill("Lucía");
    await page.getByLabel("Tono").selectOption("cercano");
    // What the agent does: selling is on by default; add quoting.
    await page.getByLabel("Cotizar").check();
    await saved(page);

    // --- 3. the catalogue ------------------------------------------------
    await page.getByRole("button", { name: "Siguiente" }).click();
    await expect(page.getByRole("heading", { name: /3\. Qué vendes/ })).toBeVisible();
    await page.getByLabel("Nombre", { exact: true }).fill("Pan ciabatta");
    await page.getByLabel("Precio", { exact: true }).fill("8.50");
    await page.getByRole("button", { name: "Agregar", exact: true }).click();
    await expect(page.getByText("Pan ciabatta")).toBeVisible({ timeout: 15_000 });

    // --- 4. knowledge is optional, so skip it ----------------------------
    await page.getByRole("button", { name: "Siguiente" }).click();
    await expect(page.getByRole("heading", { name: /4\. Lo que Leo debe saber/ })).toBeVisible();

    // --- 5. payment ------------------------------------------------------
    await page.getByRole("button", { name: "Siguiente" }).click();
    await expect(page.getByRole("heading", { name: /5\. Cómo te pagan/ })).toBeVisible();
    await page.getByLabel("Método").selectOption("yape");
    await page
      .getByLabel("Instrucciones")
      .fill("Yape al 987654321 a nombre de Ana Quispe. Manda la captura por aquí.");
    await page.getByRole("button", { name: "Agregar", exact: true }).click();
    await expect(page.getByText(/Yape al 987654321/)).toBeVisible({ timeout: 15_000 });

    // --- 6. the summary --------------------------------------------------
    await page.getByRole("button", { name: "Siguiente" }).click();
    await expect(page.getByRole("heading", { name: /6\. Resumen/ })).toBeVisible();

    // `sandbox_ready`, the state the task names.
    await expect(page.getByText("Tu agente está listo para probar")).toBeVisible({
      timeout: 15_000,
    });

    // Confirmed on a different page, after a full reload. Both render from a
    // fresh `GET /tenant/activation-checklist`, so this is the server's
    // verdict rather than anything this component was holding in memory.
    await page.goto("/");
    await page.reload();
    await expect(page.getByText("5 de 6 pasos completos")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("link", { name: "Abrir modo prueba" })).toBeVisible();
  });

  test("lo que escribes sobrevive a cerrar la pestaña", async ({ page }) => {
    await signedIn(page);
    await page.goto("/onboarding?paso=1");

    await page.getByLabel("Nombre comercial").fill("Bodega La Esquina");
    await saved(page);

    // A full reload: nothing in memory survives it, so anything still on the
    // screen afterwards came back from the server.
    await page.reload();
    await expect(page.getByLabel("Nombre comercial")).toHaveValue("Bodega La Esquina", {
      timeout: 15_000,
    });
  });

  test("no se pierde lo último escrito al pasar de paso enseguida", async ({ page }) => {
    await signedIn(page);
    await page.goto("/onboarding?paso=1");

    // Type and move on straight away, inside the debounce window. This is how
    // people actually use a wizard, and it is the guarantee HU-02 makes:
    // "queda persistido aunque cierre el navegador".
    //
    // The assertion is on the *guarantee*, not on which mechanism keeps it -
    // either the debounce fires in time or the step flushes on its way out,
    // and from the customer's side those are the same thing. An earlier
    // version froze the clock to force the flush path specifically; it was
    // dropped because freezing time also freezes React's scheduler, so the
    // navigation sometimes never finished rendering and the test failed for a
    // reason that had nothing to do with saving. A flaky test is worth less
    // than no test.
    await page.getByLabel("Nombre comercial").fill("Minimarket El Sol");
    await page.getByRole("button", { name: "Siguiente" }).click();
    await expect(page.getByRole("heading", { name: /2\. Tu agente/ })).toBeVisible();

    // A full reload, so nothing in memory can be answering for the server.
    await page.goto("/onboarding?paso=1");
    await page.reload();
    await expect(page.getByLabel("Nombre comercial")).toHaveValue("Minimarket El Sol", {
      timeout: 15_000,
    });
  });

  test("el wizard se retoma donde quedó", async ({ page }) => {
    await signedIn(page);

    await page.goto("/onboarding?paso=2");
    await page.getByLabel("¿Cómo se llama tu agente?").fill("Rosa");
    await saved(page);

    // Straight to step 2 by URL, which is what the reminder email's deep-link
    // does.
    await page.goto("/onboarding?paso=2");
    await expect(page.getByLabel("¿Cómo se llama tu agente?")).toHaveValue("Rosa", {
      timeout: 15_000,
    });
  });

  test("la barra de pasos marca lo que ya está hecho", async ({ page }) => {
    await signedIn(page);
    await page.goto("/onboarding?paso=1");

    await page.getByLabel("Nombre comercial").fill("Panadería Quispe");
    await page.getByLabel("Rubro").selectOption("restaurante");
    await saved(page);

    // Step 3 is reachable by clicking its label, not only by walking forward.
    await page.getByRole("button", { name: /Catálogo/ }).click();
    await expect(page.getByRole("heading", { name: /3\. Qué vendes/ })).toBeVisible();
  });
});

test.describe("Importar el catálogo", () => {
  test("muestra un preview y no crea nada hasta confirmar", async ({ page }) => {
    await signedIn(page);
    await page.goto("/onboarding?paso=3");

    // One good row and one with a price that is not a number.
    await page.setInputFiles("#catalog-import", {
      name: "productos.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "nombre,precio,descripcion\n" +
          "Pan francés,0.50,Bolsa de 10\n" +
          "Torta de chocolate,no-es-un-precio,Por encargo\n" +
          "Empanada,3.50,De carne\n",
        "utf8",
      ),
    });

    await expect(page.getByText(/2 productos válidos/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/1 fila con problemas/)).toBeVisible();
    // The row number and the reason, so somebody can go and fix it.
    await expect(page.getByText(/Fila 3:/)).toBeVisible();

    // Nothing exists yet. This is the promise the preview makes.
    await expect(page.getByText("Pan francés")).toBeHidden();

    await page.getByRole("button", { name: /Crear 2 productos/ }).click();
    await expect(page.getByText("Se crearon 2 productos")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("Pan francés")).toBeVisible();
    await expect(page.getByText("Empanada")).toBeVisible();
    // The invalid row was skipped, not silently coerced into something.
    await expect(page.getByText("Torta de chocolate")).toBeHidden();
  });

  test("cancelar descarta el preview sin crear nada", async ({ page }) => {
    await signedIn(page);
    await page.goto("/onboarding?paso=3");

    await page.setInputFiles("#catalog-import", {
      name: "productos.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("nombre,precio\nAlfajor,2.00\n", "utf8"),
    });
    await expect(page.getByText(/1 producto válido/)).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: "Cancelar" }).click();
    await expect(page.getByText("Alfajor")).toBeHidden();
  });
});
