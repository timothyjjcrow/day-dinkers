/* Tournament presentation: explicit participants, results and bracket paths. */
(function exposeTournamentBracket(root, factory) {
  const bracket = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = bracket;
  if (root) root.TournamentBracket = bracket;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
  ));
  const id = (value) => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 0;
  const same = (a, b) => id(a) > 0 && id(a) === id(b);
  const isBronze = (t, m) => t.format !== 'round_robin' && Number(t.total_rounds) > 1
    && Number(m.round) === Number(t.total_rounds) && Number(m.position) === 1;

  function roundLabel(round, total) {
    return ({ 0: 'Final', 1: 'Semifinals', 2: 'Quarterfinals' })[Number(total) - Number(round)] || `Round ${round}`;
  }

  function matchLabel(t, m) {
    if (t.format === 'round_robin') return `Round ${m.round} · Match ${Number(m.position) + 1}`;
    if (isBronze(t, m)) return '3rd place';
    const remaining = Number(t.total_rounds) - Number(m.round);
    if (remaining === 0) return 'Final';
    return `${remaining === 1 ? 'Semifinal' : remaining === 2 ? 'Quarterfinal' : `Round ${m.round} · Match`} ${Number(m.position) + 1}`;
  }

  function resultMeta(m) {
    const aliases = { pending: 'awaiting_confirmation', final: 'confirmed', done: 'confirmed' };
    const raw = String(m.result_state || m.status || 'unreported').toLowerCase();
    const state = aliases[raw] || raw;
    if (state === 'bye') return { state, label: 'Advances with a bye', tone: 'quiet', decided: true };
    if (state === 'void') return { state, label: 'Not played', tone: 'quiet', decided: true };
    if (state === 'confirmed') return { state, label: m.resolution_kind === 'organizer_forfeit' ? 'Forfeit' : 'Final score', tone: 'final', decided: true };
    if (state === 'awaiting_confirmation') return { state, label: 'Awaiting confirmation', tone: 'pending', decided: false };
    if (state === 'disputed') return { state, label: 'Under review', tone: 'review', decided: false };
    return { state: 'unreported', label: !id(m.entry1_id) || !id(m.entry2_id)
      ? 'Awaiting players' : m.scheduled_at ? 'Scheduled' : 'Ready to play', tone: 'quiet', decided: false };
  }

  function feeders(t, m) {
    if (Number(m.round) <= 1) return [];
    const start = isBronze(t, m) ? 0 : Number(m.position) * 2;
    return [0, 1].map((side) => (t.matches || []).find((source) => (
      Number(source.round) === Number(m.round) - 1 && Number(source.position) === start + side
    )));
  }

  function render(t, { selectedRound = 'all', mineOnly = false, formatDateTime = (value) => value } = {}) {
    const matches = Array.isArray(t.matches) ? t.matches : [];
    const entries = Object.fromEntries((t.entries || []).map((entry) => [id(entry.id), entry]));
    const visible = (m) => (selectedRound === 'all' || Number(m.round) === Number(selectedRound))
      && (!mineOnly || same(m.entry1_id, t.my_entry_id) || same(m.entry2_id, t.my_entry_id));
    const connected = t.format !== 'round_robin' && selectedRound === 'all' && !mineOnly;
    const playable = matches.filter((m) => resultMeta(m).state !== 'bye');
    const decided = playable.filter((m) => resultMeta(m).decided).length;
    const bestOfThree = t.game_format === 'best_of_3_11';
    const sideName = (entry) => (entry?.players?.length
      ? entry.players.map((player) => player.display_name).filter(Boolean).join(' & ')
      : entry?.name) || 'Awaiting player';
    const nextMatch = (m) => matches.find((next) => !isBronze(t, next)
      && Number(next.round) === Number(m.round) + 1
      && Number(next.position) === Math.floor(Number(m.position) / 2));

    const matchHtml = (m) => {
      const meta = resultMeta(m);
      const both = id(m.entry1_id) && id(m.entry2_id);
      const sources = feeders(t, m);
      const games = Array.isArray(m.game_scores) ? m.game_scores : [];
      const showGames = bestOfThree && games.length > 0;
      const label = matchLabel(t, m);
      const next = nextMatch(m);
      const destination = t.format === 'round_robin' ? `Round ${m.round}` : isBronze(t, m) ? (meta.state === 'confirmed' ? 'Third place decided' : 'Winner takes 3rd place')
        : next ? `${meta.decided ? 'Advances' : 'Winner'} → ${matchLabel(t, next)}` : meta.state === 'confirmed' ? 'Tournament winner decided' : 'Winner takes the title';
      const sideHtml = (entryId, side) => {
        const entry = entries[id(entryId)];
        const winner = ['confirmed', 'bye'].includes(meta.state) && same(m.winner_entry_id, entryId);
        const missing = meta.state === 'bye' ? 'Bye' : sources[side - 1]
          ? `${isBronze(t, m) ? 'Loser' : 'Winner'} of ${matchLabel(t, sources[side - 1])}` : 'Awaiting player';
        const names = entry?.players?.length
          ? entry.players.map((player) => player.display_name).filter(Boolean) : entry ? [entry.name] : [missing];
        const score = m[`score${side}`];
        return `<div class="bm-side${winner ? ' bm-win' : ''}${same(entryId, t.my_entry_id) ? ' bm-mine' : ''}${entry ? '' : ' bm-placeholder'}" data-side="${side}">
          <span class="bm-seed"${entry?.seed ? ` aria-label="Seed ${Number(entry.seed)}"` : ' aria-hidden="true"'}>${entry?.seed ? `<small>Seed</small>${Number(entry.seed)}` : '·'}</span>
          <span class="bm-name">${names.map((name) => `<span class="bm-player">${escape(name)}</span>`).join('')}${same(entryId, t.my_entry_id) ? '<span class="bm-you">You</span>' : ''}</span>
          <span class="bm-score" aria-label="${winner ? 'Winner. ' : ''}${score != null ? `${bestOfThree ? 'Games won' : 'Points'}: ${Number(score)}` : 'No score'}">${winner ? '<span class="bm-winner-mark" aria-hidden="true">✓</span>' : ''}${score != null ? Number(score) : '—'}</span>
        </div>`;
      };
      const schedule = meta.state !== 'bye' && !meta.decided && (m.scheduled_at || m.court_number)
        ? [m.scheduled_at ? formatDateTime(m.scheduled_at) : '', m.court_number ? `Court ${m.court_number}` : ''].filter(Boolean).join(' · ') : '';
      const gameLedger = showGames ? `<div class="bm-games" aria-label="Points by game">
        <span class="bm-games-label">Game scores</span>
        ${games.map((game, i) => `<span class="bm-game"><small>G${i + 1}</small><b>${Number(game.score1)}–${Number(game.score2)}</b></span>`).join('')}
      </div>` : '';
      const scoreDescription = m.score1 != null && m.score2 != null
        ? `${meta.decided ? 'Score' : 'Provisional score'}: ${Number(m.score1)} to ${Number(m.score2)} ${bestOfThree ? 'games' : 'points'}. ` : '';
      const winnerDescription = ['confirmed', 'bye'].includes(meta.state) && entries[id(m.winner_entry_id)]
        ? `Winner: ${sideName(entries[id(m.winner_entry_id)])}. ` : '';
      const description = `${label}: ${sideName(entries[id(m.entry1_id)])} versus ${sideName(entries[id(m.entry2_id)])}. ${meta.label}. ${scoreDescription}${winnerDescription}Open match details.`;
      return `<div class="bracket-match-slot" data-bracket-node="${id(m.id)}"${connected ? sources.map((source, i) => source ? ` data-bracket-source-${i + 1}="${id(source.id)}"` : '').join('') : ''}>
        <div class="bm competition-bracket-match is-${meta.tone}${both && t.status === 'active' && meta.state === 'unreported' ? ' bm-ready' : ''}${both ? '' : ' bm-tbd'}" ${both ? `data-tmatch="${id(m.id)}" data-result-match="${id(m.id)}" data-match-key="${id(m.id)}" aria-label="${escape(description)}"` : `aria-label="${escape(label)}. ${escape(meta.label)}"`}>
          <div class="bm-heading"><span class="bm-match-label">${escape(label)}</span><span class="bm-state is-${meta.tone}">${escape(meta.label)}</span></div>
          ${bestOfThree ? '<div class="bm-score-heading">Games won</div>' : ''}
          ${sideHtml(m.entry1_id, 1)}${sideHtml(m.entry2_id, 2)}${gameLedger}
          <div class="bm-footer">${schedule ? `<span class="bm-schedule">${escape(schedule)}</span>` : ''}<span class="bm-destination">${escape(meta.state === 'void' ? 'No result recorded' : destination)}</span>${both ? '<span class="bm-open" aria-hidden="true">↗</span>' : ''}</div>
        </div>
      </div>`;
    };

    if (t.format === 'round_robin') {
      const shown = matches.filter(visible);
      if (!shown.length) return '<div class="empty-state">You have no matches in this round.</div>';
      const groups = [...new Set(shown.map((m) => Number(m.round)))].sort((a, b) => a - b);
      return `<div class="tournament-round-robin">${groups.map((round) => `<section><h3 class="rr-round-title">Round ${round}</h3><div class="tournament-match-grid">${shown.filter((m) => Number(m.round) === round).map(matchHtml).join('')}</div></section>`).join('')}</div>`;
    }
    const rounds = [];
    for (let round = 1; round <= Number(t.total_rounds); round++) {
      const roundMatches = matches.filter((m) => Number(m.round) === round && !isBronze(t, m) && visible(m))
        .sort((a, b) => Number(a.position) - Number(b.position));
      if (!roundMatches.length) continue;
      rounds.push({ round, name: roundLabel(round, t.total_rounds), matches: roundMatches });
    }
    const bronze = matches.find((m) => isBronze(t, m) && visible(m));
    if (!rounds.length && !bronze) return '<div class="empty-state">You have no matches in this round.</div>';
    const final = matches.find((m) => Number(m.round) === Number(t.total_rounds) && Number(m.position) === 0);
    const finalDecided = final && resultMeta(final).state === 'confirmed' && id(final.winner_entry_id);
    const places = finalDecided ? [
      ['1st', final.winner_entry_id],
      ['2nd', same(final.winner_entry_id, final.entry1_id) ? final.entry2_id : final.entry1_id],
      ...(bronze && resultMeta(bronze).state === 'confirmed' ? [['3rd', bronze.winner_entry_id]] : []),
    ].filter(([, entryId]) => entries[id(entryId)]) : [];
    const podium = places.length && connected ? `<div class="bracket-placings" aria-label="Confirmed tournament places">${places.map(([place, entryId]) => `<div class="bracket-place"><span>${place}${place === '1st' ? ' · Tournament winner' : ' place'}</span><b>${escape(sideName(entries[id(entryId)]))}</b></div>`).join('')}</div>` : '';
    return `<section class="tournament-bracket-view${connected ? ' is-connected' : ' is-filtered'}" aria-label="Tournament bracket">
      ${podium}
      <div class="bracket-overview"><div><h3>${t.status === 'completed' ? 'Final results' : 'Tournament bracket'}</h3><p>${decided} of ${playable.length} matches decided${rounds.length > 1 ? ' · Follow each round below' : ''}</p></div><span class="bracket-progress" aria-hidden="true" style="--bracket-progress:${playable.length ? 100 * decided / playable.length : 0}%"></span></div>
      ${rounds.length > 1 ? `<nav class="bracket-round-nav" aria-label="Jump to a round">${rounds.map((r) => `<button type="button" data-bracket-round="${r.round}">${escape(r.name)}<span aria-hidden="true">→</span></button>`).join('')}</nav>` : ''}
      <div class="bracket" role="region" aria-label="Tournament bracket. Scroll horizontally to follow winners through the rounds." tabindex="0">
        <div class="bracket-track">${connected && rounds.length > 1 ? '<svg class="bracket-connectors" aria-hidden="true"></svg>' : ''}
          ${rounds.map((r) => `<section class="bracket-round" data-bracket-column="${r.round}" aria-label="${escape(r.name)}">
            <div class="bracket-round-title"><b>${escape(r.name)}</b><span>${r.matches.length} match${r.matches.length === 1 ? '' : 'es'}</span></div>
            <div class="bracket-round-matches">${r.matches.map(matchHtml).join('')}</div>
          </section>`).join('')}
        </div>
      </div>
      ${bronze ? `<section class="bracket-placement" aria-label="Third-place playoff"><div class="bracket-placement-heading"><h4>Third-place playoff</h4><p>${resultMeta(bronze).state === 'confirmed' ? 'The match that decided third place.' : 'The two other semifinalists play for third place.'}</p></div>${matchHtml(bronze)}</section>` : ''}
    </section>`;
  }

  function bind(root) {
    const bracket = root.querySelector('.bracket');
    const track = bracket?.querySelector('.bracket-track');
    if (!track) return () => {};
    const svg = track.querySelector('.bracket-connectors');
    let frame;
    let stopped = false;
    const draw = () => {
      if (stopped || !svg || !track.isConnected || !track.offsetWidth) return;
      const box = track.getBoundingClientRect();
      svg.setAttribute('width', String(track.scrollWidth));
      svg.setAttribute('height', String(track.offsetHeight));
      svg.setAttribute('viewBox', `0 0 ${track.scrollWidth} ${track.offsetHeight}`);
      const paths = [];
      track.querySelectorAll('[data-bracket-node]').forEach((target) => {
        for (const side of [1, 2]) {
          const sourceId = target.getAttribute(`data-bracket-source-${side}`);
          if (!sourceId) continue;
          const source = track.querySelector(`[data-bracket-node="${Number(sourceId)}"]`);
          const targetSide = target.querySelector(`[data-side="${side}"]`);
          if (!source || !targetSide) continue;
          const a = (source.querySelector('.bm-win') || source.querySelector('.bm')).getBoundingClientRect();
          const b = targetSide.getBoundingClientRect();
          const x1 = a.right - box.left, y1 = a.top + a.height / 2 - box.top;
          const x2 = b.left - box.left, y2 = b.top + b.height / 2 - box.top;
          const mid = x1 + (x2 - x1) / 2;
          paths.push(`<path d="M${x1},${y1}H${mid}V${y2}H${x2}"${source.querySelector('.bm-win') ? ' class="is-decided"' : ''}/>`);
        }
      });
      svg.innerHTML = paths.join('');
    };
    const queue = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(draw); };
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(queue) : null;
    observer?.observe(track);
    window.addEventListener('resize', queue);
    const jumps = [...root.querySelectorAll('[data-bracket-round]')];
    const handlers = jumps.map((button) => {
      const handler = () => {
        const column = track.querySelector(`[data-bracket-column="${Number(button.dataset.bracketRound)}"]`);
        if (!column) return;
        const behavior = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
        bracket.scrollTo({ left: column.offsetLeft, behavior });
        const first = column.querySelector?.('.bracket-match-slot');
        const modal = bracket.closest?.('.modal');
        if (first && modal) {
          const bounds = modal.getBoundingClientRect();
          const match = first.getBoundingClientRect();
          const action = modal.querySelector('.competition-actions');
          const inset = (modal.querySelector('.modal-head')?.offsetHeight || 60)
            + (action && window.getComputedStyle(action).position === 'sticky' ? action.offsetHeight : 0) + 18;
          if (match.top < bounds.top + inset || match.bottom > bounds.bottom - 18) {
            modal.scrollBy({ top: match.top - bounds.top - inset, behavior });
          }
        }
      };
      button.addEventListener('click', handler);
      return handler;
    });
    queue();
    return () => {
      stopped = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener('resize', queue);
      jumps.forEach((button, i) => button.removeEventListener('click', handlers[i]));
    };
  }
  return { render, bind, roundLabel, matchLabel, resultMeta };
}));
