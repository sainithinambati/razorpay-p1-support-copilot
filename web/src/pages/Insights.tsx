import { useEffect, useState } from "react";
import { fetchAll, sb } from "../lib";

export default function Insights() {
  const [spikes, setSpikes] = useState<any[]>([]);
  const [sugg, setSugg] = useState<any[]>([]);
  useEffect(() => {
    sb.from("v_payment_failure_spikes").select("*").then(({ data }) => setSpikes(data || []));
    fetchAll("ai_kb_suggestions").then(setSugg);
  }, []);
  const grouped: Record<string, any[]> = {};
  sugg.forEach((s) => { (grouped[s.topic] ||= []).push(s); });
  return (
    <>
      <section className="card">
        <h2>Payment-failure spikes</h2>
        <p className="muted">Days where 3 or more different merchants reported payment failures for the same method within a 2-day window.
          A signal for Payments Ops; it does not change individual ticket actions.</p>
        {spikes.length === 0 && <p>No spikes detected in the current results.</p>}
        <table><thead><tr><th>Day</th><th>Method</th><th>Merchants</th><th>Tickets</th></tr></thead>
          <tbody>{spikes.map((s, i) => <tr key={i}><td>{s.day}</td><td>{s.method}</td><td>{s.merchants}</td><td><small>{(s.tickets || []).join(", ")}</small></td></tr>)}</tbody></table>
      </section>
      <section className="card">
        <h2>Suggested KB improvements</h2>
        <p className="muted">Written by the system when a ticket had no KB coverage. Humans decide whether to turn them into a KB draft.</p>
        {Object.keys(grouped).length === 0 && <p>No suggestions yet.</p>}
        {Object.entries(grouped).map(([topic, items]) => (
          <div key={topic} className="sugg"><b>{topic}</b> <small className="muted">({items.length} ticket{items.length > 1 ? "s" : ""}: {[...new Set(items.map((x) => x.ticket_id))].join(", ")})</small>
            <ul>{[...new Set(items.map((x) => x.suggestion))].slice(0, 6).map((s) => <li key={s}><small>{s}</small></li>)}</ul></div>))}
      </section>
    </>
  );
}
