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
    if (review !== 'approved') return { publicNow, title: review === 'pending' ? 'Changes are being reviewed' : 'A change needs attention', copy: 'Your listing is private until these changes are approved.', tool: 'revisions', action: 'View changes' };
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
    const readPreview = () => ({ ...business, ...Object.fromEntries(['name', 'description', 'announcement', 'hours', 'amenities', 'phone', 'email', 'website-url', 'logo-url'].map((key) => [key.replaceAll('-', '_'), modal.querySelector(`#business-${key}`).value.trim()])) });
    const syncPreview = () => {
      const dirty = formUX.isDirty();
      modal.querySelector('#venue-details-preview').innerHTML = preview(readPreview(), icon);
      modal.querySelector('#venue-details-dirty').textContent = dirty ? 'Unsaved changes' : 'All changes saved';
      const sensitive = ['name', 'phone', 'email'].some((key) => modal.querySelector(`#business-${key}`).value.trim() !== String(baseline[key] || ''))
        || modal.querySelector('#business-website-url').value.trim() !== String(baseline.website_url || '')
        || modal.querySelector('#business-logo-url').value.trim() !== String(baseline.logo_url || '');
      modal.querySelector('#venue-details-save-impact').textContent = sensitive && verified
        ? 'These changes make your listing private until reviewed.'
        : (business.is_public === true || (business.is_public == null && publicNow)) ? 'Saved changes appear on your listing.' : 'Only managers can see this preview. Your listing is private.';
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
    const content = (items) => items.map((item) => Object.fromEntries(Object.entries(item).filter(([key]) => !['created_at', 'updated_at', 'source_updated_at', 'freshness', 'sort_order'].includes(key)).sort(([a], [b]) => a.localeCompare(b)))).sort((a, b) => entryId(a.id) - entryId(b.id));
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
      const keys = Object.keys(original).filter((key) => !['created_at', 'updated_at', 'source_updated_at', 'freshness', 'sort_order'].includes(key));
      if (keys.some((key) => JSON.stringify(next[index][key]) !== JSON.stringify(original[key]))) {
        throw new Error('This item changed elsewhere. Your edits are still here; close this editor and refresh your venue before trying again.');
      }
      if (updated === null) next.splice(index, 1);
      else {
        const edits = Object.fromEntries(Object.entries(updated).filter(([key]) => !['created_at', 'updated_at', 'source_updated_at', 'freshness', 'sort_order'].includes(key)));
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

  function render(b, { icon, day, time, workspace, canEdit, panel = 'details' }) {
    const active = panels.includes(panel) ? panel : 'details';
    const e = escape;
    const tool = (name, label, primary = false) => `<button type="button" class="btn btn-${primary ? 'primary' : 'secondary'}" data-business-tool="${name}" ${canEdit ? '' : 'disabled'}>${e(label)}</button>`;
    const nav = [['details', 'building', 'Venue'], ['schedule', 'calendar', 'Schedule'], ['offerings', 'target', 'Lessons'], ['booking', 'link', 'Booking']];
    const fact = (label, value) => `<div><dt>${e(label)}</dt><dd>${value ? e(value) : '<span class="venue-missing">Not added yet</span>'}</dd></div>`;
    const panelHead = (title, copy, action) => `<div class="venue-panel-heading"><div><h3>${e(title)}</h3><p>${e(copy)}</p></div>${action}</div>`;
    const editorActions = (kind, item) => canEdit ? `<div class="venue-item-actions"><button type="button" class="btn btn-secondary btn-sm" data-venue-edit="${kind}" data-item-id="${entryId(item.id)}" aria-label="Edit ${e(item.title || item.name)}">${icon('edit')} Edit</button><button type="button" class="venue-remove" data-venue-remove="${kind}" data-item-id="${entryId(item.id)}" aria-label="Remove ${e(item.title || item.name)}">${icon('trash')}</button></div>` : '';
    const visibility = (item) => item.active === false ? 'Hidden' : (item.status === 'completed' || (item.recurrence && !sessionIsCurrent(item))) ? 'Past session · no longer listed' : workspace.publicNow ? 'On your listing' : 'Saved · listing private';
    const schedule = (b.schedule || []).map((item) => {
      const date = item.recurrence === 'dated' ? item.event_date : day(item.day_of_week ?? item.day);
      const range = item.recurrence === 'date_range' ? [item.start_date, item.end_date].filter(Boolean).join(' to ') : '';
      const state = String(item.status || 'scheduled').replace(/_/g, ' ');
      return `<article class="venue-content-item"><div class="venue-session-date"><b>${e(date || 'Date not set')}</b><span>${item.recurrence === 'dated' ? 'One-time' : 'Repeats'}</span></div><div class="venue-item-main"><h4>${e(item.title || 'Untitled session')}</h4><p class="venue-session-time">${e(time(item.start_time))} – ${e(time(item.end_time))}</p><p>${e([item.skill_level, item.location_note, item.capacity ? `${item.capacity} places` : ''].filter(Boolean).join(' · '))}</p>${range ? `<p>${e(range)}</p>` : ''}<div class="venue-item-meta"><span>${e(visibility(item))}</span>${state !== 'scheduled' ? `<span class="venue-item-status">${e(state)}</span>` : ''}<span>${e(item.timezone || '')}</span></div></div>${editorActions('schedule', item)}</article>`;
    }).join('');
    const offerings = (b.offerings || []).map((item) => `<article class="venue-content-item"><span class="venue-service-icon" aria-hidden="true">${icon('target')}</span><div class="venue-item-main"><h4>${e(item.name || 'Untitled lesson')}</h4><p>${e(item.description || '')}</p><p class="venue-session-time">${e([item.price_text, item.duration_minutes ? `${item.duration_minutes} min` : ''].filter(Boolean).join(' · '))}</p><div class="venue-item-meta"><span>${e(visibility(item))}</span><span>${e(String(item.category || 'other').replace(/_/g, ' '))}</span></div></div>${editorActions('offerings', item)}</article>`).join('');
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
        ${panelHead('Sessions & events', `${(b.schedule || []).length} saved sessions`, tool('add-session', 'Add session', true))}
        <div class="venue-content-list">${schedule || empty('calendar', 'No sessions yet. Add your first open play, clinic, or event.')}</div>
        ${tool('schedule', 'Import or edit multiple sessions')}<p class="simple-note">Times use each session’s venue timezone. You keep these sessions up to date.</p>
      </section>
      <section class="venue-owner-panel" id="venue-panel-offerings" role="tabpanel" aria-labelledby="venue-tab-offerings" ${active === 'offerings' ? '' : 'hidden'}>
        ${panelHead('Lessons & services', `${(b.offerings || []).length} saved offerings`, tool('add-lesson', 'Add lesson or service', true))}
        <div class="venue-content-list">${offerings || empty('target', 'Add coaching, a clinic, a membership, or another service.')}</div>${tool('offerings', 'Edit multiple lessons')}
      </section>
      <section class="venue-owner-panel" id="venue-panel-booking" role="tabpanel" aria-labelledby="venue-tab-booking" ${active === 'booking' ? '' : 'hidden'}>
        ${panelHead('Booking links', 'Players book directly with you.', tool('booking', 'Edit booking links', true))}
        <div class="venue-overview-grid"><article class="venue-info-card"><span class="venue-service-icon">${icon('calendar')}</span><h4>Court reservations</h4><p>Players choose “Book a court” on your listing and finish booking on this page.</p><div class="venue-saved-link">${e(b.booking_url || 'No booking page added')}</div></article><article class="venue-info-card"><span class="venue-service-icon">${icon('ticket')}</span><h4>Memberships</h4><p>An optional link for players who want to join your venue.</p><div class="venue-saved-link">${e(b.membership_url || 'No membership page added')}</div></article></div>
      </section>`;
  }

  function bookingForm(business, { icon, head, canEdit }) {
    return `${head}<div class="venue-booking-studio"><p class="venue-editor-context">${escape(business.name)}</p>
      <form id="venue-booking-form" novalidate>
        <h3>Let players book with you</h3><p class="venue-field-intro">Connect your existing booking page.</p>
        <div class="form-field"><label for="venue-booking-url">Court booking link</label><input type="url" id="venue-booking-url" value="${escape(business.booking_url || '')}" placeholder="https://your-booking-page.com" inputmode="url" ${canEdit ? '' : 'disabled'} /></div>
        <div class="venue-booking-handoff" aria-label="Booking action preview"><span>${icon('calendar')}</span><div><b>Book a court</b><small id="venue-booking-destination">Your booking provider</small></div>${icon('external')}</div>
        <details class="simple-disclosure" ${business.membership_url ? 'open' : ''}><summary>Membership link <span>Optional</span></summary><div class="form-field"><label for="venue-membership-url">Membership page</label><input type="url" id="venue-membership-url" value="${escape(business.membership_url || '')}" placeholder="https://yourclub.com/join" inputmode="url" ${canEdit ? '' : 'disabled'} /></div></details>
        <p class="venue-booking-impact">${icon('shield')} Changed links make a verified listing private until reviewed.</p>
        ${canEdit ? '<button type="submit" class="btn btn-primary btn-block" id="venue-booking-save">Save booking links</button>' : '<p class="simple-note">An owner, admin, or editor can update booking links.</p>'}
      </form>
      <details class="simple-disclosure venue-booking-extras"><summary>Schedule &amp; integrations</summary>
        <button type="button" class="venue-task" id="venue-booking-schedule"><span class="venue-task-icon">${icon('calendar')}</span><span class="row-main"><b>Edit your schedule</b><small>Add sessions or import a spreadsheet</small></span>${icon('chevron-right', 'chev')}</button>
        <button type="button" class="venue-task" id="venue-booking-feed"><span class="venue-task-icon">${icon('refresh')}</span><span class="row-main"><b>Schedule feeds</b><small>Connections and automatic updates</small></span>${icon('chevron-right', 'chev')}</button>
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
        <div class="form-field"><label for="business-logo-url">Logo image link</label><input type="url" id="business-logo-url" value="${escape(business.logo_url || '')}" placeholder="https://yourclub.com/logo.png" inputmode="url" /><small class="field-help">Logo uploads save immediately. A changed logo makes a verified listing private until reviewed.</small></div>
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
          <button type="button" class="btn-link" id="business-logo-remove" ${hasManagedLogo ? '' : 'hidden'}>Remove uploaded logo</button>
        </div>
        </details>`;
  }

  function preview(b, icon, { saved = false, publicNow = false } = {}) {
    const amenities = (Array.isArray(b.amenities) ? b.amenities : String(b.amenities || '').split(',')).map((item) => item.trim()).filter(Boolean);
    const logo = /^(https:\/\/|\/api\/businesses\/\d+\/logo$)/.test(String(b.logo_url || '')) ? b.logo_url : '';
    return `<div class="venue-live-preview-card"><div class="venue-preview-cover"><span class="venue-preview-brand">${icon('building')} ${saved && publicNow ? 'PLAYER LISTING' : 'PLAYER PREVIEW'}</span><span class="venue-preview-privacy">${icon(saved && publicNow ? 'check-circle' : 'lock')}${saved && publicNow ? 'Live' : 'Private preview'}</span><div class="venue-preview-identity"><span class="venue-preview-avatar">${logo ? `<img src="${escape(logo)}" alt="" />` : icon('building')}</span><div><h3>${escape(b.name || 'Your venue name')}</h3>${b.court_name ? `<p>${icon('map-pin')}${escape(b.court_name)}</p>` : ''}</div></div></div><div class="venue-preview-content">
      <p class="venue-preview-description">${escape(b.description || 'Add a short introduction for players.')}</p>
      ${b.announcement ? `<div class="venue-preview-update"><b>${icon('bell')} From the venue</b><p>${escape(b.announcement)}</p></div>` : ''}
      <dl class="venue-facts"><div><dt>${icon('clock')} Opening hours</dt><dd>${escape(b.hours || 'Hours not added')}</dd></div>${amenities.length ? `<div><dt>${icon('check-circle')} Amenities</dt><dd class="venue-preview-amenities">${amenities.map((item) => `<span>${escape(item)}</span>`).join('')}</dd></div>` : ''}${b.phone || b.email || b.website_url ? `<div><dt>${icon('phone')} Contact</dt><dd>${escape([b.phone, b.email, b.website_url].filter(Boolean).join('\n'))}</dd></div>` : ''}</dl>
      <span class="venue-preview-caption">${saved ? publicNow ? 'Your saved venue information' : 'Saved · only visible to managers' : 'Preview of your edits · save to update'}</span></div></div>`;
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
          <div class="form-field"><label for="business-hours">Opening hours</label><textarea id="business-hours" rows="4" maxlength="1000" placeholder="Mon–Fri 7 AM–9 PM&#10;Sat–Sun 8 AM–8 PM">${e(b.hours || '')}</textarea></div>
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
      const text = String(value);
      return text.length > 180 ? `${text.slice(0, 177)}…` : text;
    };
    const label = (key) => String(key || '').replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase());
    const changed = [...new Set([...Object.keys(beforeProfile), ...Object.keys(afterProfile)])]
      .filter((key) => JSON.stringify(beforeProfile[key] ?? null) !== JSON.stringify(afterProfile[key] ?? null))
      .map((key) => ({ key, before: displayValue(beforeProfile[key]), after: displayValue(afterProfile[key]) }));
    for (const key of ['offerings', 'schedule']) {
      const prior = Array.isArray(before[key]) ? before[key] : [];
      const next = Array.isArray(after[key]) ? after[key] : [];
      if (JSON.stringify(prior) !== JSON.stringify(next)) {
        changed.push({ key, before: `${prior.length} item${prior.length === 1 ? '' : 's'}`, after: `${next.length} item${next.length === 1 ? '' : 's'}` });
      }
    }
    if (!changed.length) return '<div class="business-operator-empty">No value-level difference is available for this legacy revision.</div>';
    return `<div class="business-revision-diff" aria-label="Changed business fields">${changed.map((change) => `<div class="business-revision-diff-row"><b>${esc(label(change.key))}</b><small><span>Before</span>${esc(change.before)}</small><small><span>After</span>${esc(change.after)}</small></div>`).join('')}</div>`;
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

  return { setFilePickerState, fileSize, fileDescription, revisionDiff, welcome, task, unavailable, render, preview, bindTabs, mergeItem, logoFields, state, bindDetailsEditor, sessionIsCurrent, bookingForm, detailsForm, changedDetails, assertCollectionUnchanged, bindBookingPreview };
}));
