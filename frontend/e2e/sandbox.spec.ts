// See `./fixtures` for why the rate limiter is reset rather than raised.
import { expect, test } from "./fixtures";

import { freshIdentity, verificationPath, waitForEmail } from "./outbox";
import { readyToTest } from "./ready";

/**
 * Modo prueba, end to end (TASK-022).
 *
 * The verification the task asks for is "conversación con respuesta del
 * agente y traza visible", and that is what the first case does - through the
 * browser, against the real backend, with a real agent turn persisted.
 *
 * It needs a working LLM, and there is none in CI. `LLM_PROFILE=offline`
 * supplies a deterministic provider that answers from a hash and says so in
 * its first line, so the whole path - conversation, agent-core, trace,
 * persistence - is exercised without a credential. What it cannot prove is
 * the quality of an answer; that is what the golden-conversation evals are
 * for, and they run against a real model in their own workflow.
 */

test.describe("Modo prueba", () => {
  test("el agente responde y su traza es visible", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/sandbox");

    // The label is not decoration. An owner who suspects each test costs them
    // a trial message stops testing.
    await expect(page.getByText("No gasta mensajes de tu prueba")).toBeVisible();

    await page.getByLabel("Escribe como si fueras un cliente").fill("Hola, ¿qué venden?");
    await page.getByRole("button", { name: "Enviar", exact: true }).click();

    // The question shows immediately, before the round trip.
    await expect(page.getByText("Hola, ¿qué venden?")).toBeVisible();

    // And the agent answers. Generous timeout: a real model would be slower
    // than the offline one, and this test should not start failing the day a
    // credential is configured.
    const trace = page.getByTestId("trace");
    await expect(trace).toBeVisible({ timeout: 40_000 });

    // Collapsed by default, so the transcript stays readable.
    await expect(page.getByText("Herramientas que usó")).toBeHidden();

    await trace.getByText("Ver cómo respondió").click();
    await expect(page.getByText("Herramientas que usó")).toBeVisible();
    await expect(page.getByText("Fuentes que consultó")).toBeVisible();
    // The model that produced the answer, named. Without it a trace cannot
    // explain anything.
    await expect(page.getByText("Modelo:")).toBeVisible();
  });

  test("reiniciar deja el hilo limpio", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/sandbox");

    await page.getByLabel("Escribe como si fueras un cliente").fill("¿Hacen delivery?");
    await page.getByRole("button", { name: "Enviar", exact: true }).click();
    await expect(page.getByTestId("trace")).toBeVisible({ timeout: 40_000 });

    await page.getByRole("button", { name: "Reiniciar conversación" }).click();

    // Clean means nothing of the old thread is left. The question is also one of
    // the suggestion chips of the empty state, so "hidden" would only hold for the
    // instant the skeleton shows; what is left is that chip alone, and no trace.
    await expect(page.getByText("Escríbele como lo haría un cliente")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId("trace")).toHaveCount(0);
    await expect(page.getByText("¿Hacen delivery?")).toHaveCount(1);
  });

  test("una sugerencia envía el mensaje de una", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/sandbox");

    await page.getByRole("button", { name: "¿Cómo puedo pagar?" }).click();
    await expect(page.getByTestId("trace")).toBeVisible({ timeout: 40_000 });
  });
});

test.describe("Modo prueba sin configurar", () => {
  test("explica qué falta en vez de fallar", async ({ page }) => {
    // A brand new account: the checklist is empty, so the backend refuses
    // with `sandbox_not_ready`.
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

    await page.goto("/sandbox");

    await expect(page.getByText("Todavía no puedes probar tu agente")).toBeVisible({
      timeout: 15_000,
    });
    // A way forward, not a dead end.
    await expect(page.getByRole("link", { name: "Ir a la configuración" })).toBeVisible();
  });
});
