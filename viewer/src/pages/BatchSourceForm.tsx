/** The "Published batch base URL" form. A GET form sends only its own fields, so the page you're on
 *  (and the model on a profile page) ride along as hidden fields; otherwise Load batch lands on home (#700). */
export function BatchSourceForm({ base, page, model }: { base: string; page: string | null; model: string | null }) {
  return (
    <form method="get" className="batch-source">
      <input type="hidden" name="page" value={page && page !== "home" ? page : "games"} />
      {model && <input type="hidden" name="model" value={model} />}
      <label>Published batch base URL <input name="base" type="url" required defaultValue={base} /></label>
      <button type="submit">Load batch</button>
    </form>
  );
}
