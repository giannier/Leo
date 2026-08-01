// See `./fixtures` for why the rate limiter is reset rather than raised.
import { expect, test } from "./fixtures";

import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Every link in the panel goes somewhere.
 *
 * This exists because seven of them did not. The sidebar and the bottom bar
 * advertise the whole product from day one - which is right, a menu that
 * grows item by item hides the shape of what you bought - but every one of
 * those links answered 404 with Next's default page, in English, looking like
 * a crash rather than like work in progress.
 *
 * The test walks the navigation the way a person does and asserts each
 * destination renders. It does not care whether a screen is finished; a
 * placeholder that says "todavía estamos construyendo esto" is a fine answer
 * and a 404 is not.
 */

async function signedIn(page: import("@playwright/test").Page) {
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

test.describe("Navegación del panel", () => {
  test("ningún enlace del menú lleva a una página rota", async ({ page }) => {
    await signedIn(page);

    // Read the destinations off the rendered navigation rather than repeating
    // them here. A list in the test would go stale the first time somebody
    // adds a menu item, and going stale silently is the whole failure mode.
    const targets = await page
      .locator("nav a[href^='/']")
      .evaluateAll((links) =>
        Array.from(new Set(links.map((a) => a.getAttribute("href")).filter(Boolean) as string[])),
      );

    expect(targets.length).toBeGreaterThan(3);

    for (const href of targets) {
      const response = await page.goto(href);
      expect(response?.status(), `${href} answered ${response?.status()}`).toBeLessThan(400);
      // Next's 404 renders with a 200 in the App Router in some setups, so
      // the status alone is not proof. The panel shell is.
      await expect(
        page.getByRole("heading", { level: 1 }),
        `${href} rendered no heading`,
      ).toBeVisible({ timeout: 15_000 });
      await expect(page.getByText("No encontramos esta página")).toBeHidden();
    }
  });

  test("una URL inventada sí da el 404, en español", async ({ page }) => {
    await signedIn(page);
    await page.goto("/esta-ruta-no-existe");

    await expect(page.getByRole("heading", { name: "No encontramos esta página" })).toBeVisible();
    // A way back, rather than a dead end.
    await expect(page.getByRole("link", { name: "Ir a mi panel" })).toBeVisible();
  });

  test("la barra inferior aparece en el teléfono y no en el escritorio", async ({ page }) => {
    await signedIn(page);

    await page.setViewportSize({ width: 375, height: 800 });
    const bottom = page.locator("nav.fixed");
    await expect(bottom).toBeVisible();
    // Material caps a bottom bar at five destinations.
    expect(await bottom.locator("a").count()).toBeLessThanOrEqual(5);

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(bottom).toBeHidden();
  });
});
