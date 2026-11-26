import assert from "node:assert/strict";
import test from "node:test";
import { route } from "../dist/data/router.js";
import { vietcapProvider } from "../dist/data/vietcap/provider.js";
import { dataProviders } from "../dist/engine/factory.js";
import { priceHistoryTool } from "../dist/tools/price-history.js";

const clock = () => new Date("2026-11-26T07:59:59Z");
const window = { from: "2026-11-24", to: "2026-11-26" };
const times = ["23", "24", "25", "26"].map((day) =>
  String(Date.parse(`2026-11-${day}T01:00:00Z`) / 1000),
);

function chart(symbol) {
  return {
    symbol,
    t: times,
    o: [60_000, 60_500, 61_000, 62_000],
    h: [61_000, 62_000, 62_000, 62_500],
    l: [59_000, 60_000, 60_000, 61_000],
    c: [60_900, 61_000, 61_100, 62_000],
    v: [300, 400, 500, 600],
  };
}

test("the v1 tool routes explicit VN tickers to Vietcap and cites only completed VND bars", async () => {
  const queries = [];
  const providers = dataProviders({
    fetch: async (url, { body }) => {
      assert.equal(new URL(url).hostname, "trading.vietcap.com.vn");
      const request = JSON.parse(body);
      queries.push(request);
      return Response.json([chart(request.symbols[0])]);
    },
  }, clock);
  const { capabilities } = route(providers);
  const tool = priceHistoryTool(capabilities.priceHistory, clock);
  const answer = await tool.run(tool.input.parse({ symbol: "security:VHM.VN", period: "1mo" }), {
    signal: new AbortController().signal,
  });
  const result = tool.output.parse(answer.payload);
  assert.equal(answer.provenance.source, "Vietcap");
  assert.equal(answer.provenance.as_of, "2026-11-25");
  assert.equal(answer.provenance.syncs, undefined);
  assert.equal(result.currency, "VND");
  assert.equal(result.closes.at(-1), 61_100);
  assert.deepEqual(queries.map((q) => q.symbols), [["VHM"]]);
  assert.equal(queries[0].to, Date.parse("2026-11-26T07:59:59Z") / 1000);

  const pair = await capabilities.priceHistory.dailyPair(["VIC.VN", "VNINDEX"], window);
  assert.equal(pair.source, "Vietcap");
  assert.equal(pair.data[0].coverage.through, "2026-11-25");
  assert.equal(pair.data[1].instrument.classification.instrument_type, "index");
  assert.equal(pair.data[1].instrument.currency, null);
  assert.equal(pair.data[0].earlier_end, "2026-11-23");
  assert.equal(pair.data[0].bars[0].revision, "provisional");
  assert.deepEqual(queries.slice(1).map((q) => q.symbols), [["VIC"], ["VNINDEX"]]);
  await assert.rejects(capabilities.priceHistory.dailyPair(["VIC.VN", "SPY"], window), {
    kind: "unsupported",
  });
  assert.equal(queries.length, 3);
});

test("invalid VN evidence cannot fall back to another listing, and non-VN routing is unchanged", async () => {
  let response = [chart("VIC")];
  let yahooCalls = 0;
  const { capabilities } = route({
    vietcap: vietcapProvider({ now: clock, fetch: async () => Response.json(response) }),
    yahoo: {
      name: "Yahoo",
      capabilities: {
        priceHistory: { daily: async () => { yahooCalls++; return {}; } },
      },
    },
  });
  await assert.rejects(capabilities.priceHistory.daily("VHM.VN", window), /instrument does not match/);
  response = [chart("VHM")];
  response[0].h[2] = 10;
  await assert.rejects(capabilities.priceHistory.daily("VHM.VN", window), /inconsistent OHLCV/);
  response = [chart("VHM")];
  response[0].t = [...times.slice(0, 3), times[2]];
  await assert.rejects(capabilities.priceHistory.daily("VHM.VN", window), /duplicate session/);
  assert.equal(yahooCalls, 0);
  const foreign = await capabilities.priceHistory.daily("AAPL", window);
  assert.equal(foreign.source, "Yahoo");
  assert.deepEqual(foreign.after, []);
  assert.equal(yahooCalls, 1);
});
