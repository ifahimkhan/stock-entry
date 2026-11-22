// What each item of a stock's Overview tab means: the text behind the "i" buttons (web/infotip.js).
// The numbers repeat the rules in stock_entry/signals.py, indicators.py, strategy.py and smc.py.

/** The coloured box at the top: where the stock is in the breakout -> wait -> entry cycle. */
export const CYCLE_INFO = {
  label: "What the signal box means",
  title: "Breakout → wait → entry cycle (daily bars)",
  columns: ["Step", "Rule"],
  rows: [
    ["Breakout", "An up day whose close is above the highest close of the previous 4 days."],
    ["Wait", "A down or flat day after a breakout: the pullback. The box counts the waiting days."],
    ["Entry", "The first up day after a wait."],
    ["No signal", "The latest day is none of these."],
    ["Last entry", "The most recent entry day, its close, and the change in price since then."],
  ],
};

/** Indicator tiles, by their label. */
export const TILE_INFO = {
  "RSI 14": {
    title: "RSI 14: strength of recent gains against recent losses (14 days, 0 to 100)",
    lines: ["Below 30: oversold. Above 70: overbought. Between: neutral.",
      "The Breakout screen needs RSI of 40 or more; a Top pick needs 45 or more."],
  },
  "CCI 60": {
    title: "CCI 60: how far the price is from its 60-day average",
    lines: ["Above 100: overbought zone. Below -100: oversold zone.",
      "The CCI up-cross screen needs CCI at or below 0; the Breakout screen needs it at or below 100.",
      "Calculated the way the Mid Cap notebook does it, so values can differ a little from other charts."],
  },
  "CCI EMA 9": {
    title: "CCI EMA 9: the 9-day moving average (EMA) of the CCI",
    lines: ["CCI above this line means momentum is turning up.",
      "CCI up-cross screen: CCI crossed above it today, by 4 points or more.",
      "Breakout screen: CCI is above it by 2 points or more."],
  },
  "P/B ratio": {
    title: "P/B ratio: the market price of a share against its book value",
    lines: ["P/B = market price per share ÷ book value per share (BVPS).",
      "Price: the latest close. Book value per share: from Yahoo Finance, the company's latest reported figure.",
      "When Yahoo gives no book value: BVPS = (total assets − total liabilities) ÷ shares outstanding, from the balance sheet.",
      "A negative P/B means the liabilities are larger than the assets (negative book value). It is not a cheap stock.",
      "A dash means neither figure is available."],
  },
  "ATR %": {
    title: "ATR %: the average daily range of the last 14 days, as a percent of the price",
    lines: ["It shows how much the stock usually moves in a day.",
      "Up to 3%: Low. Above 3% and up to 5%: High. Above 5%: Very high."],
  },
  "Close > EMA 7": {
    title: "Close > EMA 7: is the close above its 7-day moving average (EMA)?",
    lines: ["Pass means the short-term trend is up.", "Both screens (CCI up-cross and Breakout) need a Pass."],
  },
  "3-rule candle": {
    title: "3-rule candle: a strong up candle",
    lines: ["Pass when all three hold: high above the previous day's high, low above the previous day's low, "
      + "close above the previous day's high.",
      "The Breakout screen needs it. It is also the candle that gives Entry1 and Entry2 in the Swings tabs."],
  },
  "Above prev 5 closes": {
    title: "Above prev 5 closes: a closing breakout",
    lines: ["Pass when today's close is above the highest close of the previous 5 days.", "The Breakout screen needs it."],
  },
  "Screens passed": {
    title: "Screens passed: which of the two notebook screens the latest day passes",
    columns: ["Screen", "Needs all of"],
    rows: [
      ["CCI up-cross", "CCI crossed above its EMA 9 today by 4 or more, CCI at or below 0, close above EMA 7, debt/equity up to 0.4."],
      ["Breakout", "Close above the previous 5 closes, CCI above its EMA 9 by 2 or more, CCI up to 100, RSI 40 or more, "
        + "3-rule candle, close above EMA 7, debt/equity up to 0.3."],
      ["Top pick", "Breakout, plus small cap, RSI 45 or more, CCI at or below 0, debt/equity up to 0.2."],
      ["Note", "When debt/equity or market cap is not known, that one check is skipped."],
    ],
  },
  "Internal trend": {
    title: "Internal trend: short-term market structure (5-bar swings)",
    lines: ["Bullish after price last closed above a short-term swing high; Bearish after it closed below a swing low."],
  },
  "Swing trend": {
    title: "Swing trend: larger market structure (50-bar swings)",
    lines: ["The same idea as the internal trend, on bigger swings, so it changes less often."],
  },
  "Structure today": {
    title: "Structure today: did the latest day break a swing level?",
    columns: ["Text", "Meaning"],
    rows: [
      ["BOS up / down", "Break of structure: price broke a swing level in the direction of the trend (the trend continues)."],
      ["CHoCH up / down", "Change of character: price broke a swing level against the trend (a possible turn)."],
      ["(internal) / (swing)", "Which structure broke: the 5-bar one or the 50-bar one."],
      ["No break", "Nothing broke on the latest day."],
    ],
  },
  "EMA 9 / 20 signal": {
    title: "EMA 9 / 20 signal: the latest crossover of the two moving averages",
    lines: ["Buy: EMA 9 crossed above EMA 20. Sell: EMA 9 crossed below EMA 20.", "The date is the day of that crossover."],
  },
  "Active bullish SPV": {
    title: "Active bullish SPV: the latest bullish SPV zone that still holds",
    lines: ["SPV: an order block candle (the last opposite candle before a structure break) that also starts a fair value gap.",
      "Active: no later day has traded below the bottom of the zone. The date is the day of that candle.",
      "The SPV and PV rules were worked out from the MRI Scan indicator's output, so they are a close match, not an exact copy."],
  },
  "SPV high / low": {
    title: "SPV high / low: the top and bottom price of the active SPV zone",
    lines: ["The zone is the range of the SPV candle. A trade below the low ends the zone."],
  },
  "LTP vs SPV": {
    title: "LTP vs SPV: where the last price is against the SPV zone",
    lines: ["Above, Inside or Below the zone.", "The percent is the distance from the nearest edge of the zone, as a share of the price."],
  },
  "Last PV": {
    title: "Last PV: the most recent PV candle and its day",
    lines: ["PV: the first fair value gap candle after the order block of the same structure break.",
      "PV bull follows a break up; PV bear follows a break down."],
  },
};
