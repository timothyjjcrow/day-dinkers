/* Venue owner presentation and collection edits. No network or account state. */
(function expose(root, factory) {
  const workspace = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = workspace;
  if (root) root.VenueWorkspace = workspace;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const panels = ['details', 'schedule', 'offerings', 'booking'];
  const entryId = (value) => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 0;

  function state(business, verification) {
    const role = String(business.manager_role || (business.is_owner ? 'owner' : 'viewer')).toLowerCase();
    const verified = verification === 'verified';
    const review = String(business.content_review_status || 'approved');
    const publicNow = business.is_public === true || (business.is_public == null && verified && business.published === true && review === 'approved' && !business.suspended);
    if (business.is_public === false && verified && business.published && review === 'approved' && !business.suspended) return { publicNow, title: 'Listing is not visible to players', copy: 'Check your court listing and venue status before sharing it.', tool: 'verification', action: 'Review visibility' };
    if (business.suspended) return { publicNow, title: 'Publishing paused', copy: 'Review the decision before making this venue visible again.', tool: role === 'owner' ? 'ownership' : 'verification', action: 'Review status' };
    if (verification === 'rejected') return { publicNow, title: 'Your claim needs attention', copy: 'Review the feedback, then update your claim.', tool: 'verification', action: 'Review feedback' };
    if (!verified) return { publicNow, title: verification === 'pending' ? 'Your claim is being reviewed' : 'Confirm your management role', copy: 'Your venue stays private while we review your claim. You can prepare its details now.', tool: 'verification', action: 'View verification' };
    if (review !== 'approved') return { publicNow, title: review === 'pending' ? 'Changes are being reviewed' : 'A change needs attention', copy: publicNow ? 'Your approved listing stays live. These edits are saved for review.' : 'Your draft is saved. Players will see it after approval and publishing.', tool: 'revisions', action: 'View changes' };
    if (business.has_unpublished_changes) return { publicNow, title: 'Changes approved · ready to publish', copy: publicNow ? 'Players still see your previous approved version.' : 'Review your saved draft, then publish it.', tool: ['owner', 'admin'].includes(role) ? 'publish' : 'preview', action: 'Publish changes' };
    if (publicNow) return { publicNow, title: 'Live on the court map', copy: 'Players can see your venue details and booking options.', tool: 'preview', action: 'View player listing' };
    return { publicNow, title: 'Ready when you are', copy: 'Check your listing, then publish it for players.', tool: ['owner', 'admin'].includes(role) ? 'publish' : 'preview', action: ['owner', 'admin'].includes(role) ? 'Publish venue' : 'Preview listing' };
  }

  function bindDetailsEditor(modal, business, { formUX, icon, verified, publicNow, baseline = business }) {
    const form = modal.querySelector('#business-details-form');
    const sectionButtons = [...modal.querySelectorAll('[data-venue-section]')];
    const selectSection = (key, focus = false) => {
      sectionButtons.forEach((button) => {
        const selected = button.dataset.venueSection === key;
        button.setAttribute('aria-selected', String(selected));
        button.tabIndex = selected ? 0 : -1;
        modal.querySelector(`#venue-field-${button.dataset.venueSection}`).hidden = !selected;
        if (selected && focus) button.focus();
      });
    };
    const setView = (view) => {
      form.dataset.view = view;
      modal.querySelectorAll('[data-venue-editor-view]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.venueEditorView === view)));
    };
    sectionButtons.forEach((button, index) => {
      button.addEventListener('click', () => selectSection(button.dataset.venueSection));
      button.addEventListener('keydown', (event) => {
        const next = event.key === 'ArrowRight' ? (index + 1) % sectionButtons.length : event.key === 'ArrowLeft' ? (index + sectionButtons.length - 1) % sectionButtons.length : event.key === 'Home' ? 0 : event.key === 'End' ? sectionButtons.length - 1 : null;
        if (next !== null) { event.preventDefault(); selectSection(sectionButtons[next].dataset.venueSection, true); }
      });
    });
    modal.querySelectorAll('[data-venue-editor-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.venueEditorView)));
    const reveal = (target) => {
      const section = target?.closest('[data-venue-field-section]');
      if (section) selectSection(section.dataset.venueFieldSection);
      setView('edit');
    };
    const originalError = formUX.showError;
    formUX.showError = (message, target) => { if (target) reveal(target); originalError(message, target); };
    const readPreview = () => ({ ...business, ...Object.fromEntries(['name', 'description', 'announcement', 'hours', 'timezone', 'amenities', 'phone', 'email', 'website-url', 'logo-url'].map((key) => [key.replaceAll('-', '_'), modal.querySelector(`#business-${key}`).value.trim()])) });
    formUX.isDirty = () => {
      const fields = readPreview();
      fields.amenities = String(fields.amenities || '').split(',').map(value => value.trim()).filter(Boolean);
      return Object.keys(changedDetails(baseline, Object.fromEntries(['name', 'description', 'announcement', 'hours', 'timezone', 'amenities', 'phone', 'email', 'website_url', 'logo_url'].map(key => [key, fields[key]])))).length > 0;
    };
    const syncPreview = () => {
      const dirty = formUX.isDirty();
      modal.querySelector('#venue-details-preview').innerHTML = preview(readPreview(), icon);
      modal.querySelector('#venue-details-dirty').textContent = dirty ? 'Unsaved changes' : 'All changes saved';
      const sensitive = ['name', 'phone', 'email'].some((key) => modal.querySelector(`#business-${key}`).value.trim() !== String(baseline[key] || ''))
        || modal.querySelector('#business-website-url').value.trim() !== String(baseline.website_url || '')
        || modal.querySelector('#business-logo-url').value.trim() !== String(baseline.logo_url || '');
      modal.querySelector('#venue-details-save-impact').textContent = sensitive && verified
        ? (publicNow ? 'Saved as a draft for review. Your approved listing stays live.' : 'Saved as a draft for review before publishing.')
        : business.has_unpublished_changes ? 'Saved to your draft. Your approved listing stays live.' : (business.is_public === true || (business.is_public == null && publicNow)) ? 'Saved changes appear on your listing.' : 'Only managers can see this preview. Your listing is private.';
    };
    form.addEventListener('input', syncPreview);
    form.addEventListener('change', syncPreview);
    syncPreview.reveal = reveal;
    syncPreview();
    return syncPreview;
  }

  function changedDetails(original, fields) {
    return Object.fromEntries(Object.entries(fields).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(original[key] ?? (Array.isArray(value) ? [] : ''))));
  }

  function assertCollectionUnchanged(fresh, original) {
    const content = (items) => items.map((item) => Object.fromEntries(Object.entries(item).filter(([key]) => !['created_at', 'updated_at', 'source_updated_at', 'availability_updated_at', 'availability_fresh', 'availability_label', 'availability_as_of', 'freshness', 'sort_order'].includes(key)).sort(([a], [b]) => a.localeCompare(b)))).sort((a, b) => entryId(a.id) - entryId(b.id));
    if (JSON.stringify(content(fresh)) !== JSON.stringify(content(original))) throw new Error('This list changed elsewhere. Your edits are still here. Close and reopen it to use the latest version.');
  }

  function mergeItem(items, updated, original = null) {
    const next = items.map((item) => ({ ...item }));
    if (!original) {
      if (next.length >= 100) throw new Error('This list can contain up to 100 items. Remove an old item before adding another.');
      const clean = { ...updated }; delete clean.id;
      next.push(clean);
    } else {
      const index = next.findIndex((item) => entryId(item.id) === entryId(original.id));
      if (!entryId(original.id) || index < 0) throw new Error('This item was removed elsewhere. Close this editor and refresh your venue.');
      const keys = Object.keys(original).filter((key) => !['created_at', 'updated_at', 'source_updated_at', 'availability_updated_at', 'availability_fresh', 'availability_label', 'availability_as_of', 'freshness', 'sort_order'].includes(key));
      if (keys.some((key) => JSON.stringify(next[index][key]) !== JSON.stringify(original[key]))) {
        throw new Error('This item changed elsewhere. Your edits are still here; close this editor and refresh your venue before trying again.');
      }
      if (updated === null) next.splice(index, 1);
      else {
        const edits = Object.fromEntries(Object.entries(updated).filter(([key]) => !['created_at', 'updated_at', 'source_updated_at', 'availability_updated_at', 'availability_fresh', 'availability_label', 'availability_as_of', 'freshness', 'sort_order'].includes(key)));
        next[index] = { ...next[index], ...edits, id: original.id };
      }
    }
    return next;
  }

  function sessionIsCurrent(item, now = new Date()) {
    if (!['dated', 'date_range'].includes(item.recurrence)) return true;
    let today;
    try {
      const parts = new Intl.DateTimeFormat('en-US', { timeZone: item.timezone || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
      const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
      today = `${values.year}-${values.month}-${values.day}`;
    } catch { today = now.toISOString().slice(0, 10); }
    const end = item.recurrence === 'dated' ? item.event_date : item.end_date;
    return !!end && end >= today;
  }

  function timezoneOptions(selected = '') {
    const preferred = ['America/Los_Angeles', 'America/Denver', 'America/Phoenix', 'America/Chicago', 'America/New_York', 'Pacific/Honolulu', 'America/Anchorage', 'UTC'];
    const zones = [...new Set([...preferred, selected, ...(Intl.supportedValuesOf?.('timeZone') || [])])].filter(Boolean);
    return `<option value="">Choose venue time zone</option>${zones.map(zone => `<option value="${escape(zone)}" ${zone === selected ? 'selected' : ''}>${escape(zone.replaceAll('_', ' ').replace('/', ' · '))}</option>`).join('')}`;
  }

  const occurrenceEditableFields = ['title', 'kind', 'offering_id', 'start_time', 'end_time', 'timezone', 'skill_level', 'booking_url', 'capacity', 'spots_remaining', 'status', 'location_note', 'instructor', 'active', 'availability_checked'];
  async function saveOccurrence(business, original, changes, {scope = 'this_date', request, resolveConflicts}) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const agenda = await request(`/businesses/${business.id}/agenda?draft=1&from=${original.occurrence_on}&to=${original.occurrence_on}`);
      const latest = agenda.items.find(item => Number(item.schedule_item_id) === Number(original.schedule_item_id));
      if (!latest) throw new Error('This session date changed or was removed. Your edits are still here; review the current agenda.');
      let result = reconcileProfile(original, changes, latest);
      if (result.conflicts.length) {
        const choices = await resolveConflicts(result.conflicts);
        if (!choices) throw new Error('Your edits are still here. Nothing was saved.');
        result = reconcileProfile(original, changes, latest, choices);
      }
      if (!Object.keys(result.value).length) return await request(`/businesses/${business.id}`);
      try {
        return await request(`/businesses/${business.id}/schedule/${original.schedule_item_id}/occurrences/${original.occurrence_on}`, {method: 'PATCH', headers: contentHeaders(agenda), body: JSON.stringify({scope, changes: result.value})});
      } catch (error) { if (error.status !== 412 || attempt === 2) throw error; }
    }
  }

  function render(b, { icon, day, time, workspace, canEdit, panel = 'details', agendaView = 'week' }) {
    const active = panels.includes(panel) ? panel : 'details';
    const e = escape;
    const tool = (name, label, primary = false) => `<button type="button" class="btn btn-${primary ? 'primary' : 'secondary'}" data-business-tool="${name}" ${canEdit ? '' : 'disabled'}>${e(label)}</button>`;
    const nav = [['details', 'building', 'Venue'], ['schedule', 'calendar', 'Schedule'], ['offerings', 'target', 'Services'], ['booking', 'link', 'Booking']];
    const fact = (label, value) => `<div><dt>${e(label)}</dt><dd>${value ? e(value) : '<span class="venue-missing">Not added yet</span>'}</dd></div>`;
    const panelHead = (title, copy, action) => `<div class="venue-panel-heading"><div><h3>${e(title)}</h3><p>${e(copy)}</p></div>${action}</div>`;
    const editorActions = (kind, item) => canEdit ? `<div class="venue-item-actions"><button type="button" class="btn btn-secondary btn-sm" data-venue-edit="${kind}" data-item-id="${entryId(item.id)}" ${item.occurrence_on ? `data-occurrence-on="${item.occurrence_on}"` : ''} aria-label="Edit ${e(item.title || item.name)}">${icon('edit')} ${item.occurrence_on ? 'Edit date' : 'Edit'}</button>${item.occurrence_on ? `<button type="button" class="btn btn-secondary btn-sm" data-venue-duplicate="${entryId(item.id)}" data-occurrence-on="${item.occurrence_on}">Duplicate</button>` : ''}<button type="button" class="venue-remove" data-venue-remove="${kind}" data-item-id="${entryId(item.id)}" ${item.occurrence_on ? `data-occurrence-on="${item.occurrence_on}"` : ''} aria-label="${item.occurrence_on ? 'Cancel date for' : 'Remove'} ${e(item.title || item.name)}">${icon('trash')}</button></div>` : '';
    const visibility = (item) => item.active === false ? 'Hidden' : (item.status === 'completed' || (item.recurrence && !sessionIsCurrent(item))) ? 'Past session · no longer listed' : b.has_unpublished_changes ? 'Saved draft · awaiting publication' : workspace.publicNow ? 'On your listing' : 'Saved · listing private';
    const shownSchedule = agendaView === 'patterns' ? (b.schedule || []) : (b.schedule_occurrences || b.schedule || []);
    const schedule = shownSchedule.map((item) => {
      const date = item.recurrence === 'dated' ? new Date(`${item.event_date}T12:00:00`).toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'}) : day(item.day_of_week ?? item.day);
      const range = item.recurrence === 'date_range' ? [item.start_date, item.end_date].filter(Boolean).join(' to ') : '';
      const state = String(item.status || 'scheduled').replace(/_/g, ' ');
      return `<article class="venue-content-item"><div class="venue-session-date"><b>${e(date || 'Date not set')}</b><span>${item.occurrence_on ? item.is_exception ? 'Updated date' : 'Scheduled date' : item.recurrence === 'dated' ? 'One-time' : 'Repeats'}</span></div><div class="venue-item-main"><h4>${e(item.title || 'Untitled session')}</h4><p class="venue-session-time">${e(time(item.start_time))} – ${e(time(item.end_time))}</p><p>${e([item.skill_level, item.location_note, item.capacity ? `${item.capacity} places` : ''].filter(Boolean).join(' · '))}</p>${item.availability_label ? `<p>${e(item.availability_label)}</p>` : ''}${item.booking_url ? `<p>Registration: ${e(registrationHost(item.booking_url))}</p>` : ''}${item.time_warning ? `<p role="alert">${e(item.time_warning)}</p>` : ''}${range ? `<p>${e(range)}</p>` : ''}<div class="venue-item-meta"><span>${e(visibility(item))}</span>${state !== 'scheduled' ? `<span class="venue-item-status">${e(state)}</span>` : ''}<span>${e(item.timezone || '')}</span></div></div>${editorActions('schedule', item)}</article>`;
    }).join('');
    const offerings = (b.offerings || []).map((item) => `<article class="venue-content-item"><span class="venue-service-icon" aria-hidden="true">${icon('target')}</span><div class="venue-item-main"><h4>${e(item.name || 'Untitled service')}</h4><p>${e(item.description || '')}</p><p class="venue-session-time">${e([item.price_text, item.duration_minutes ? `${item.duration_minutes} min` : ''].filter(Boolean).join(' · '))}</p><div class="venue-item-meta"><span>${e(visibility(item))}</span><span>${e(String(item.category || 'other').replace(/_/g, ' '))}</span></div></div>${editorActions('offerings', item)}</article>`).join('');
    const empty = (name, copy) => `<div class="venue-content-empty">${icon(name)}<p>${e(copy)}</p></div>`;
    return `<nav class="venue-owner-tabs" role="tablist" aria-label="Manage your venue">${nav.map(([key, mark, label]) => `<button type="button" id="venue-tab-${key}" role="tab" aria-selected="${key === active}" aria-controls="venue-panel-${key}" tabindex="${key === active ? 0 : -1}" data-venue-panel="${key}">${icon(mark)}<span>${label}</span></button>`).join('')}</nav>
      <section class="venue-owner-panel" id="venue-panel-details" role="tabpanel" aria-labelledby="venue-tab-details" ${active === 'details' ? '' : 'hidden'}>
        ${panelHead('Your player listing', '', tool('details', 'Edit venue details', true))}
        <div class="venue-overview-studio"><div class="venue-overview-content">${preview(b, icon, { saved: true, publicNow: workspace.publicNow })}</div><div class="venue-overview-actions">
          <p class="venue-section-label">MAKE IT YOURS</p>
          ${[['details', 'edit', 'About & updates', b.announcement ? 'Announcement added' : 'Introduce your venue'], ['visit', 'clock', 'Hours & amenities', b.hours ? 'Visiting information added' : 'Help players plan a visit'], ['contact', 'phone', 'Contact & branding', b.email || b.phone ? 'Contact details added' : 'Add your contact details']].map(([name, mark, label, copy]) => `<button type="button" class="venue-edit-entry" data-business-tool="${name}" ${canEdit ? '' : 'disabled'}><span>${icon(mark)}</span><div><b>${label}</b><small>${copy}</small></div>${icon('chevron-right')}</button>`).join('')}
          <div class="venue-owner-tip">${icon('shield')}<span>${workspace.publicNow ? 'You manage this venue’s official information.' : 'Your edits stay private until this venue is approved and published.'}</span></div>
        </div></div>
      </section>
      <section class="venue-owner-panel" id="venue-panel-schedule" role="tabpanel" aria-labelledby="venue-tab-schedule" ${active === 'schedule' ? '' : 'hidden'}>
        ${panelHead('Sessions & events', b.schedule_range ? `${new Date(b.schedule_range.from + 'T12:00:00').toLocaleDateString([], {month:'short',day:'numeric'})} – ${new Date(b.schedule_range.to + 'T12:00:00').toLocaleDateString([], {month:'short',day:'numeric'})}` : `${(b.schedule || []).length} saved sessions`, tool('add-session', 'Add session', true))}
        <div class="quick-times" role="group" aria-label="Schedule view"><button data-venue-agenda-view="week" aria-pressed="${agendaView === 'week'}">Agenda</button><button data-venue-agenda-view="patterns" aria-pressed="${agendaView === 'patterns'}">Repeating sessions</button></div>
        ${agendaView === 'week' ? '<div class="quick-times" role="group" aria-label="Agenda week"><button data-venue-agenda-shift="-7">Previous week</button><button data-venue-agenda-today>Today</button><button data-venue-agenda-shift="7">Next week</button></div>' : ''}
        <div class="venue-content-list">${schedule || empty('calendar', agendaView === 'week' ? 'No sessions on these dates. Choose another week or add a session.' : 'No repeating sessions yet.')}</div>
        ${tool('schedule', 'Import or edit multiple sessions')}<p class="simple-note">Times use each session’s venue timezone. You keep these sessions up to date.</p>
      </section>
      <section class="venue-owner-panel" id="venue-panel-offerings" role="tabpanel" aria-labelledby="venue-tab-offerings" ${active === 'offerings' ? '' : 'hidden'}>
        ${panelHead('Services', `${(b.offerings || []).length} saved services`, tool('add-lesson', 'Add service', true))}
        <div class="venue-content-list">${offerings || empty('target', 'Add coaching, a clinic, a membership, or another service.')}</div>${tool('offerings', 'Edit multiple services')}
      </section>
      <section class="venue-owner-panel" id="venue-panel-booking" role="tabpanel" aria-labelledby="venue-tab-booking" ${active === 'booking' ? '' : 'hidden'}>
        ${panelHead('Booking links', 'Players book directly with you.', tool('booking', 'Edit booking links', true))}
        <div class="venue-overview-grid"><article class="venue-info-card"><span class="venue-service-icon">${icon('calendar')}</span><h4>Court reservations</h4><p>Players choose “Book a court” and finish on your linked booking site.</p><div class="venue-saved-link">${e(b.booking_url || 'No booking page added')}</div></article><article class="venue-info-card"><span class="venue-service-icon">${icon('ticket')}</span><h4>Memberships</h4><p>An optional link for players who want to join your venue.</p><div class="venue-saved-link">${e(b.membership_url || 'No membership page added')}</div></article></div>
      </section>`;
  }

  function bookingForm(business, { icon, head, canEdit }) {
    return `${head}<div class="venue-booking-studio"><p class="venue-editor-context">${escape(business.name)}</p>
      <form id="venue-booking-form" novalidate>
        <h3>Let players book with you</h3><p class="venue-field-intro">Connect your existing booking page.</p>
        <div class="form-field"><label for="venue-booking-url">Court booking link</label><input type="url" id="venue-booking-url" value="${escape(business.booking_url || '')}" placeholder="https://your-booking-page.com" inputmode="url" ${canEdit ? '' : 'disabled'} /></div>
        <div class="venue-booking-handoff" aria-label="Booking action preview"><span>${icon('calendar')}</span><div><b>Book a court</b><small id="venue-booking-destination">Your booking provider</small></div>${icon('external')}</div>
        <details class="simple-disclosure" ${business.membership_url ? 'open' : ''}><summary>Membership link <span>Optional</span></summary><div class="form-field"><label for="venue-membership-url">Membership page</label><input type="url" id="venue-membership-url" value="${escape(business.membership_url || '')}" placeholder="https://yourclub.com/join" inputmode="url" ${canEdit ? '' : 'disabled'} /></div></details>
        <p class="venue-booking-impact">${icon('shield')} Changed links are saved for review. Your approved links stay live until you publish the new version.</p>
        ${canEdit ? '<button type="submit" class="btn btn-primary btn-block" id="venue-booking-save">Save booking links</button>' : '<p class="simple-note">An owner, admin, or editor can update booking links.</p>'}
      </form>
      <details class="simple-disclosure venue-booking-extras"><summary>Schedule &amp; booking tools</summary>
        <button type="button" class="venue-task" id="venue-booking-schedule"><span class="venue-task-icon">${icon('calendar')}</span><span class="row-main"><b>Edit your schedule</b><small>Add sessions or import a spreadsheet</small></span>${icon('chevron-right', 'chev')}</button>
        <button type="button" class="venue-task" id="venue-booking-feed"><span class="venue-task-icon">${icon('refresh')}</span><span class="row-main"><b>Booking &amp; schedule tools</b><small>Import a schedule or get setup help</small></span>${icon('chevron-right', 'chev')}</button>
        <button type="button" class="btn-link" id="venue-booking-help">Ask about my booking system</button>
      </details></div>`;
  }

  function bindBookingPreview(modal) {
    const input = modal.querySelector('#venue-booking-url');
    const sync = () => {
      let destination = 'Add a link to your booking page';
      try { const url = new URL(input.value); if (url.protocol === 'https:' || url.protocol === 'http:') destination = `Continue to ${url.hostname.replace(/^www\./, '')}`; } catch { /* Incomplete URL while typing. */ }
      modal.querySelector('#venue-booking-destination').textContent = destination;
    };
    input.addEventListener('input', sync);
    input.addEventListener('change', sync);
    sync();
  }

  function logoFields(business, { icon, hasManagedLogo }) {
    return `        <details class="simple-disclosure" id="business-branding"><summary>Logo &amp; branding <span>Optional</span></summary>
        <div class="form-field"><label for="business-logo-url">Logo image link</label><input type="url" id="business-logo-url" value="${escape(business.logo_url || '')}" placeholder="https://yourclub.com/logo.png" inputmode="url" /><small class="field-help">Each logo upload saves a draft immediately. Your approved logo stays live until the new version is reviewed and published.</small></div>
        <div class="form-field business-logo-upload">
          <span class="business-file-label" id="business-logo-file-label">Upload a logo</span>
          <div class="business-file-picker" id="business-logo-file-picker" data-state="${hasManagedLogo ? 'success' : 'idle'}" data-idle-icon="camera">
            <input class="business-file-native" type="file" id="business-logo-file" data-no-draft accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" tabindex="-1" aria-hidden="true" />
            <button type="button" class="business-file-button" id="business-logo-file-button" data-file-button aria-labelledby="business-logo-file-label business-logo-file-action" aria-describedby="business-logo-upload-status business-logo-file-help">
              <span class="business-file-button-icon" aria-hidden="true">${icon('camera')}</span>
              <span class="business-file-button-copy"><b id="business-logo-file-action">${hasManagedLogo ? 'Replace uploaded logo' : 'Choose logo image'}</b><small>Browse this device</small></span>
              <span class="business-file-button-cta" aria-hidden="true">Choose</span>
            </button>
            <div class="business-file-feedback" id="business-logo-upload-status" data-file-feedback role="status" aria-live="polite" aria-atomic="true">
              <span class="business-file-state-icon" data-file-state-icon aria-hidden="true">${icon(hasManagedLogo ? 'check-circle' : 'camera')}</span>
              <span class="business-file-feedback-copy"><b data-file-name>${hasManagedLogo ? 'Uploaded logo on file' : business.logo_url ? 'No replacement chosen' : 'No logo selected'}</b><small data-file-meta>${hasManagedLogo ? 'Choose a new image to replace it' : 'PNG, JPEG, or WebP · optimized to 512 KB'}</small></span>
              <span class="business-file-state" data-file-state>${hasManagedLogo ? 'Current' : 'Optional'}</span>
            </div>
          </div>
          <small class="field-help" id="business-logo-file-help">Choose an image up to 12 MB. Third Shot resizes it before upload. Avoid confidential images.</small>
          <button type="button" class="btn-link" id="business-logo-retry" hidden>Retry logo upload</button>
          <p id="business-logo-saved" role="status" aria-live="polite"></p><button type="button" class="btn-link" id="business-logo-history" hidden>Review or undo the saved change</button>
          <button type="button" class="btn-link" id="business-logo-remove" ${hasManagedLogo ? '' : 'hidden'}>Remove uploaded logo</button>
        </div>
        </details>`;
  }

  function preview(b, icon, { saved = false, publicNow = false } = {}) {
    publicNow = publicNow && !b.has_unpublished_changes;
    const amenities = (Array.isArray(b.amenities) ? b.amenities : String(b.amenities || '').split(',')).map((item) => item.trim()).filter(Boolean);
    const logoValue = String(b.logo_preview_url || b.logo_url || '');
    const logo = /^(https:\/\/|\/api\/businesses\/\d+\/logo(?:\?draft=1)?$)/.test(logoValue) ? logoValue : '';
    return `<div class="venue-live-preview-card"><div class="venue-preview-cover"><span class="venue-preview-brand">${icon('building')} ${saved && publicNow ? 'PLAYER LISTING' : 'PLAYER PREVIEW'}</span><span class="venue-preview-privacy">${icon(saved && publicNow ? 'check-circle' : 'lock')}${saved && publicNow ? 'Live' : 'Private preview'}</span><div class="venue-preview-identity"><span class="venue-preview-avatar">${logo ? `<img src="${escape(logo)}" alt="" />` : icon('building')}</span><div><h3>${escape(b.name || 'Your venue name')}</h3>${b.court_name ? `<p>${icon('map-pin')}${escape(b.court_name)}</p>` : ''}</div></div></div><div class="venue-preview-content">
      <p class="venue-preview-description">${escape(b.description || 'Add a short introduction for players.')}</p>
      ${b.announcement ? `<div class="venue-preview-update"><b>${icon('bell')} From the venue</b><p>${escape(b.announcement)}</p></div>` : ''}
      <dl class="venue-facts"><div><dt>${icon('clock')} Opening hours</dt><dd>${escape(hoursSummary(b))}</dd></div>${amenities.length ? `<div><dt>${icon('check-circle')} Amenities</dt><dd class="venue-preview-amenities">${amenities.map((item) => `<span>${escape(item)}</span>`).join('')}</dd></div>` : ''}${b.phone || b.email || b.website_url ? `<div><dt>${icon('phone')} Contact</dt><dd>${escape([b.phone, b.email, b.website_url].filter(Boolean).join('\n'))}</dd></div>` : ''}</dl>
      ${Object.keys(b.visitor_info || {}).length ? `<details class="simple-disclosure"><summary>Access &amp; arrival</summary><dl class="court-visiting-facts">${Object.entries(b.visitor_info).map(([key,value]) => { const summary = visitingSummary({[key]:value}), colon = summary.indexOf(':'); return `<div><dt>${escape(summary.slice(0,colon))}</dt><dd>${escape(summary.slice(colon+1).trim())}</dd></div>`; }).join('')}</dl></details>` : ''}
      ${b.hours ? `<p class="venue-preview-visiting-notes"><b>Venue note</b> ${escape(b.hours)}</p>` : ''}<span class="venue-preview-caption">${saved ? publicNow ? 'Your saved venue information' : 'Saved · only visible to managers' : 'Preview of your edits · save to update'}</span></div></div>`;
  }

  const visitingFields = [
    ['entrance', 'Finding the entrance', 400, 'Enter through the gate beside the north parking lot.'],
    ['parking', 'Parking', 400, 'Where to park, charges, or restrictions.'],
    ['guest_access', 'Guests & membership', 240, 'Who can play and any guest requirements.'],
    ['accessibility', 'Accessibility', 300, 'Step-free routes, accessible parking, or known barriers.'],
    ['rotation', 'How open play works', 300, 'Where to queue and how players rotate in.'],
    ['court_labels', 'Finding your court', 160, 'Court numbers or names used on the schedule.'],
  ];
  const visitingChoices = {
    access_type: [['public','Public access'],['fee','Entry fee'],['members','Members & guests'],['unknown','Access not confirmed']],
    play_access: [['drop_in','Drop-in play'],['reservation','Reservation required'],['both','Drop-in & reservations'],['unknown','Booking requirements not confirmed']],
  };
  function visitingSummary(value) {
    const info = value && typeof value === 'object' ? value : {};
    return Object.entries(info).map(([key, raw]) => {
      const label = visitingFields.find(row => row[0] === key)?.[1] || (key === 'access_type' ? 'Access' : 'Playing here');
      const text = visitingChoices[key]?.find(row => row[0] === raw)?.[1] || raw;
      return `${label}: ${text}`;
    }).join(' · ');
  }
  function visitingForm(value = {}, prefix = 'venue-visit', { owner = false } = {}) {
    const info = value && typeof value === 'object' ? value : {};
    const labels = {access_type:'Who can use this venue?',play_access:'Can players drop in?'};
    return `<div class="venue-visiting-fields" data-visiting-fields="${escape(prefix)}"><p class="simple-note">${owner ? 'Save verified venue details here. Empty fields keep community information; your changes are reviewed before going live.' : 'Share only what you know. These are community corrections; venue-provided facts remain separate.'}</p>${Object.entries(visitingChoices).map(([key,rows]) => `<label class="form-field" for="${prefix}-${key}">${labels[key]}<select id="${prefix}-${key}" data-visit-field="${key}"><option value="">${owner ? 'Use community information' : 'Not listed'}</option>${rows.map(([value,label]) => `<option value="${value}" ${info[key] === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>`).join('')}<details class="simple-disclosure"><summary>Arrival, parking &amp; playing details</summary>${visitingFields.map(([key,label,max,placeholder]) => `<label class="form-field" for="${prefix}-${key}">${label}<textarea id="${prefix}-${key}" data-visit-field="${key}" maxlength="${max}" rows="2" placeholder="${escape(placeholder)}">${escape(info[key] || '')}</textarea></label>`).join('')}</details></div>`;
  }
  function readVisitingForm(root, prefix = 'venue-visit') {
    return Object.fromEntries([...root.querySelectorAll(`[data-visiting-fields="${prefix}"] [data-visit-field]`)].map(input => [input.dataset.visitField, input.value.trim()]).filter(([,value]) => value));
  }

  const hoursDays = [['mon','Monday'],['tue','Tuesday'],['wed','Wednesday'],['thu','Thursday'],['fri','Friday'],['sat','Saturday'],['sun','Sunday']];
  const hoursObject = value => { try { return typeof value === 'string' ? JSON.parse(value || '{}') : value || {}; } catch { return {}; } };
  function hoursWindowsHtml(prefix, rows = []) {
    rows = Array.isArray(rows) ? rows : rows && rows.open ? [rows] : [];
    return `<div class="venue-hours-windows">${Array.from({length:4}, (_,index) => `<div class="venue-hours-window" ${index && !rows[index] ? 'hidden' : ''}><label>Opens<input type="time" id="${prefix}-open-${index}" data-hours-open value="${escape(rows[index]?.open || '')}" /></label><label>Closes<input type="time" id="${prefix}-close-${index}" data-hours-close value="${escape(rows[index]?.close || '')}" /></label>${index ? '<button type="button" class="btn-link" data-hours-remove-window>Remove</button>' : ''}</div>`).join('')}<button type="button" class="btn-link" data-hours-add-window>Add another opening period</button></div>`;
  }
  function hoursExceptionHtml(on='', rows=[], index=0) {
    return `<fieldset class="venue-hours-exception" data-hours-exception><legend>Holiday or special date</legend><label>Date<input type="date" id="hours-exception-${index}-date" data-hours-date value="${escape(on)}" /></label><label>Hours<select data-hours-day-mode id="hours-exception-${index}-mode"><option value="closed" ${!rows.length ? 'selected' : ''}>Closed</option><option value="open" ${rows.length ? 'selected' : ''}>Set hours</option></select></label>${hoursWindowsHtml(`hours-exception-${index}`,rows)}<button type="button" class="btn-link" data-hours-remove-date>Remove date</button></fieldset>`;
  }
  function hoursForm(b) {
    const schedule=hoursObject(b.structured_hours), mode=b.hours_dawn_to_dusk ? 'dawn' : Object.keys(schedule).length ? 'weekly' : 'community';
    return `<form id="venue-hours-form" novalidate><p class="simple-note">These hours control Open now on the map and the court page. Reviewed hours stay live while changes are reviewed.</p><label class="form-field">Hours source<select id="venue-hours-mode"><option value="community" ${mode==='community'?'selected':''}>Use community court hours</option><option value="weekly" ${mode==='weekly'?'selected':''}>Set venue hours</option><option value="dawn" ${mode==='dawn'?'selected':''}>Dawn to dusk</option></select></label><div data-venue-weekly-hours><label class="form-field">Time zone at the venue<select id="venue-hours-zone">${timezoneOptions(schedule.timezone || b.timezone)}</select></label><p class="simple-note">Closing before opening means the next day. Matching times mean open 24 hours.</p><button type="button" class="btn btn-secondary" id="venue-hours-copy-weekdays">Use Monday hours for weekdays</button><div class="venue-hours-week">${hoursDays.map(([day,label])=>`<fieldset class="venue-hours-day" data-hours-weekday="${day}"><legend>${label}</legend><label>Hours<select data-hours-day-mode id="venue-hours-${day}-mode"><option value="unknown" ${!(day in schedule)?'selected':''}>Not listed</option><option value="closed" ${Array.isArray(schedule[day])&&!schedule[day].length?'selected':''}>Closed</option><option value="open" ${schedule[day]&&(schedule[day].open||schedule[day].length)?'selected':''}>Set hours</option></select></label>${hoursWindowsHtml(`venue-hours-${day}`,schedule[day])}</fieldset>`).join('')}</div><h4>Holiday hours</h4><p class="simple-note">Each date replaces the normal hours, including any overnight opening carried into that day.</p><div id="venue-hours-exceptions">${Object.entries(schedule.exceptions || {}).map(([on,rows],index)=>hoursExceptionHtml(on,rows,index)).join('')}</div><button type="button" class="btn btn-secondary" id="venue-hours-add-date">Add a special date</button></div><div class="form-field"><label for="venue-hours-note">Additional visiting information</label><textarea id="venue-hours-note" rows="3" maxlength="1000" aria-describedby="venue-hours-note-help">${escape(b.hours || '')}</textarea><small id="venue-hours-note-help">Keep this consistent with the opening times above.</small></div><p id="venue-hours-impact" role="status"></p><button type="submit" class="btn btn-primary btn-block" id="venue-hours-save">Save hours for review</button></form>`;
  }
  function bindHoursForm(modal) {
    const form=modal.querySelector('#venue-hours-form');
    const sync=()=>{
      const mode=modal.querySelector('#venue-hours-mode').value;
      modal.querySelector('[data-venue-weekly-hours]').hidden=mode!=='weekly';
      modal.querySelector('#venue-hours-impact').textContent=mode==='community'?'Players will see the community court hours.':mode==='dawn'?'Players see Dawn to dusk. Open now stays unknown because daylight varies.':'Times are local to your venue. Changes are saved together.';
      form.querySelectorAll('[data-hours-day-mode]').forEach(select=>{select.closest('fieldset').querySelector('.venue-hours-windows').hidden=select.value!=='open';});
    };
    form.addEventListener('change',sync);
    form.addEventListener('click',event=>{
      if(event.target.closest('[data-hours-add-window]')) {const row=event.target.closest('.venue-hours-windows').querySelector('.venue-hours-window[hidden]');if(row)row.hidden=false;}
      if(event.target.closest('[data-hours-remove-window]')) {const row=event.target.closest('.venue-hours-window');row.querySelectorAll('input').forEach(input=>{input.value='';});row.hidden=true;}
      if(event.target.closest('[data-hours-remove-date]')) event.target.closest('[data-hours-exception]').remove();
    });
    modal.querySelector('#venue-hours-copy-weekdays').addEventListener('click',()=>{const source=form.querySelector('[data-hours-weekday=mon]');for(const day of ['tue','wed','thu','fri']){const target=form.querySelector(`[data-hours-weekday=${day}]`);target.querySelector('[data-hours-day-mode]').value=source.querySelector('[data-hours-day-mode]').value;const sourceRows=[...source.querySelectorAll('.venue-hours-window')];target.querySelectorAll('.venue-hours-window').forEach((row,index)=>{row.hidden=sourceRows[index].hidden;row.querySelector('[data-hours-open]').value=sourceRows[index].querySelector('[data-hours-open]').value;row.querySelector('[data-hours-close]').value=sourceRows[index].querySelector('[data-hours-close]').value;});}sync();});
    let nextIndex=form.querySelectorAll('[data-hours-exception]').length;
    modal.querySelector('#venue-hours-add-date').addEventListener('click',()=>{modal.querySelector('#venue-hours-exceptions').insertAdjacentHTML('beforeend',hoursExceptionHtml('',[],nextIndex++));sync();modal.querySelectorAll('[data-hours-date]')[nextIndex-1]?.focus();});
    sync();return sync;
  }
  function readHoursForm(modal) {
    const mode=modal.querySelector('#venue-hours-mode').value;
    const hours=modal.querySelector('#venue-hours-note').value.trim();
    if(mode!=='weekly')return {structured_hours:{},hours_dawn_to_dusk:mode==='dawn',hours};
    const zone=modal.querySelector('#venue-hours-zone').value;
    if(!zone)throw new Error('Choose the time zone at your venue.');
    const schedule={timezone:zone,exceptions:{}};
    const windows=fieldset=>{
      if(fieldset.querySelector('[data-hours-day-mode]').value!=='open')return [];
      const result=[...fieldset.querySelectorAll('.venue-hours-window')].filter(row=>!row.hidden).map(row=>({open:row.querySelector('[data-hours-open]').value,close:row.querySelector('[data-hours-close]').value}));
      if(!result.length||result.some(row=>!row.open||!row.close))throw new Error('Add both opening and closing times for each opening period.');
      return result;
    };
    modal.querySelectorAll('[data-hours-weekday]').forEach(fieldset=>{if(fieldset.querySelector('[data-hours-day-mode]').value!=='unknown')schedule[fieldset.dataset.hoursWeekday]=windows(fieldset);});
    if(!hoursDays.some(([day])=>day in schedule))throw new Error('Set at least one day’s hours or mark it closed.');
    modal.querySelectorAll('[data-hours-exception]').forEach(fieldset=>{const on=fieldset.querySelector('[data-hours-date]').value;if(!on)throw new Error('Choose a date for each holiday change.');if(on in schedule.exceptions)throw new Error('Use one hours entry per special date.');schedule.exceptions[on]=windows(fieldset);});
    return {structured_hours:schedule,hours_dawn_to_dusk:false,timezone:zone,hours};
  }
  function hoursSummary(b) {
    const configured=hoursObject(b.structured_hours);
    if(b.hours_dawn_to_dusk)return 'Dawn to dusk';
    if(Object.keys(configured).length){
      const clock=value=>{const [h,m]=String(value).split(':').map(Number);return `${h%12 || 12}${m?`:${String(m).padStart(2,'0')}`:''} ${h<12?'AM':'PM'}`;};
      const groups=[];
      hoursDays.forEach(([day,label],index)=>{
        if(!(day in configured))return;
        const rows=Array.isArray(configured[day])?configured[day]:[configured[day]];
        const windows=rows.length?rows.map(row=>row.open===row.close?'24 hours':`${clock(row.open)}–${clock(row.close)}`).join(', '):'Closed';
        const previous=groups.at(-1);
        if(previous && previous.end===index-1 && previous.windows===windows){previous.last=label.slice(0,3);previous.end=index;}
        else groups.push({first:label.slice(0,3),last:label.slice(0,3),end:index,windows});
      });
      const count=Object.keys(configured.exceptions || {}).length;
      return groups.map(group=>`${group.first}${group.first!==group.last?'–'+group.last:''} ${group.windows}`).join(' · ')+(count?` · ${count} special date${count===1?'':'s'}`:'');
    }
    return b.effective_hours?.open_status?.label || b.hours || 'Hours not added';
  }

  function detailsForm(b, { head, icon, hasManagedLogo }) {
    const e = escape;
    return `${head}<div class="venue-editor-context-row"><p class="venue-editor-context">${e(b.name || b.court_name)}</p><span class="venue-editor-owner">${icon('shield')} Venue manager</span></div>
      <form id="business-details-form" class="venue-studio-form" data-view="edit" novalidate>
      <div class="venue-editor-view-switch" role="group" aria-label="Editor view"><button type="button" id="venue-back-edit" data-venue-editor-view="edit" aria-pressed="true">${icon('edit')} Edit details</button><button type="button" id="venue-jump-preview" data-venue-editor-view="preview" aria-pressed="false">${icon('eye')} Player preview</button></div>
      <div class="venue-edit-layout"><div class="venue-edit-fields">
        <nav class="venue-section-tabs" role="tablist" aria-label="Venue detail sections">${[['about', 'About'], ['visit', 'Visit'], ['contact', 'Contact']].map(([key,label]) => `<button type="button" role="tab" id="venue-section-${key}" aria-controls="venue-field-${key}" aria-selected="${key === 'about'}" tabindex="${key === 'about' ? 0 : -1}" data-venue-section="${key}">${label}</button>`).join('')}</nav>
        <section id="venue-field-about" data-venue-field-section="about" role="tabpanel" aria-labelledby="venue-section-about"><h3>Introduce your venue</h3><p class="venue-field-intro">Give players a reason to visit.</p>
          <div class="form-field"><label for="business-name">Venue name</label><input type="text" id="business-name" maxlength="120" value="${e(b.name || '')}" placeholder="Your club or facility name" /></div>
          <div class="form-field"><label for="business-description">About your venue</label><textarea id="business-description" rows="4" maxlength="2000" placeholder="Courts, atmosphere, and who you welcome…">${e(b.description || '')}</textarea></div>
          <div class="form-field"><label for="business-announcement">Latest update <span>Optional</span></label><textarea id="business-announcement" rows="3" maxlength="500" placeholder="A timely announcement for players">${e(b.announcement || '')}</textarea></div>
        </section>
        <section id="venue-field-visit" data-venue-field-section="visit" role="tabpanel" aria-labelledby="venue-section-visit" hidden><h3>Plan a visit</h3><p class="venue-field-intro">The essentials before players arrive.</p>
          <div class="form-field"><button type="button" class="btn btn-secondary" id="business-edit-hours">Set weekly &amp; holiday hours</button><button type="button" class="btn btn-secondary" id="business-edit-visiting">Access, parking &amp; arrival</button><label for="business-hours">Visiting notes</label><textarea id="business-hours" rows="4" maxlength="1000" placeholder="A short note for visitors">${e(b.hours || '')}</textarea></div>
          <div class="form-field"><label for="business-timezone">Venue time zone</label><select id="business-timezone" data-select-title="Venue time zone">${timezoneOptions(b.timezone)}</select><small>New sessions use this time zone, wherever you manage the venue from.</small></div>
          <div class="form-field"><label for="business-amenities">Amenities</label><textarea id="business-amenities" rows="3" maxlength="1800" placeholder="Paddle rentals, water station, parking">${e((b.amenities || []).join(', '))}</textarea><small>Separate each amenity with a comma.</small></div>
        </section>
        <section id="venue-field-contact" data-venue-field-section="contact" role="tabpanel" aria-labelledby="venue-section-contact" hidden><h3>Make it easy to reach you</h3><p class="venue-field-intro">Public contact details for your venue.</p>
          <div class="form-field"><label for="business-phone">Phone</label><input type="tel" id="business-phone" maxlength="40" value="${e(b.phone || '')}" autocomplete="tel" /></div>
          <div class="form-field"><label for="business-email">Email</label><input type="email" id="business-email" maxlength="255" value="${e(b.email || '')}" autocomplete="email" /></div>
          <div class="form-field"><label for="business-website-url">Website</label><input type="url" id="business-website-url" value="${e(b.website_url || '')}" placeholder="https://yourclub.com" inputmode="url" /></div>
          ${logoFields(b, { icon, hasManagedLogo })}
        </section>
      </div><aside class="venue-editor-preview" aria-label="Player preview"><p class="venue-preview-heading">As players will see it</p><div id="venue-details-preview"></div></aside></div>
      <footer class="venue-editor-savebar"><div><b id="venue-details-dirty">All changes saved</b><p id="venue-details-save-impact"></p></div><button type="submit" class="btn btn-primary" id="business-details-save">Save venue details</button></footer></form>`;
  }

  function bindTabs(root, onChange) {
    const tabs = [...root.querySelectorAll('[data-venue-panel]')];
    const activate = (button, focus = false) => {
      tabs.forEach((tab) => { const selected = tab === button; tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1; root.querySelector(`#venue-panel-${tab.dataset.venuePanel}`).hidden = !selected; });
      if (focus) button.focus();
      onChange(button.dataset.venuePanel);
    };
    tabs.forEach((button, index) => {
      button.addEventListener('click', () => activate(button));
      button.addEventListener('keydown', (event) => {
        const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
        if (next !== null) { event.preventDefault(); activate(tabs[next], true); }
      });
    });
  }
  function analyticsHtml(data, formatDate) {
    const e = escape, summary = data.summary || data, previous = data.previous || {};
    const main = [['Listing views', 'profile_views'], ['Booking clicks', 'booking_clicks'], ['Lesson clicks', 'lesson_clicks']];
    const difference = key => {
      const current = summary[key], prior = previous[key];
      if (current == null || prior == null) return 'No comparison available';
      const delta = Number(current) - Number(prior);
      if (!Number.isFinite(delta)) return 'No comparison available';
      return delta === 0 ? 'Same as previous period' : `${delta > 0 ? '+' : '−'}${Math.abs(delta)} vs previous period`;
    };
    return `<p class="business-data-freshness">${data.since ? e(formatDate(data.since)) : ''}${data.until ? ` – ${e(formatDate(data.until))}` : ''}</p>
      <div class="business-analytics-grid">${main.map(([label,key])=>`<div class="card"><b>${e(metricValue(summary[key]))}</b><span>${label}</span><small>${e(difference(key))}</small></div>`).join('')}</div>
      <section class="venue-analytics-sessions"><h3>Most-clicked sessions</h3>${data.top_sessions?.length ? `<ol>${data.top_sessions.map(item=>`<li><span><b>${e(item.title || 'Session name not recorded')}</b><small>${e([item.date,item.source].filter(Boolean).join(' · '))}</small></span><strong>${e(metricValue(item.clicks))} clicks</strong></li>`).join('')}</ol>` : '<p>No session link clicks recorded in this period.</p>'}<p class="simple-note">${e(data.top_sessions_note || 'General and older unattributed links are excluded.')}</p></section>
      <details class="simple-disclosure"><summary>More activity</summary><div class="business-analytics-grid">${[['Schedule opens',summary.schedule_opens],['Calls & emails',summary.contact_clicks],['Website visits',summary.website_clicks],['Provider-reported bookings',summary.conversions]].map(([label,value])=>`<div class="card"><b>${e(metricValue(value))}</b><span>${label}</span></div>`).join('')}</div><p class="simple-note">${summary.conversions == null ? 'Booking reports are not connected.' : 'Booking reports come from a connected source.'} Reports are not linked to individual clicks, so a conversion rate is unavailable.</p></details>
      <p class="business-form-note">Views and clicks are activity counts, not unique players or confirmed bookings. Opening a booking link never confirms a reservation.</p>`;
  }

  function teamCapabilities(role) {
    return ({owner:'Edit content · publish · invite staff · transfer ownership',admin:'Edit content · publish · invite editors and viewers',editor:'Edit content · update existing schedule feeds · cannot publish or invite',viewer:'View workspace · cannot edit, publish or invite'})[role] || 'Ask the owner to confirm access';
  }

  function locationFields(location = {}) {
    const e = escape;
    return `<fieldset class="venue-form-section"><legend>Venue location</legend>
      <div class="form-field"><label for="venue-location-name">Venue name</label><input id="venue-location-name" maxlength="120" value="${e(location.name || '')}" /></div>
      <div class="form-field"><label for="venue-location-address">Street address</label><input id="venue-location-address" maxlength="255" value="${e(location.address || '')}" autocomplete="street-address" /></div>
      <div class="form-grid"><div class="form-field"><label for="venue-location-city">City</label><input id="venue-location-city" maxlength="120" value="${e(location.city || '')}" /></div><div class="form-field"><label for="venue-location-state">State (two letters)</label><input id="venue-location-state" maxlength="2" value="${e(location.state || '')}" placeholder="CA" autocapitalize="characters" /></div></div>
      <button type="button" class="btn btn-secondary btn-block" id="venue-location-find">Find address on map</button>
      <div id="venue-location-results" class="business-court-results" aria-live="polite"></div>
      <input type="hidden" id="venue-location-latitude" value="${e(location.latitude ?? '')}" /><input type="hidden" id="venue-location-longitude" value="${e(location.longitude ?? '')}" />
      <div id="venue-location-pin" class="business-preview-note" aria-live="polite"></div>
      <div class="form-field"><label for="venue-location-count">Number of courts</label><input type="number" id="venue-location-count" min="1" max="100" value="${e(location.num_courts || 1)}" /></div>
      <label class="business-authorized-check"><input type="checkbox" id="venue-location-indoor" ${location.indoor ? 'checked' : ''} /> <span>Indoor courts</span></label>
    </fieldset>`;
  }

  function bindLocation(modal, {request, showError, icon = () => ''}) {
    const input = key => modal.querySelector(`#venue-location-${key}`);
    const renderPin = () => {
      const lat = input('latitude').value, lng = input('longitude').value;
      modal.querySelector('#venue-location-pin').innerHTML = lat && lng ? `<div><b>Location selected</b><p>${escape([input('address').value, input('city').value, input('state').value].filter(Boolean).join(', '))}</p><a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat},${lng}`)}" target="_blank" rel="noopener">Check this map location ${icon('link')}</a></div>` : '<p>Find the address, then select its map location.</p>';
    };
    for (const key of ['address', 'city', 'state']) input(key).addEventListener('input', () => { input('latitude').value = ''; input('longitude').value = ''; renderPin(); });
    modal.querySelector('#venue-location-find').addEventListener('click', async event => {
      const button = event.currentTarget, results = modal.querySelector('#venue-location-results');
      if (input('address').value.trim().length < 3 || !input('city').value.trim() || !/^[a-z]{2}$/i.test(input('state').value.trim())) { showError('Add a street address, city and two-letter state first.', input('address')); return; }
      button.disabled = true; results.textContent = 'Finding this address…';
      try {
        const data = await request(`/geocode?q=${encodeURIComponent([input('address').value, input('city').value, input('state').value, 'USA'].join(', '))}`);
        const places = (data.items || []).filter(place => Number.isFinite(Number(place.lat)) && Number.isFinite(Number(place.lng)));
        results.innerHTML = places.length ? places.map((place, index) => `<button type="button" class="court-search-row" data-venue-place="${index}"><span class="row-main"><b>${escape(place.label || 'Map location')}</b><small>${escape(place.detail || '')}</small></span><span>Select</span></button>`).join('') : '<p>No address found. Check the street address and try again. Your venue details are still here.</p>';
        results.querySelectorAll('[data-venue-place]').forEach(button => button.addEventListener('click', () => { const place = places[Number(button.dataset.venuePlace)]; input('latitude').value = place.lat; input('longitude').value = place.lng; input('latitude').dispatchEvent(new Event('change', {bubbles:true})); results.innerHTML = ''; renderPin(); }));
      } catch (error) { results.textContent = ''; showError(error.message, button); }
      finally { button.disabled = false; }
    });
    renderPin();
    return () => {
      const value = Object.fromEntries(['name', 'address', 'city', 'state'].map(key => [key, input(key).value.trim()]));
      value.state = value.state.toUpperCase(); value.num_courts = Number(input('count').value); value.indoor = input('indoor').checked;
      value.latitude = Number(input('latitude').value); value.longitude = Number(input('longitude').value);
      if (value.name.length < 2) { showError('Add the venue name.', input('name')); return null; }
      if (!value.address || !value.city || !/^[A-Z]{2}$/.test(value.state)) { showError('Complete the venue address.', input('address')); return null; }
      if (!input('latitude').value || !input('longitude').value) { showError('Find the address and select its map location.', modal.querySelector('#venue-location-find')); return null; }
      if (!Number.isInteger(value.num_courts) || value.num_courts < 1 || value.num_courts > 100) { showError('Use a court count from 1 to 100.', input('count')); return null; }
      return value;
    };
  }

  function welcome(court = null, uiIcon) {
    const esc = escape;
    return `<section class="venue-welcome">
      <span class="venue-welcome-icon">${uiIcon('building')}</span>
      <h2>Your venue. Ready for players.</h2>
      <p>Manage your court’s information and help players book, join sessions, and find your business.</p>
      <ol class="venue-start-steps">
        <li><span>1</span><div><b>Find your venue</b><small>Choose its existing court listing.</small></div></li>
        <li><span>2</span><div><b>Confirm you manage it</b><small>We review your role before publishing.</small></div></li>
        <li><span>3</span><div><b>Make it yours</b><small>Add details and your existing booking link.</small></div></li>
      </ol>
      <button type="button" class="btn btn-primary btn-block" id="business-claim-start">${court ? `Manage ${esc(court.name)}` : 'Find my venue'}</button>
      <p class="venue-welcome-footnote">Already managing a venue? It appears here when you sign in with your business account.</p>
    </section>`;
  }

  function task({ tool, icon, title, copy, disabled = false }, uiIcon) {
    const esc = escape;
    return `<button type="button" class="venue-task" data-business-tool="${tool}" ${disabled ? 'disabled' : ''}>
      <span class="venue-task-icon" aria-hidden="true">${uiIcon(icon)}</span><span class="row-main"><b>${esc(title)}</b><small>${esc(copy)}</small></span>${uiIcon('chevron-right', 'chev')}
    </button>`;
  }

  function unavailable(feature, error, uiIcon) {
    const esc = escape;
    const unavailable = error?.status === 404 || error?.status === 501;
    return `<div class="business-feature-state ${unavailable ? 'is-neutral' : 'is-error'}" role="${unavailable ? 'status' : 'alert'}">
      <span aria-hidden="true">${uiIcon(unavailable ? 'activity' : 'alert-triangle')}</span>
      <div><b>${unavailable ? `${esc(feature)} is not enabled yet` : `${esc(feature)} could not load`}</b>
      <p>${unavailable ? 'Your public listing and secure outbound links continue to work. This control will appear when the server capability is enabled.' : esc(error?.message || 'Try again in a moment.')}</p></div>
    </div>`;
  }

  function revisionDiff(item) {
    const esc = escape;
    const before = item?.before_snapshot && typeof item.before_snapshot === 'object' ? item.before_snapshot : {};
    const after = item?.after_snapshot && typeof item.after_snapshot === 'object' ? item.after_snapshot : {};
    const beforeProfile = before.profile && typeof before.profile === 'object' ? before.profile : {};
    const afterProfile = after.profile && typeof after.profile === 'object' ? after.profile : {};
    const displayValue = (value) => {
      if (value === true) return 'Yes';
      if (value === false) return 'No';
      if (value == null || String(value).trim() === '') return 'Not set';
      const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
      return text.length > 180 ? `${text.slice(0, 177)}…` : text;
    };
    const label = (key) => String(key || '').replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase());
    const changed = [...new Set([...Object.keys(beforeProfile), ...Object.keys(afterProfile)])]
      .filter((key) => JSON.stringify(beforeProfile[key] ?? null) !== JSON.stringify(afterProfile[key] ?? null))
      .filter(key => key !== 'visitor_info')
      .map((key) => ({ key, before: displayValue(beforeProfile[key]), after: displayValue(afterProfile[key]) }));
    const visitObject = value => {try {return typeof value === 'string' ? JSON.parse(value || '{}') : value || {};} catch {return {};}};
    const oldVisit = visitObject(beforeProfile.visitor_info), newVisit = visitObject(afterProfile.visitor_info);
    for (const key of new Set([...Object.keys(oldVisit), ...Object.keys(newVisit)])) {
      if (!sameValue(oldVisit[key], newVisit[key])) changed.push({key:`Visiting details · ${visitingFields.find(row=>row[0]===key)?.[1] || label(key)}`, before:oldVisit[key] ? visitingSummary({[key]:oldVisit[key]}) : 'Uses community information', after:newVisit[key] ? visitingSummary({[key]:newVisit[key]}) : 'Uses community information'});
    }
    const serviceLabel = (snapshot, id) => id == null ? 'Standalone session' : snapshot.offerings?.find(row=>Number(row.id) === Number(id))?.name || 'Previously linked service';
    for (const key of ['offerings', 'schedule']) {
      const prior = Array.isArray(before[key]) ? before[key] : [];
      const next = Array.isArray(after[key]) ? after[key] : [];
      const previous = new Map(prior.map((row, index) => [String(row.id ?? `new-${index}`), row]));
      const following = new Map(next.map((row, index) => [String(row.id ?? `new-${index}`), row]));
      const ignored = new Set(['id', 'sort_order', 'created_at', 'updated_at', 'source_updated_at']);
      for (const id of new Set([...previous.keys(), ...following.keys()])) {
        const oldRow = previous.get(id), newRow = following.get(id);
        const name = newRow?.title || newRow?.name || oldRow?.title || oldRow?.name || label(key);
        if (!oldRow || !newRow) {
          changed.push({ key: `${key === 'offerings' ? 'Service' : 'Session'}: ${name}`, before: oldRow ? 'Listed' : 'Not listed', after: newRow ? 'Listed' : 'Removed' });
          continue;
        }
        for (const field of new Set([...Object.keys(oldRow), ...Object.keys(newRow)])) {
          if (field === 'occurrence_overrides') {
            const oldRules = oldRow[field] || {}, newRules = newRow[field] || {};
            for (const scope of ['dates', 'following']) for (const on of new Set([...Object.keys(oldRules[scope] || {}), ...Object.keys(newRules[scope] || {})])) {
              const previous = oldRules[scope]?.[on] || {}, following = newRules[scope]?.[on] || {};
              for (const key of new Set([...Object.keys(previous), ...Object.keys(following)])) {
                if (JSON.stringify(previous[key] ?? null) !== JSON.stringify(following[key] ?? null)) changed.push({key:`${name} · ${on}${scope === 'following' ? ' onward' : ' only'} · ${label(key)}`, before:key in previous ? key === 'offering_id' ? serviceLabel(before, previous[key]) : displayValue(previous[key]) : 'Uses repeating schedule', after:key in following ? key === 'offering_id' ? serviceLabel(after, following[key]) : displayValue(following[key]) : 'Uses repeating schedule'});
              }
            }
            continue;
          }
          if (!ignored.has(field) && JSON.stringify(oldRow[field] ?? null) !== JSON.stringify(newRow[field] ?? null)) changed.push({ key: `${name} · ${label(field)}`, before: field === 'offering_id' ? serviceLabel(before, oldRow[field]) : displayValue(oldRow[field]), after: field === 'offering_id' ? serviceLabel(after, newRow[field]) : displayValue(newRow[field]) });
        }
      }
    }
    if (!changed.length) return '<div class="business-operator-empty">No value-level difference is available for this legacy revision.</div>';
    return `<div class="business-revision-diff" aria-label="Changed business fields">${changed.map((change) => `<div class="business-revision-diff-row"><b>${esc(label(change.key))}</b><small><span>Before</span>${esc(change.before)}</small><small><span>After</span>${esc(change.after)}</small></div>`).join('')}</div>`;
  }

  function registrationHost(value) { try { return new URL(value).hostname; } catch { return 'Link needs updating'; } }

  const comparisonIgnoredFields = new Set(['id', 'sort_order', 'created_at', 'updated_at', 'source_updated_at', 'availability_updated_at', 'availability_fresh', 'availability_label', 'availability_as_of', 'freshness']);
  const sameValue = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  function reconcileProfile(original, edits, current, choices = {}) {
    const value = {}, conflicts = [];
    for (const [key, mine] of Object.entries(edits)) {
      if (sameValue(mine, original[key])) continue;
      const theirs = current[key];
      if (key === 'visitor_info') {
        const merged = {...(theirs || {})}, before = original[key] || {};
        for (const field of new Set([...Object.keys(before), ...Object.keys(mine || {})])) {
          if (sameValue(mine?.[field], before[field])) continue;
          const conflictKey = `${key}:${field}`;
          if (!sameValue(theirs?.[field], before[field]) && !sameValue(theirs?.[field], mine?.[field])) {
            conflicts.push({key:conflictKey, label:visitingFields.find(row=>row[0]===field)?.[1] || (field === 'access_type' ? 'Venue access' : 'Drop-in or reservation'), mine:mine?.[field], theirs:theirs?.[field]});
            if (choices[conflictKey] !== 'mine') continue;
          }
          if (mine?.[field]) merged[field] = mine[field]; else delete merged[field];
        }
        value[key] = merged;
        continue;
      }
      if (!sameValue(theirs, original[key]) && !sameValue(mine, theirs)) {
        conflicts.push({key, label: key.replaceAll('_', ' '), mine, theirs});
        if (choices[key] !== 'mine') continue;
      }
      value[key] = mine;
    }
    return {value, conflicts};
  }

  function reconcileCollection(original, edits, current, choices = {}) {
    const old = new Map(original.filter(row => entryId(row.id)).map(row => [entryId(row.id), row]));
    const mine = new Map(edits.filter(row => entryId(row.id)).map(row => [entryId(row.id), row]));
    const latest = new Map(current.map(row => [entryId(row.id), {...row}]));
    const conflicts = [];
    const content = row => Object.fromEntries(Object.entries(row || {}).filter(([key]) => !comparisonIgnoredFields.has(key)));
    for (const [id, before] of old) {
      const proposed = mine.get(id), saved = latest.get(id), name = proposed?.title || proposed?.name || saved?.title || saved?.name || before.title || before.name;
      if (!proposed) {
        if (!saved) continue;
        if (!sameValue(content(before), content(saved))) {
          const key = `${id}:remove`;
          conflicts.push({key, label: name, mine: 'Remove this item', theirs: 'Keep the updated item'});
          if (choices[key] !== 'mine') continue;
        }
        latest.delete(id); continue;
      }
      if (!saved) {
        if (sameValue(content(before), content(proposed))) continue;
        const key = `${id}:restore`;
        conflicts.push({key, label: name, mine: 'Restore with my edits', theirs: 'Keep it removed'});
        if (choices[key] === 'mine') { const restored = {...proposed}; delete restored.id; latest.set(`restored-${id}`, restored); }
        continue;
      }
      for (const [field, proposedValue] of Object.entries(content(proposed))) {
        if (sameValue(proposedValue, before[field])) continue;
        const key = `${id}:${field}`;
        if (!sameValue(saved[field], before[field]) && !sameValue(saved[field], proposedValue)) {
          conflicts.push({key, label: `${name} · ${field.replaceAll('_', ' ')}`, mine: proposedValue, theirs: saved[field]});
          if (choices[key] !== 'mine') continue;
        }
        saved[field] = proposedValue;
      }
    }
    const value = [...latest.values(), ...edits.filter(row => !entryId(row.id)).map(row => { const clean = {...row}; delete clean.id; return clean; })];
    if (value.length > 100) throw new Error('This list now has more than 100 items. Your edits are still here; remove an old item before saving.');
    return {value, conflicts};
  }

  async function saveEdits(business, {kind = '', changes = {}, items = [], request, resolveConflicts}) {
    // Each attempt compares against the original editor, preserving this
    // person's intent and every unrelated edit made by another manager.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const current = await request(`/businesses/${business.id}`);
      const reconcile = choices => kind
        ? reconcileCollection(business[kind] || [], items, current[kind] || [], choices)
        : reconcileProfile(business, changes, current, choices);
      let result = reconcile({});
      if (result.conflicts.length) {
        const choices = await resolveConflicts(result.conflicts);
        if (!choices) throw new Error('Your edits are still here. Nothing was saved.');
        result = reconcile(choices);
      }
      try {
        return await request(`/businesses/${business.id}${kind ? `/${kind}` : ''}`, {
          method: kind ? 'PUT' : 'PATCH', headers: contentHeaders(current),
          body: JSON.stringify(kind ? {items: result.value} : result.value),
        });
      } catch (error) {
        if (error.status !== 412 || attempt === 2) throw error;
      }
    }
  }

  async function saveLogo(business, {data = null, request, resolveConflicts}) {
    let current = business;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        return await request(`/businesses/${business.id}/logo`, {method: data ? 'POST' : 'DELETE', headers: contentHeaders(current), ...(data ? {body: JSON.stringify({data})} : {})});
      } catch (error) {
        if (error.status !== 412 || attempt === 2) throw error;
        const latest = await request(`/businesses/${business.id}`);
        if (!current.logo_content_version || current.logo_content_version !== latest.logo_content_version) {
          const choices = await resolveConflicts([{key:'logo', label:'Venue logo', mine:data ? 'Use my selected image' : 'Remove the uploaded image', theirs:'Keep the logo saved by another manager'}]);
          if (!choices || choices.logo !== 'mine') throw new Error('The saved logo was kept. Your text edits are still here.');
        }
        current = latest;
      }
    }
  }

  function contentHeaders(business) {
    if (!business?.content_version) throw new Error('Refresh this venue before saving. Your edits are still here.');
    return { 'If-Match': `"${business.content_version}"` };
  }

  function savedStatus(business) {
    if (business.content_review_status === 'pending') return business.is_public ? 'Draft saved for review. Your approved listing stays live.' : 'Draft saved for review. Your listing is private.';
    if (business.has_unpublished_changes) return 'Draft saved. Publish the approved changes when you are ready.';
    return business.is_public ? 'Saved. Your player listing is up to date.' : 'Draft saved. Your listing is private.';
  }

  function metricValue(value) {
    if (value == null || value === '') return '—';
    return typeof value === 'string' ? value : Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '—';
  }

  function fileSize(bytes) {
    const size = Math.max(0, Number(bytes) || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
    return `${(size / (1024 * 1024)).toFixed(size < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  }

  function fileDescription(file, fallback = 'File') {
    const type = String(file?.type || '').toLowerCase();
    const extension = String(file?.name || '').split('.').pop()?.toUpperCase() || '';
    const label = ({
      'application/json': 'JSON', 'text/json': 'JSON',
      'image/jpeg': 'JPEG image', 'image/png': 'PNG image', 'image/webp': 'WebP image',
    })[type] || (extension && extension.length <= 5 ? extension : fallback);
    return `${label} · ${fileSize(file?.size)}`;
  }

  function setFilePickerState(picker, {
    state = 'idle', name = 'No file selected', meta = '', badge = 'Optional', icon = null,
  } = {}, uiIcon) {
    if (!picker) return;
    picker.dataset.state = state;
    picker.toggleAttribute('aria-busy', state === 'loading');
    const button = picker.querySelector('[data-file-button]');
    const feedback = picker.querySelector('[data-file-feedback]');
    const stateIcon = picker.querySelector('[data-file-state-icon]');
    picker.querySelector('[data-file-name]').textContent = name;
    picker.querySelector('[data-file-meta]').textContent = meta;
    picker.querySelector('[data-file-state]').textContent = badge;
    stateIcon.innerHTML = uiIcon(icon || (state === 'success' ? 'check-circle' : state === 'error' ? 'alert-triangle' : state === 'loading' ? 'refresh' : picker.dataset.idleIcon || 'plus'));
    if (state === 'error') {
      feedback.setAttribute('role', 'alert');
      feedback.setAttribute('aria-live', 'assertive');
      button.setAttribute('aria-invalid', 'true');
    } else {
      feedback.setAttribute('role', 'status');
      feedback.setAttribute('aria-live', 'polite');
      button.removeAttribute('aria-invalid');
    }
  }

  return { visitingForm, readVisitingForm, visitingSummary, hoursForm, bindHoursForm, readHoursForm, hoursSummary, analyticsHtml, teamCapabilities, locationFields, bindLocation, saveLogo, timezoneOptions, saveOccurrence, occurrenceEditableFields, saveEdits, reconcileProfile, reconcileCollection, contentHeaders, savedStatus, metricValue, setFilePickerState, fileSize, fileDescription, revisionDiff, welcome, task, unavailable, render, preview, bindTabs, mergeItem, logoFields, state, bindDetailsEditor, sessionIsCurrent, bookingForm, detailsForm, changedDetails, assertCollectionUnchanged, bindBookingPreview };
}));
