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

  function bindDetailsEditor(modal, business, { formUX, icon, verified, publicNow }) {
    const readPreview = () => Object.fromEntries(['name', 'description', 'announcement', 'hours', 'amenities', 'phone', 'email'].map((key) => [key, modal.querySelector(`#business-${key}`).value.trim()]));
    const syncPreview = () => {
      modal.querySelector('#venue-details-preview').innerHTML = preview(readPreview(), icon);
      modal.querySelector('#venue-details-dirty').textContent = formUX.isDirty() ? 'Unsaved changes' : 'Your saved details';
      const sensitive = ['name', 'phone', 'email'].some((key) => modal.querySelector(`#business-${key}`).value.trim() !== String(business[key] || ''))
        || modal.querySelector('#business-website-url').value.trim() !== String(business.website_url || '')
        || modal.querySelector('#business-logo-url').value.trim() !== String(business.logo_url || '');
      modal.querySelector('#venue-details-save-impact').textContent = sensitive && verified
        ? 'Saving these identity or contact changes makes your listing private until reviewed.'
        : (business.is_public === true || (business.is_public == null && publicNow)) ? 'Saved changes appear on your player listing.' : 'Changes save to your venue. Your listing is currently private.';
    };
    modal.querySelector('#business-details-form').addEventListener('input', syncPreview);
    modal.querySelector('#business-details-form').addEventListener('change', syncPreview);
    syncPreview();
    modal.querySelector('#venue-jump-preview').addEventListener('click', () => { const preview = modal.querySelector('.venue-editor-preview'); preview.tabIndex = -1; preview.scrollIntoView({ block: 'start' }); preview.focus({ preventScroll: true }); });
    modal.querySelector('#venue-back-edit').addEventListener('click', () => { const input = modal.querySelector('#business-name'); input.scrollIntoView({ block: 'center' }); input.focus({ preventScroll: true }); });
    return syncPreview;
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
      return `<article class="venue-content-item"><div class="venue-session-date"><b>${e(date || 'Date not set')}</b><span>${item.recurrence === 'dated' ? 'One-time session' : 'Recurring session'}</span></div><div class="venue-item-main"><h4>${e(item.title || 'Untitled session')}</h4><p class="venue-session-time">${e(time(item.start_time))} – ${e(time(item.end_time))}</p><p>${e([item.skill_level, item.location_note, item.capacity ? `${item.capacity} places` : ''].filter(Boolean).join(' · '))}</p>${range ? `<p>${e(range)}</p>` : ''}<div class="venue-item-meta"><span>${e(visibility(item))}</span>${state !== 'scheduled' ? `<span class="venue-item-status">${e(state)}</span>` : ''}<span>${e(item.timezone || '')}</span></div></div>${editorActions('schedule', item)}</article>`;
    }).join('');
    const offerings = (b.offerings || []).map((item) => `<article class="venue-content-item"><span class="venue-service-icon" aria-hidden="true">${icon('target')}</span><div class="venue-item-main"><h4>${e(item.name || 'Untitled lesson')}</h4><p>${e(item.description || '')}</p><p class="venue-session-time">${e([item.price_text, item.duration_minutes ? `${item.duration_minutes} min` : ''].filter(Boolean).join(' · '))}</p><div class="venue-item-meta"><span>${e(visibility(item))}</span><span>${e(String(item.category || 'other').replace(/_/g, ' '))}</span></div></div>${editorActions('offerings', item)}</article>`).join('');
    const empty = (name, copy) => `<div class="venue-content-empty">${icon(name)}<p>${e(copy)}</p></div>`;
    return `<nav class="venue-owner-tabs" role="tablist" aria-label="Manage your venue">${nav.map(([key, mark, label]) => `<button type="button" id="venue-tab-${key}" role="tab" aria-selected="${key === active}" aria-controls="venue-panel-${key}" tabindex="${key === active ? 0 : -1}" data-venue-panel="${key}">${icon(mark)}<span>${label}</span></button>`).join('')}</nav>
      <section class="venue-owner-panel" id="venue-panel-details" role="tabpanel" aria-labelledby="venue-tab-details" ${active === 'details' ? '' : 'hidden'}>
        ${panelHead('Venue details', 'The information players need before they visit.', tool('details', 'Edit venue details', true))}
        <div class="venue-overview-grid"><article class="venue-info-card"><span class="simple-eyebrow">ABOUT YOUR VENUE</span><h4>${e(b.name)}</h4><p>${e(b.description || 'Add a short introduction so players know what to expect.')}</p><dl class="venue-facts">${fact('Opening hours', b.hours)}${fact('Amenities', (b.amenities || []).join(' · '))}</dl></article><article class="venue-info-card"><span class="simple-eyebrow">CONTACT & UPDATES</span><dl class="venue-facts">${fact('Phone', b.phone)}${fact('Email', b.email)}${fact('Website', b.website_url)}</dl><div class="venue-announcement"><b>Latest update</b><p>${e(b.announcement || 'Nothing to announce. Add an update when something changes.')}</p>${tool('announcement', b.announcement ? 'Edit update' : 'Add an update')}</div></article></div>
      </section>
      <section class="venue-owner-panel" id="venue-panel-schedule" role="tabpanel" aria-labelledby="venue-tab-schedule" ${active === 'schedule' ? '' : 'hidden'}>
        ${panelHead('Sessions & events', 'Add a session. Set the time. Let players know what’s on.', tool('add-session', 'Add session', true))}
        <div class="venue-content-list">${schedule || empty('calendar', 'No sessions yet. Add your first open play, clinic, or event.')}</div>
        ${tool('schedule', 'Import or edit multiple sessions')}<p class="simple-note">Times use each session’s venue timezone. You keep these sessions up to date.</p>
      </section>
      <section class="venue-owner-panel" id="venue-panel-offerings" role="tabpanel" aria-labelledby="venue-tab-offerings" ${active === 'offerings' ? '' : 'hidden'}>
        ${panelHead('Lessons & services', 'Show what you offer, what it costs, and how to book.', tool('add-lesson', 'Add lesson or service', true))}
        <div class="venue-content-list">${offerings || empty('target', 'Add coaching, a clinic, a membership, or another service.')}</div>${tool('offerings', 'Edit multiple lessons')}
      </section>
      <section class="venue-owner-panel" id="venue-panel-booking" role="tabpanel" aria-labelledby="venue-tab-booking" ${active === 'booking' ? '' : 'hidden'}>
        ${panelHead('Booking links', 'Send players to the booking system you already use.', tool('booking', 'Edit booking links', true))}
        <div class="venue-overview-grid"><article class="venue-info-card"><span class="venue-service-icon">${icon('calendar')}</span><h4>Court reservations</h4><p>Players choose “Book a court” on your listing and finish booking on this page.</p><div class="venue-saved-link">${e(b.booking_url || 'No booking page added')}</div></article><article class="venue-info-card"><span class="venue-service-icon">${icon('ticket')}</span><h4>Memberships</h4><p>An optional link for players who want to join your venue.</p><div class="venue-saved-link">${e(b.membership_url || 'No membership page added')}</div></article></div>
      </section>`;
  }

  function bookingForm(business, { icon, head, canEdit }) {
    return `${head}
      <div class="simple-page-intro"><h3>Use the system you already have</h3><p>Add the page where players reserve a court. They’ll complete their booking with your provider.</p></div>
      <form id="venue-booking-form" novalidate>
        <div class="form-field"><label for="venue-booking-url">Booking page</label><input type="url" id="venue-booking-url" value="${escape(business.booking_url || '')}" placeholder="https://your-booking-page.com" inputmode="url" ${canEdit ? '' : 'disabled'} /><small>Copy the public booking link from your website or booking app.</small></div>
        <details class="simple-disclosure" ${business.membership_url ? 'open' : ''}><summary>Membership link <span>Optional</span></summary><div class="form-field"><label for="venue-membership-url">Membership page</label><input type="url" id="venue-membership-url" value="${escape(business.membership_url || '')}" placeholder="https://yourclub.com/join" inputmode="url" ${canEdit ? '' : 'disabled'} /></div></details>
        ${canEdit ? '<button type="submit" class="btn btn-primary btn-block" id="venue-booking-save">Save booking links</button>' : '<p class="simple-note">An owner, admin, or editor can update booking links.</p>'}
        <p class="simple-note">Changing these links makes a verified listing private until the links are reviewed.</p>
      </form>
      <div class="simple-section-title">Show players what’s on</div>
      <button type="button" class="venue-task" id="venue-booking-schedule"><span class="venue-task-icon">${icon('calendar')}</span><span class="row-main"><b>Add a schedule</b><small>Enter sessions or import a spreadsheet.</small></span>${icon('chevron-right', 'chev')}</button>
      <details class="simple-disclosure"><summary>Automatic updates &amp; integration help</summary>
        <p class="simple-note">A booking link is enough to get started. If your system can export a schedule feed, connect it here.</p>
        <button type="button" class="venue-task" id="venue-booking-feed"><span class="venue-task-icon">${icon('refresh')}</span><span class="row-main"><b>Connect a schedule feed</b><small>Manage automatic updates and connection health.</small></span>${icon('chevron-right', 'chev')}</button>
        <button type="button" class="btn-link" id="venue-booking-help">Ask about my booking system</button>
      </details>`;
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

  function preview(b, icon) {
    return `<div class="venue-live-preview-card"><div class="venue-preview-brand">${icon('building')}<span>PLAYER PREVIEW</span></div><h3>${escape(b.name || 'Your venue name')}</h3><p>${escape(b.description || 'Your introduction will appear here.')}</p>${b.announcement ? `<div class="venue-preview-update"><b>Latest update</b><p>${escape(b.announcement)}</p></div>` : ''}<dl class="venue-facts"><div><dt>Opening hours</dt><dd>${escape(b.hours || 'Add your opening hours')}</dd></div>${b.amenities ? `<div><dt>Amenities</dt><dd>${escape(b.amenities)}</dd></div>` : ''}${b.phone || b.email ? `<div><dt>Contact</dt><dd>${escape([b.phone, b.email].filter(Boolean).join(' · '))}</dd></div>` : ''}</dl><span class="venue-preview-caption">Preview of your edits · save to update</span></div>`;
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
  return { render, preview, bindTabs, mergeItem, logoFields, state, bindDetailsEditor, sessionIsCurrent, bookingForm };
}));
