/* Shared, dependency-free scheduling logic for the post-game crew planner. */
(function exposeCrewPlanner(root, factory) {
  const planner = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = planner;
  if (root) root.CrewPlanner = planner;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';

  const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
  const DAY_NAMES = {
    sun: 'Sunday', mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday',
    thu: 'Thursday', fri: 'Friday', sat: 'Saturday',
  };
  const PARTS = {
    am: { hour: 10, label: 'morning' },
    pm: { hour: 14, label: 'afternoon' },
    eve: { hour: 18, label: 'evening' },
  };
  const VALID_SLOT = /^(sun|mon|tue|wed|thu|fri|sat)-(am|pm|eve)$/;

  function normalizedDate(value) {
    const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
    return Number.isFinite(date.getTime()) ? date : null;
  }

  function slotFromDate(value) {
    const date = normalizedDate(value);
    if (!date) return null;
    const part = date.getHours() < 12 ? 'am' : date.getHours() < 17 ? 'pm' : 'eve';
    return `${DAYS[date.getDay()]}-${part}`;
  }

  function nextOccurrence(slot, nowValue = new Date(), minLeadMinutes = 50) {
    if (typeof slot !== 'string' || !VALID_SLOT.test(slot)) return null;
    const now = normalizedDate(nowValue);
    if (!now) return null;
    const [day, part] = slot.split('-');
    const candidate = new Date(
      now.getFullYear(), now.getMonth(), now.getDate(), PARTS[part].hour, 0, 0, 0,
    );
    candidate.setDate(candidate.getDate() + ((DAYS.indexOf(day) - now.getDay() + 7) % 7));
    if (candidate.getTime() <= now.getTime() + Math.max(0, Number(minLeadMinutes) || 0) * 60000) {
      candidate.setDate(candidate.getDate() + 7);
    }
    return candidate;
  }

  function rosterPlayers(players) {
    const seen = new Set();
    return (Array.isArray(players) ? players : []).filter((player, index) => {
      if (!player || typeof player !== 'object') return false;
      const key = Number.isSafeInteger(Number(player.id)) && Number(player.id) > 0
        ? `id:${Number(player.id)}` : `row:${index}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function bestSlot(players, options = {}) {
    const now = normalizedDate(options.now == null ? new Date() : options.now);
    if (!now) return null;
    const minLeadMinutes = options.minLeadMinutes == null ? 50 : options.minLeadMinutes;
    const roster = rosterPlayers(players);
    const hostId = Number(options.hostId);
    const host = Number.isSafeInteger(hostId) && hostId > 0
      ? roster.find((player) => Number(player.id) === hostId) : null;
    const hostSlots = new Set(Array.isArray(host && host.availability)
      ? host.availability.filter((slot) => typeof slot === 'string' && VALID_SLOT.test(slot))
      : []);
    const counts = new Map();
    roster.forEach((player) => {
      const slots = new Set(Array.isArray(player.availability)
        ? player.availability.filter((slot) => typeof slot === 'string' && VALID_SLOT.test(slot))
        : []);
      slots.forEach((slot) => counts.set(slot, (counts.get(slot) || 0) + 1));
    });

    const ranked = [...counts]
      .filter(([slot]) => !hostSlots.size || hostSlots.has(slot))
      .map(([slot, coverage]) => ({
      slot,
      coverage,
      occurrence: nextOccurrence(slot, now, minLeadMinutes),
    })).filter((item) => item.occurrence).sort((a, b) => (
      b.coverage - a.coverage || a.occurrence.getTime() - b.occurrence.getTime()
        || a.slot.localeCompare(b.slot)
    ));

    let winner = ranked[0] || null;
    let usedFallback = false;
    if (!winner && options.fallbackScheduledAt) {
      const fallbackSlot = slotFromDate(options.fallbackScheduledAt);
      const occurrence = nextOccurrence(fallbackSlot, now, minLeadMinutes);
      if (occurrence) {
        winner = { slot: fallbackSlot, coverage: 0, occurrence };
        usedFallback = true;
      }
    }
    if (!winner) return null;
    return {
      slot: winner.slot,
      scheduledAt: winner.occurrence.toISOString(),
      coverage: winner.coverage,
      total: roster.length,
      usedFallback,
    };
  }

  function slotLabel(slot) {
    if (typeof slot !== 'string' || !VALID_SLOT.test(slot)) return '';
    const [day, part] = slot.split('-');
    return `${DAY_NAMES[day]} ${PARTS[part].label}`;
  }

  return { bestSlot, nextOccurrence, slotFromDate, slotLabel };
}));
