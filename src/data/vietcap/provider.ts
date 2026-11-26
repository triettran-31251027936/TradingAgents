/** Vietcap's public daily chart, in VND per share or index points. */
import { z } from "zod";
import { addDays } from "../../contracts/index.js";
import { bounded } from "../bounded.js";
import type { DailyBar, DailyBars, DataProvider, PriceHistory } from "../capabilities.js";
import { DataError } from "../errors.js";
import { parseInstrument } from "../instrument-key.js";

const SOURCE = "Vietcap";
const CHART = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart";
const COUNT = 1500;
const TIMEOUT_MS = 25_000;
const MAX_BYTES = 4_000_000;
const sessionDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Ho_Chi_Minh" });
const barTime = z.union([z.number(), z.string().regex(/^\d{1,10}$/)]).transform(Number);
const chartResponse = z
  .array(
    z.object({
      symbol: z.string(),
      t: z.array(barTime),
      o: z.array(z.number()),
      h: z.array(z.number()),
      l: z.array(z.number()),
      c: z.array(z.number()),
      v: z.array(z.number()),
    }),
  )
  .max(1);

/** An explicit suffix prevents confusion with identically spelled foreign listings. */
export function vietnamTicker(instrument: string): string | undefined {
  const { kind, id } = parseInstrument(instrument);
  if (kind !== "security") return undefined;
  if (id === "VNINDEX") return id;
  return /^([A-Z][A-Z0-9]{2,8})\.VN$/.exec(id)?.[1];
}

type Window = Parameters<PriceHistory["daily"]>[1];

export function vietcapProvider({
  fetch: fetchChart = fetch,
  now = () => new Date(),
}: {
  fetch?: typeof globalThis.fetch;
  now?: () => Date;
} = {}): DataProvider {
  const daily: NonNullable<NonNullable<DataProvider["capabilities"]["priceHistory"]>["daily"]> = async (
    instrument,
    window,
    call,
  ) => {
    const ticker = vietnamTicker(instrument);
    if (!ticker) throw unavailableInstrument(instrument);
    const instant = now();
    const end = validateWindow(window);
    const cutoff = Math.min(end, instant.getTime());
    const signal = AbortSignal.any([AbortSignal.timeout(TIMEOUT_MS), ...(call?.signal ? [call.signal] : [])]);
    const chart = await dailyChart(fetchChart, ticker, cutoff, signal);
    return dailyBars(chart, ticker, window, instant);
  };

  return {
    name: SOURCE,
    capabilities: {
      priceHistory: {
        daily,
        dailyPair: async ([first, second], window, call) => {
          for (const instrument of [first, second]) {
            if (!vietnamTicker(instrument)) throw unavailableInstrument(instrument);
          }
          // This endpoint returned an empty array for multiple symbols.
          return [await daily(first, window, call), await daily(second, window, call)];
        },
      },
    },
  };
}

function unavailableInstrument(instrument: string): DataError {
  return new DataError("unsupported", `${instrument}: Vietcap needs a Vietnamese .VN ticker or VNINDEX`, {
    source: SOURCE,
  });
}

function validateWindow({ from, to }: Window): number {
  const local = (date: string) => Date.parse(`${date}T23:59:59+07:00`);
  for (const date of [from, to]) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(local(date))) {
      throw new DataError("invalid", "Expected an ISO calendar date", { source: SOURCE });
    }
    if (sessionDate.format(local(date)) !== date) {
      throw new DataError("invalid", `Invalid calendar date: ${date}`, { source: SOURCE });
    }
  }
  if (from > to) throw new DataError("invalid", "Window start is after its end", { source: SOURCE });
  return local(to);
}

type Chart = z.output<typeof chartResponse>[number];

async function dailyChart(
  fetchChart: typeof fetch,
  ticker: string,
  cutoff: number,
  signal: AbortSignal,
): Promise<Chart> {
  let body: unknown;
  try {
    const response = await fetchChart(CHART, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Referer: new URL(CHART).origin,
      },
      body: JSON.stringify({
        timeFrame: "ONE_DAY",
        symbols: [ticker],
        to: Math.floor(cutoff / 1000),
        countBack: COUNT,
      }),
      signal,
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new DataError(
        response.status === 429 ? "rate_limited" : "unavailable",
        `Vietcap chart returned HTTP ${response.status}`,
        { source: SOURCE, status: response.status },
      );
    }
    body = await bounded(response, MAX_BYTES, () => badChart("response exceeds size limit")).json();
  } catch (error) {
    if (error instanceof DataError) throw error;
    if (signal.aborted && signal.reason?.name === "AbortError") throw signal.reason;
    throw badChart("request failed or response was unreadable");
  }
  const result = chartResponse.safeParse(body);
  if (!result.success) throw badChart("unexpected response schema");
  const chart = result.data[0];
  if (!chart) throw new DataError("no_data", `Vietcap returned no chart for ${ticker}`, { source: SOURCE });
  if (chart.symbol !== ticker) throw badChart("response instrument does not match the request");
  if ([chart.o, chart.h, chart.l, chart.c, chart.v].some((column) => column.length !== chart.t.length)) {
    throw badChart("OHLCV columns have different lengths");
  }
  return chart;
}

function badChart(reason: string): DataError {
  return new DataError("unavailable", `Vietcap daily chart: ${reason}`, { source: SOURCE });
}

function dailyBars(chart: Chart, ticker: string, { from, to }: Window, now: Date): DailyBars {
  const today = sessionDate.format(now);
  const afterClose = now.getTime() >= Date.parse(`${today}T15:00:00+07:00`);
  const bars: DailyBar[] = [];
  let hasOlderBars = false;
  const seen = new Set<string>();
  for (const [index, time] of chart.t.entries()) {
    if (!Number.isSafeInteger(time) || time <= 0 || time > 9_999_999_999) throw badChart("invalid timestamp");
    const date = sessionDate.format(time * 1000);
    if (date > to || date > today || (date === today && !afterClose)) continue;
    if (date < from) {
      hasOlderBars = true;
      continue;
    }
    if (seen.has(date)) throw badChart(`duplicate session ${date}`);
    seen.add(date);
    const open = chart.o[index] as number;
    const high = chart.h[index] as number;
    const low = chart.l[index] as number;
    const close = chart.c[index] as number;
    const volume = chart.v[index] as number;
    if (!(0 < low && low <= Math.min(open, close) && Math.max(open, close) <= high && volume >= 0)) {
      throw badChart(`inconsistent OHLCV for ${ticker} on ${date}`);
    }
    // Vietcap does not publish a revision/finality marker or historical vintages.
    bars.push({ date, open, high, low, close, volume, revision: "provisional" });
  }
  bars.sort((a, b) => a.date.localeCompare(b.date));
  const index = ticker === "VNINDEX";
  return {
    instrument: {
      key: index ? ticker : `${ticker}.VN`,
      currency: index ? null : "VND",
      price_scale: 1,
      classification: { instrument_type: index ? "index" : "stock" },
    },
    price_basis: index
      ? "Vietcap index points, current provider history"
      : "Vietcap current adjustment basis, VND per share; historical bars may differ from traded prices",
    bars,
    coverage: {
      from: bars[0]?.date ?? null,
      through: bars.at(-1)?.date ?? null,
      complete: null,
      missing_sessions: 0,
    },
    earlier_end: hasOlderBars ? addDays(from, -1) : null,
  };
}
