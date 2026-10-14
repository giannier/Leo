// `./fixtures`, not `@playwright/test`: the fixture clears the auth rate
// limiter before each test. See the comment there for why the limit is
// reset rather than raised.
import { expect, test } from "./fixtures";

import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * `register → verify → login`, the verification TASK-020 asks for.
 *
 * Driven through the browser against the real backend, because everything
 * interesting here lives between the two: whether the refresh cookie is
 * actually stored and sent, whether CORS lets the credentialed request
 * through, whether a reload restores the session. None of that is visible to
 * a component test.
 */

test.describe("Registro, verificación y entrada", () => {
  test("una persona crea su cuenta, confirma el correo y entra al panel", async ({ page }) => {
    const user = freshIdentity();

    // --- register ---------------------------------------------------
    await page.goto("/register");
    await expect(page.getByRole("heading", { name: "Crea tu cuenta" })).toBeVisible();

    await page.getByLabel("Tu nombre").fill(user.fullName);
    await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
    await page.getByLabel("Correo electrónico").fill(user.email);
    await page.getByLabel("Teléfono de contacto").fill(user.phone);
    await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
    await page.getByRole("button", { name: "Crear mi cuenta" }).click();

    await expect(page.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();
    await expect(page.getByText(user.email)).toBeVisible();

    // --- verify -------------------------------------------------------
    const message = await waitForEmail(user.email);
    await page.goto(verificationPath(message));

    // The page verifies and then forwards to login on its own.
    await expect(page).toHaveURL(/\/login\?verified=1/, { timeout: 15_000 });
    await expect(page.getByText("Correo confirmado")).toBeVisible();

    // --- login --------------------------------------------------------
    await page.getByLabel("Correo electrónico").fill(user.email);
    await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
    await page.getByRole("button", { name: "Entrar" }).click();

    await expect(page).toHaveURL("/", { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: /Hola/ })).toBeVisible();
    // The name is rendered twice - once in the sidebar, once in the phone
    // header - and CSS hides whichever does not belong to this viewport. So
    // ask for the visible one rather than the first in the DOM, which on a
    // phone is the hidden sidebar copy.
    await expect(page.getByText(user.businessName).locator("visible=true").first()).toBeVisible();

    // The trial banner is the thing that must never be missing: it is how a
    // customer learns their trial is ending before the agent goes quiet.
    // "Modo pruebas" while BILLING_ENFORCED is off (the default for now).
    await expect(
      page.getByText(/días de prueba|día de prueba|termina hoy|Modo pruebas/).first(),
    ).toBeVisible();
    await expect(page.getByText(/respuestas del agente por WhatsApp/)).toBeVisible();

    // --- the session survives a reload ----------------------------------
    // The access token is deliberately held in memory only, so this proves
    // the httpOnly refresh cookie was stored and is sent back. If the cookie
    // path, SameSite attribute or CORS credentials were wrong, the panel
    // would bounce to /login right here.
    await page.reload();
    await expect(page.getByRole("heading", { name: /Hola/ })).toBeVisible({ timeout: 15_000 });

    // And the token is not sitting anywhere a script could read it.
    const leaked = await page.evaluate(() => ({
      local: JSON.stringify(window.localStorage),
      session: JSON.stringify(window.sessionStorage),
      cookie: document.cookie,
    }));
    expect(leaked.local).not.toContain("eyJ");
    expect(leaked.session).not.toContain("eyJ");
    expect(leaked.cookie).not.toContain("refresh_token");

    // --- sign out -------------------------------------------------------
    await page.getByRole("button", { name: "Cerrar sesión" }).click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });

    // And it really is over: a reload must not resurrect the session.
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  });

  test("un correo sin confirmar no entra, y puede pedir otro enlace", async ({ page }) => {
    const user = freshIdentity();

    await page.goto("/register");
    await page.getByLabel("Tu nombre").fill(user.fullName);
    await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
    await page.getByLabel("Correo electrónico").fill(user.email);
    await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
    await page.getByRole("button", { name: "Crear mi cuenta" }).click();
    await expect(page.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();

    await page.goto("/login");
    await page.getByLabel("Correo electrónico").fill(user.email);
    await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
    await page.getByRole("button", { name: "Entrar" }).click();

    // The password was right. Saying "wrong credentials" here would have the
    // person retyping a correct password until they give up.
    await expect(page.getByText("Falta confirmar tu correo")).toBeVisible();
    await page.getByRole("button", { name: "Reenviar el enlace" }).click();
    await expect(page.getByText(/Te enviamos un enlace nuevo/)).toBeVisible();
  });

  test("una contraseña equivocada lo dice sin revelar si la cuenta existe", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Correo electrónico").fill("nadie@example.com");
    await page.getByLabel("Contraseña", { exact: true }).fill("una-clave-cualquiera");
    await page.getByRole("button", { name: "Entrar" }).click();

    await expect(page.getByText("Correo o contraseña incorrectos.")).toBeVisible();
  });
});

test.describe("Guardas", () => {
  test("el panel no se abre sin sesión", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  });

  test("un enlace de verificación inválido explica qué hacer", async ({ page }) => {
    await page.goto("/verify-email?token=no-es-un-token-real");
    await expect(page.getByRole("heading", { name: /No pudimos confirmar tu correo/ })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("button", { name: "Enviarme un enlace nuevo" })).toBeVisible();
  });
});

test.describe("Formulario de registro", () => {
  test("valida antes de enviar y marca el campo con el problema", async ({ page }) => {
    await page.goto("/register");
    await page.getByLabel("Correo electrónico").fill("no-es-un-correo");
    await page.getByLabel("Contraseña", { exact: true }).fill("corta");
    await page.getByRole("button", { name: "Crear mi cuenta" }).click();

    // The error sits under the field it belongs to, not in a summary at the
    // top that the user has to map back to an input.
    await expect(page.getByText("Escribe tu nombre.")).toBeVisible();
    await expect(page.getByText("Escribe un correo válido.")).toBeVisible();
    await expect(page.getByText("Usa al menos 10 caracteres.")).toBeVisible();

    // Focus lands on the first problem, so a keyboard user is not left
    // hunting for what went wrong.
    await expect(page.getByLabel("Tu nombre")).toBeFocused();
  });

  test("la contraseña se puede revelar", async ({ page }) => {
    await page.goto("/register");
    const password = page.getByLabel("Contraseña", { exact: true });
    await password.fill("una-clave-larga-123");
    await expect(password).toHaveAttribute("type", "password");

    await page.getByRole("button", { name: "Mostrar contraseña" }).click();
    await expect(password).toHaveAttribute("type", "text");
  });
});
