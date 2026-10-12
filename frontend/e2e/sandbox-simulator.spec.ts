// See `./fixtures` for why the rate limiter is reset rather than raised.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { expect, test } from "./fixtures";
import { readyToTest } from "./ready";

/**
 * The simulator as a simulator (TASK-422, C-19).
 *
 * The verification the task asks for is "tocar un botón del simulador cambia la
 * etapa en el panel de depuración". It runs against the **real stack**: the
 * conversation, the turn pipeline, the state in Postgres and the debug panel are
 * all real. Two things are set up from outside, and both are said here so nobody
 * mistakes this for a test of the model:
 *
 * - The business runs the `certificaciones` process, which is the one with paths
 *   to tap. There is no API to choose a process yet (TASK-435), so the row of
 *   `agent_configs` is updated directly, before the first message.
 * - The buttons are written to `messages` as an outbound message the way the
 *   system writes any asynchronous one (a payment confirmation, a follow-up), and
 *   the simulator's poll draws them. A model that decides to call
 *   `mostrar_opciones` produces exactly the same row, but whether a vendor's
 *   model decides that is the evals' business, not this spec's.
 *
 * Needs the stack up (`make up`) and the Postgres container reachable through
 * Docker, like the rate-limiter reset in `./fixtures`.
 */

const run = promisify(execFile);
const POSTGRES_CONTAINER = process.env.E2E_POSTGRES_CONTAINER ?? "leo-dev-postgres-1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

async function psql(sql: string): Promise<string> {
  const { stdout } = await run("docker", [
    "exec",
    POSTGRES_CONTAINER,
    "psql",
    "-U",
    process.env.E2E_POSTGRES_USER ?? "leo",
    "-d",
    process.env.E2E_POSTGRES_DB ?? "leo",
    "-At",
    "-c",
    sql,
  ]);
  return stdout.trim();
}

/**
 * The simulator conversation the page is talking in, and its business.
 *
 * In development React mounts the page twice, so two conversations are opened and
 * only one of them is used: the one that has messages is the one to look for.
 */
async function activeConversation(): Promise<{ conversationId: string; tenantId: string }> {
  const [conversationId, tenantId] = (
    await psql(
      `select c.id, c.tenant_id from conversations c
        where c.channel = 'sandbox'
          and exists (select 1 from messages m where m.conversation_id = c.id)
        order by c.created_at desc limit 1`,
    )
  ).split("|");
  // Both go into SQL below, so they are checked rather than trusted.
  expect(conversationId).toMatch(UUID);
  expect(tenantId).toMatch(UUID);
  return { conversationId, tenantId };
}

const BUTTONS = JSON.stringify({
  tipo: "botones",
  body: "¿Qué necesitas?",
  botones: [
    { id: "camino:certificacion", title: "Solo certificar" },
    { id: "camino:programa", title: "Programa completo" },
  ],
});

test.describe("Simulador con salientes tipados", () => {
  test("tocar un botón cambia la etapa en el panel de depuración", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/sandbox");
    const input = page.getByLabel("Escribe como si fueras un cliente");
    await expect(input).toBeEnabled();

    // The business of this account: the newest one with a simulator conversation.
    const tenantId = (
      await psql(
        "select tenant_id from conversations where channel = 'sandbox' order by created_at desc limit 1",
      )
    ).trim();
    expect(tenantId).toMatch(UUID);
    await psql(
      `update agent_configs set process_template_key = 'certificaciones' where tenant_id = '${tenantId}'`,
    );

    await page.getByLabel("Modo depuración").check();
    await expect(page.getByTestId("debug-panel")).toBeVisible();

    // First turn: the greeting. INICIO ends in CALIFICAR.
    await input.fill("Hola, quiero información");
    await page.getByRole("button", { name: "Enviar", exact: true }).click();
    const stage = page.getByTestId("debug-stage");
    await expect(stage).toHaveText(/INICIO\s*→\s*CALIFICAR/, { timeout: 45_000 });

    // The business asks which path the customer wants, with buttons. The
    // simulator picks them up on its next poll.
    const { conversationId } = await activeConversation();
    await psql(
      `insert into messages (tenant_id, conversation_id, direction, author, content, msg_type, payload)
       values ('${tenantId}', '${conversationId}', 'outbound', 'agent', '¿Qué necesitas?',
               'interactive', '${BUTTONS}'::jsonb)`,
    );
    const certify = page.getByRole("button", { name: "Solo certificar" });
    await expect(certify).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Programa completo" })).toBeVisible();

    // Tapping sends the id of the button, and the stage moves with it.
    await certify.click();
    await expect(stage).toHaveText(/CALIFICAR\s*→\s*IDENTIFICAR/, { timeout: 45_000 });
    await expect(page.getByTestId("debug-facts")).toContainText('"camino": "certificacion"');
    await expect(page.getByTestId("debug-tools-enabled")).toContainText("buscar_productos");
  });

  test("una imagen se envía y el agente sabe que llegó", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/sandbox");
    await expect(page.getByLabel("Escribe como si fueras un cliente")).toBeEnabled();

    // A 1x1 PNG: the simulator only checks the type and the size for now.
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
      "base64",
    );
    await page
      .getByTestId("image-input")
      .setInputFiles({ name: "voucher.png", mimeType: "image/png", buffer: png });

    await expect(page.getByText("Imagen enviada")).toBeVisible();
    await expect(page.getByTestId("trace")).toBeVisible({ timeout: 45_000 });
  });

  test("reiniciar borra la conversación y la etapa vuelve a empezar", async ({ page }) => {
    await readyToTest(page);
    await page.goto("/sandbox");
    const input = page.getByLabel("Escribe como si fueras un cliente");
    await expect(input).toBeEnabled();
    await page.getByLabel("Modo depuración").check();

    await input.fill("Hola");
    await page.getByRole("button", { name: "Enviar", exact: true }).click();
    await expect(page.getByTestId("debug-stage")).toHaveText(/INICIO\s*→/, { timeout: 45_000 });

    await page.getByRole("button", { name: "Reiniciar conversación" }).click();
    await expect(page.getByText("Escríbele como lo haría un cliente")).toBeVisible({
      timeout: 15_000,
    });
    // The state is gone with the messages: the first turn after a reset starts at INICIO.
    await expect(page.getByTestId("debug-panel")).toContainText("Envía un mensaje");

    await input.fill("Hola otra vez");
    await page.getByRole("button", { name: "Enviar", exact: true }).click();
    await expect(page.getByTestId("debug-stage")).toHaveText(/INICIO\s*→/, { timeout: 45_000 });
  });
});
