// See `./fixtures` for why the rate limiter is reset rather than raised.
import { expect, test } from "./fixtures";
import { readyToTest } from "./ready";

/**
 * ¿Respondió bien? in the simulator (Feature 018, TASK-1804, HU-06).
 *
 * With `LLM_PROFILE=offline` the answer is deterministic; what is checked is the
 * loop around it: rate a reply, say why, see why it answered that way, and land
 * on a situation already filled with the customer's words.
 */

test.describe("¿Respondió bien?", () => {
  test("califica una respuesta, explica por qué y crea una situación con el caso", async ({
    page,
  }) => {
    await readyToTest(page);
    await page.goto("/sandbox");
    await page.getByLabel("Escribe como si fueras un cliente").fill("¿Tienen envíos a Arequipa?");
    await page.getByRole("button", { name: "Enviar", exact: true }).click();

    const feedback = page.getByTestId("reply-feedback").last();
    await expect(feedback).toBeVisible({ timeout: 40_000 });

    await feedback.getByRole("button", { name: "Sí, respondió bien" }).click();
    await expect(feedback.getByRole("button", { name: "Sí, respondió bien" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await feedback.getByRole("button", { name: "No, respondió mal" }).click();
    const panel = feedback.getByTestId("feedback-panel");
    await expect(panel).toBeVisible();
    await panel.getByLabel("¿Qué estuvo mal o qué debió decir?").fill("Debió decir que enviamos por Olva");
    await panel.getByRole("button", { name: "Guardar" }).click();
    await expect(panel.getByRole("status")).toHaveText("Guardado");
    await expect(panel.getByTestId("feedback-why")).toContainText("Etapa:");
    if (process.env.E2E_SHOTS) {
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({ path: `${process.env.E2E_SHOTS}/respondio-${test.info().project.name}.png` });
    }

    await panel.getByRole("link", { name: "Crear una situación para este caso" }).click();
    await expect(page).toHaveURL(/\/asistente\/situaciones/);
    await expect(page.getByText("Nueva situación").first()).toBeVisible();
    await expect(page.getByLabel(/Cuándo aplica/)).toHaveValue(/¿Tienen envíos a Arequipa\?/);
    await expect(page.getByLabel(/Ejemplo/)).toHaveValue("Debió decir que enviamos por Olva");
  });

  test("el atajo de pregunta frecuente abre el bloque con la pregunta escrita", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/asistente/informacion?bloque=faqs&pregunta=%C2%BFHacen%20factura%3F");
    await expect(page.getByLabel("Pregunta")).toHaveValue("¿Hacen factura?");
  });
});
