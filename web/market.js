// NSE trading-hours check, used to warn that a scan during the session reads live (not closing) prices.

const IST_OFFSET_MINUTES = 330;          // UTC+5:30, no daylight saving
const OPEN_MINUTES = 9 * 60;             // 09:00 IST (pre-open starts at 09:00, trading at 09:15)
const CLOSE_MINUTES = 15 * 60 + 30;      // 15:30 IST
export const MARKET_HOURS_TEXT = "9:00 AM – 3:30 PM IST";

/**
 * True on a weekday between 09:00 and 15:30 IST. Exchange holidays are not known here,
 * so a holiday weekday also counts as open (the warning is then merely cautious).
 * @param {Date} [now]
 * @returns {boolean}
 */
export function isMarketHours(now = new Date()) {
  const ist = new Date(now.getTime() + IST_OFFSET_MINUTES * 60000);
  const day = ist.getUTCDay();
  if (day === 0 || day === 6) return false;
  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  return minutes >= OPEN_MINUTES && minutes < CLOSE_MINUTES;
}
