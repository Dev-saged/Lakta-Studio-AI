// رابط ثابت لاستوديو لقطة: يحفظ رابط النفق الحالي في KV ويحوّل إليه
// المتغيرات: TOKEN (سر) · ربط KV باسم LAKTA
const PAGE = `<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>استوديو لقطة</title><body style="margin:0;min-height:100dvh;display:grid;place-items:center;background:#0e1b3d;color:#f6ebd0;font:18px system-ui;text-align:center;padding:24px">
<div><h1 style="font-size:28px">الاستوديو متوقف الآن</h1><p>شغّل خلية الاستوديو على Kaggle أو Colab ثم حدّث هذه الصفحة.</p></div></body></html>`;

export default {
  async fetch(req, env) {
    const u = new URL(req.url);
    if (u.pathname === "/__set" && req.method === "POST") {
      if (req.headers.get("Authorization") !== `Bearer ${env.TOKEN}`) return new Response("forbidden", { status: 403 });
      const { url } = await req.json().catch(() => ({}));
      if (!/^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/.test(url || "")) return new Response("bad url", { status: 400 });
      await env.LAKTA.put("url", url);
      return Response.json({ ok: true });
    }
    const target = await env.LAKTA.get("url");
    let alive = false;
    if (target) {
      try {
        const r = await fetch(`${target}/manifest.webmanifest`, { method: "HEAD", signal: AbortSignal.timeout(4000) });
        alive = r.ok;
      } catch {}
    }
    if (!alive) return new Response(PAGE, { status: 503, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
    return Response.redirect(target + u.pathname + u.search, 302);
  },
};
