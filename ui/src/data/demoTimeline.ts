/**
 * Rolling demo timeline: keeps fixtures fresh across Today / This week / Last week /
 * This month / Last month / Two months ago. Pure + deterministic per call.
 */

export function generateTimeline(): number[] {
  const now = Date.now();
  const d = new Date(now);
  const todayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0).getTime();

  // 1. Today (8 sessions) - anchored to recent local hours, clamped >= todayStart
  const todayOffsets = [2, 8, 25, 60, 120, 180, 240, 300].map((mins) => mins * 60 * 1000);
  const today = todayOffsets.map((delta) => Math.max(todayStart, now - delta));

  // 2. This week (prior days) - ISO week (Monday = 0, Sunday = 6)
  const isoDay = (d.getDay() + 6) % 7; // Mon=0 ... Sun=6
  const currWeekStart = todayStart - isoDay * 24 * 3600 * 1000;
  const thisWeek: number[] = [];

  for (let i = 1; i <= isoDay; i++) {
    const dayDate = todayStart - i * 24 * 3600 * 1000;
    thisWeek.push(dayDate + (14 * 3600 + 20 * 60) * 1000);
    thisWeek.push(dayDate + (10 * 3600 + 45 * 60) * 1000);
  }

  while (thisWeek.length < 6) {
    const extraD = thisWeek.length + 1;
    thisWeek.push(currWeekStart - extraD * 24 * 3600 * 1000 - 4 * 3600 * 1000);
  }

  // 3. Last week (strictly Monday to Sunday of previous week)
  const lastWeekStart = currWeekStart - 7 * 24 * 3600 * 1000;
  const lastWeek: number[] = [
    lastWeekStart + 6 * 24 * 3600 * 1000 + (16 * 3600 + 30 * 60) * 1000,
    lastWeekStart + 5 * 24 * 3600 * 1000 + (11 * 3600 + 15 * 60) * 1000,
    lastWeekStart + 4 * 24 * 3600 * 1000 + (15 * 3600 + 40 * 60) * 1000,
    lastWeekStart + 3 * 24 * 3600 * 1000 + (9 * 3600 + 20 * 60) * 1000,
    lastWeekStart + 2 * 24 * 3600 * 1000 + (14 * 3600 + 10 * 60) * 1000,
    lastWeekStart + 1 * 24 * 3600 * 1000 + (10 * 3600 + 50 * 60) * 1000,
    lastWeekStart + 0 * 24 * 3600 * 1000 + (13 * 3600 + 25 * 60) * 1000,
  ];

  // 4. Earlier this month
  const firstOfMonth = new Date(d.getFullYear(), d.getMonth(), 1, 0, 0, 0, 0).getTime();
  const thisMonthEarly: number[] = [];
  if (d.getDate() > 7) {
    for (const dayNum of [2, 4, 6]) {
      if (dayNum < d.getDate() - 7) {
        thisMonthEarly.push(new Date(d.getFullYear(), d.getMonth(), dayNum, 14, 0).getTime());
      }
    }
  }
  if (thisMonthEarly.length === 0) {
    thisMonthEarly.push(
      lastWeekStart - 2 * 24 * 3600 * 1000 - 3 * 3600 * 1000,
      lastWeekStart - 4 * 24 * 3600 * 1000 - 7 * 3600 * 1000
    );
  }

  // 5. Last month (strictly inside last month)
  const lastMonthEnd = firstOfMonth - 1;
  const lastMonthObj = new Date(lastMonthEnd);
  const lYear = lastMonthObj.getFullYear();
  const lMonth = lastMonthObj.getMonth();
  const lastMonth: number[] = [
    new Date(lYear, lMonth, 5, 14, 0).getTime(),
    new Date(lYear, lMonth, 10, 10, 15).getTime(),
    new Date(lYear, lMonth, 15, 16, 30).getTime(),
    new Date(lYear, lMonth, 20, 12, 45).getTime(),
    new Date(lYear, lMonth, 25, 9, 20).getTime(),
    new Date(lYear, lMonth, 28, 17, 10).getTime(),
  ];

  // 6. Two months ago (comparison baseline)
  const twoMonthsEnd = new Date(lYear, lMonth, 1, 0, 0, 0, 0).getTime() - 1;
  const twoMonthsObj = new Date(twoMonthsEnd);
  const tmYear = twoMonthsObj.getFullYear();
  const tmMonth = twoMonthsObj.getMonth();
  const twoMonths: number[] = [
    new Date(tmYear, tmMonth, 10, 11, 0).getTime(),
    new Date(tmYear, tmMonth, 20, 15, 30).getTime(),
  ];

  return [...today, ...thisWeek, ...lastWeek, ...thisMonthEarly, ...lastMonth, ...twoMonths];
}
