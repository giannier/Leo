/**
 * `GET /widget.js`: the snippet a business pastes into its website (feature 019, phase F).
 *
 *     <script src="https://…/widget.js" data-chat="<key>" data-color="#2563eb" async></script>
 *
 * Optional `data-origin="Instagram"` marks where the visitors of that page come from (it reaches the lead).
 *
 * It draws a round button in the corner; a tap opens the business's chat page in a frame, a second tap closes
 * it. The chat itself is the page `/chat/<key>?embed=1`, so the website never touches the visitor's
 * conversation: it only hosts the frame. Served by a route, not a static file, so the production image needs
 * nothing extra.
 */

const SCRIPT = `(function () {
  var tag = document.currentScript;
  if (!tag) return;
  var key = tag.getAttribute("data-chat");
  if (!key || document.getElementById("leo-chat-button")) return;
  var color = /^#[0-9a-fA-F]{6}$/.test(tag.getAttribute("data-color") || "") ? tag.getAttribute("data-color") : "#2563eb";
  var origin = new URL(tag.src).origin;

  var button = document.createElement("button");
  button.id = "leo-chat-button";
  button.type = "button";
  button.setAttribute("aria-label", "Abrir el chat");
  button.style.cssText = "position:fixed;right:20px;bottom:20px;width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;z-index:2147483646;box-shadow:0 4px 16px rgba(0,0,0,.25);background:" + color + ";color:#fff;font-size:26px;line-height:56px;padding:0";
  button.textContent = "\\u{1F4AC}";

  var frame = document.createElement("iframe");
  frame.title = "Chat";
  var from = tag.getAttribute("data-origin");
  frame.src = origin + "/chat/" + encodeURIComponent(key) + "?embed=1" + (from ? "&o=" + encodeURIComponent(from) : "");
  frame.style.cssText = "position:fixed;right:20px;bottom:90px;width:min(380px,calc(100vw - 24px));height:min(560px,calc(100vh - 110px));border:0;border-radius:16px;box-shadow:0 8px 32px rgba(0,0,0,.3);z-index:2147483646;display:none;background:#fff";
  frame.setAttribute("allow", "clipboard-write");

  var open = false;
  button.addEventListener("click", function () {
    open = !open;
    frame.style.display = open ? "block" : "none";
    button.setAttribute("aria-label", open ? "Cerrar el chat" : "Abrir el chat");
    button.textContent = open ? "\\u2715" : "\\u{1F4AC}";
    if (open && !frame.getAttribute("data-loaded")) frame.setAttribute("data-loaded", "1");
  });
  document.body.appendChild(frame);
  document.body.appendChild(button);
})();
`;

export function GET(): Response {
  return new Response(SCRIPT, {
    headers: {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
      "Access-Control-Allow-Origin": "*",
    },
  });
}
