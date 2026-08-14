/**
 * Contact form endpoint, replacing Netlify Forms.
 *
 * Netlify used to intercept the form POST automatically because of the
 * `data-netlify="true"` attribute. Vercel has no equivalent built-in form
 * backend, so the frontend now POSTs JSON here instead (see the fetch call
 * in index.html's wireContact()).
 *
 * This currently just validates the payload and logs it — wire the TODO
 * below up to whatever you want to receive it (email, Slack, a database).
 * A few drop-in options:
 *   - Resend (https://resend.com) - a few lines with their Node SDK
 *   - SendGrid (https://sendgrid.com)
 *   - A Slack incoming webhook (a single fetch() POST, no SDK needed)
 *
 * Env vars for whichever you pick should be set in the Vercel dashboard
 * under Project Settings -> Environment Variables, not committed here.
 */
module.exports = async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method !== "POST") {
    res.status(405).json({ error: "Method not allowed" });
    return;
  }

  let body = req.body;
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch (e) {
      res.status(400).json({ error: "Invalid JSON" });
      return;
    }
  }
  body = body || {};

  const name = (body.name || "").toString().trim();
  const email = (body.email || "").toString().trim();
  const topic = (body.topic || "Something else").toString().trim();
  const message = (body.message || "").toString().trim();

  const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email);
  if (!name || !emailOk || message.length < 10) {
    res.status(400).json({ error: "Missing or invalid fields" });
    return;
  }

  try {
    // TODO: replace this with a real notification. For now the submission
    // just goes to the function's logs (visible in the Vercel dashboard
    // under your project's Logs tab, or in `vercel dev` output locally).
    console.log("[contact] submission", { name, email, topic, message });

    res.status(200).json({ ok: true });
  } catch (err) {
    console.error("[contact] failed to process submission", err);
    res.status(500).json({ error: "Server error" });
  }
};
