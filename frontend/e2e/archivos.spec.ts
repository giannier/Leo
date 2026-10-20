// See `./fixtures` for why the rate limiter is reset rather than raised.
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Files in Postgres, seen the way a person sees them: a photo uploaded in the panel
 * has to actually *load* in the browser through its signed link.
 *
 * The other specs count `<img>` elements, and a broken image is still an element.
 * What can break in a real browser and nowhere else - a cross-origin policy, a
 * content-security policy, a wrong public address - only shows up as an image that
 * never decodes, so this one asserts exactly that, then checks the link itself.
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

test.describe("Archivos", () => {
  test("una foto subida se ve en el navegador y su enlace firmado se porta como debe", async ({
    page,
  }) => {
    await register(page);
    await page.goto("/catalog");
    await page.getByRole("button", { name: "Agregar producto" }).click();
    const editor = page.getByRole("dialog", { name: "Nuevo producto" });
    await editor.getByLabel("Nombre").fill("Producto con foto");
    await editor.getByLabel("Precio (S/)").fill("10");
    await editor.getByRole("button", { name: "Crear producto" }).click();

    const edit = page.getByRole("dialog", { name: "Editar producto" });
    await expect(edit.getByRole("status")).toHaveText("Guardado");
    await edit.locator("#product-photo-upload").setInputFiles({
      name: "foto.png",
      mimeType: "image/png",
      buffer: PNG,
    });

    // 1. The browser decoded it. This is the assertion that fails when anything
    //    between the API and the page (a header, a policy, an address) blocks it.
    const photo = edit.getByTestId("product-photos").locator("img");
    await expect(photo).toHaveCount(1);
    await expect
      .poll(() => photo.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0), {
        message: "the photo never loaded in the browser",
        timeout: 15_000,
      })
      .toBe(true);

    const src = await photo.getAttribute("src");
    expect(src).toContain("/api/v1/files/v1.");

    // 2. The link serves exactly what was uploaded, and says how to reuse it.
    const served = await page.request.get(src ?? "");
    expect(served.status()).toBe(200);
    expect(served.headers()["content-type"]).toBe("image/png");
    expect(Buffer.from(await served.body()).equals(PNG)).toBe(true);
    const etag = served.headers()["etag"] ?? "";
    expect(etag).toMatch(/^"[0-9a-f]{64}"$/);
    expect(served.headers()["cache-control"]).toContain("private");
    expect(served.headers()["x-content-type-options"]).toBe("nosniff");

    // 3. A repeat visit is answered without sending the file again.
    const again = await page.request.get(src ?? "", { headers: { "If-None-Match": etag } });
    expect(again.status()).toBe(304);

    // 4. A link that has been touched is refused, and looks exactly like a link
    //    that never existed.
    const token = (src ?? "").split("/").pop() ?? "";
    const touched = token.slice(0, -1) + (token.endsWith("A") ? "B" : "A");
    const base = (src ?? "").slice(0, (src ?? "").lastIndexOf("/") + 1);
    const forged = await page.request.get(`${base}${touched}`);
    const garbage = await page.request.get(`${base}not-a-token`);
    expect(forged.status()).toBe(404);
    expect(garbage.status()).toBe(404);
    expect(await forged.text()).toBe(await garbage.text());
  });
});
