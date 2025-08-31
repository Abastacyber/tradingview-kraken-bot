import express from "express";

// ---- Config optionnelle pour relayer ailleurs (laisse vide sinon)
const FORWARD_URL = "";           // ex: "https://ton-executor.com/order"
const FORWARD_BEARER = "";        // ex: "abc123"

// ---- App
const app = express();
const PORT = process.env.PORT || 3000;

// 1) On lit d'abord la requête en brut (quelque soit le Content-Type)
app.use(express.raw({ type: "/", limit: "1mb" }));

// Utilitaire: parse "gentil"
function safeParse(buffer) {
  try {
    const s = buffer.toString("utf8").trim();
    if (!s) return { parsed: {}, raw: "" };
    return { parsed: JSON.parse(s), raw: s };
  } catch {
    return { parsed: null, raw: buffer.toString("utf8") };
  }
}

app.get("/health", (_req, res) => res.json({ status: "ok", time: Date.now() }));

app.post("/webhook", async (req, res) => {
  const { parsed, raw } = safeParse(req.body || Buffer.from(""));

  // Log utile
  console.log("=== Webhook reçu ===");
  console.log("Headers:", JSON.stringify(req.headers));
  console.log("Body (raw):", raw);

  // Si JSON invalide, on accepte quand même et on encapsule
  const payload = parsed ?? { rawMessage: raw };

  // Option: relayer
  if (FORWARD_URL) {
    try {
      const headers = { "Content-Type": "application/json" };
      if (FORWARD_BEARER) headers.Authorization = Bearer ${FORWARD_BEARER};
      const r = await fetch(FORWARD_URL, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const txt = await r.text();
      console.log("[Relay] status:", r.status, "body:", txt);
    } catch (e) {
      console.error("[Relay] erreur:", e);
    }
  }

  // Toujours répondre 200 pour ne pas casser l’alerte TV
  res.json({ status: "ok", received: payload });
});

// 404
app.use((_req, res) => res.status(404).json({ status: "not_found" }));

app.listen(PORT, () => console.log(Serveur sur port ${PORT}));
