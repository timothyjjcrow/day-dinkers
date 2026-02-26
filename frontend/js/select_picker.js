/**
 * Custom dropdown UI for large selects (e.g. court pickers).
 * Keeps the native <select> as source-of-truth for form compatibility.
 */
const SelectPicker = {
    instances: new WeakMap(),
    openInstance: null,
    _docEventsBound: false,

    enhanceById(id, options = {}) {
        const select = document.getElementById(id);
        if (!select) return null;
        return SelectPicker.enhance(select, options);
    },

    enhance(select, options = {}) {
        if (!select || select.tagName !== 'SELECT') return null;

        const existing = SelectPicker.instances.get(select);
        if (existing) {
            existing.options = SelectPicker._mergeOptions(existing.options, options);
            SelectPicker._sync(existing);
            return existing;
        }

        const mergedOptions = SelectPicker._mergeOptions({}, options);
        const wrapper = document.createElement('div');
        wrapper.className = 'select-picker';
        wrapper.dataset.selectId = select.id || '';

        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'select-picker-trigger';
        trigger.innerHTML = `
            <span class="select-picker-trigger-label">
                <span class="select-picker-label-main"></span>
                <span class="select-picker-label-sub"></span>
            </span>
            <svg class="select-picker-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="m6 9 6 6 6-6"></path>
            </svg>
        `;

        const panel = document.createElement('div');
        panel.className = 'select-picker-panel';
        panel.hidden = true;

        const searchWrap = document.createElement('div');
        searchWrap.className = 'select-picker-search-wrap';

        const searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'select-picker-search';
        searchInput.placeholder = mergedOptions.searchPlaceholder;
        searchInput.autocomplete = 'off';

        const optionsList = document.createElement('div');
        optionsList.className = 'select-picker-options';

        searchWrap.appendChild(searchInput);
        panel.appendChild(searchWrap);
        panel.appendChild(optionsList);
        wrapper.appendChild(trigger);
        wrapper.appendChild(panel);

        select.classList.add('select-picker-source');
        select.insertAdjacentElement('afterend', wrapper);

        const instance = {
            select,
            wrapper,
            trigger,
            panel,
            searchWrap,
            searchInput,
            optionsList,
            options: mergedOptions,
            query: '',
        };

        trigger.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (trigger.disabled) return;
            SelectPicker._toggle(instance);
        });

        searchInput.addEventListener('input', () => {
            instance.query = searchInput.value.trim().toLowerCase();
            SelectPicker._renderOptions(instance);
        });

        optionsList.addEventListener('click', (event) => {
            const btn = event.target.closest('.select-picker-option');
            if (!btn || btn.disabled) return;
            const value = btn.dataset.value ?? '';
            SelectPicker._choose(instance, value);
        });

        select.addEventListener('change', () => {
            SelectPicker._sync(instance);
        });

        SelectPicker.instances.set(select, instance);
        SelectPicker._bindDocumentEvents();
        SelectPicker._sync(instance);
        return instance;
    },

    _mergeOptions(base, next) {
        return {
            searchPlaceholder: next.searchPlaceholder || base.searchPlaceholder || 'Search...',
            emptyMessage: next.emptyMessage || base.emptyMessage || 'No results found.',
            searchThreshold: Number.isFinite(next.searchThreshold)
                ? Number(next.searchThreshold)
                : (Number.isFinite(base.searchThreshold) ? Number(base.searchThreshold) : 9),
        };
    },

    _bindDocumentEvents() {
        if (SelectPicker._docEventsBound) return;
        SelectPicker._docEventsBound = true;

        document.addEventListener('click', (event) => {
            const open = SelectPicker.openInstance;
            if (!open) return;
            if (!open.wrapper.isConnected) {
                SelectPicker.openInstance = null;
                return;
            }
            if (open.wrapper.contains(event.target)) return;
            SelectPicker._close(open);
        });

        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape') return;
            if (!SelectPicker.openInstance) return;
            SelectPicker._close(SelectPicker.openInstance);
        });
    },

    _toggle(instance) {
        if (SelectPicker.openInstance && !SelectPicker.openInstance.wrapper.isConnected) {
            SelectPicker.openInstance = null;
        }
        if (SelectPicker.openInstance && SelectPicker.openInstance !== instance) {
            SelectPicker._close(SelectPicker.openInstance);
        }
        if (instance.panel.hidden) {
            SelectPicker._open(instance);
        } else {
            SelectPicker._close(instance);
        }
    },

    _open(instance) {
        instance.query = '';
        instance.searchInput.value = '';
        SelectPicker._renderOptions(instance);

        instance.panel.hidden = false;
        instance.wrapper.classList.add('open');
        SelectPicker.openInstance = instance;

        if (SelectPicker._shouldShowSearch(instance)) {
            setTimeout(() => {
                instance.searchInput.focus();
            }, 0);
        }
    },

    _close(instance) {
        instance.panel.hidden = true;
        instance.wrapper.classList.remove('open');
        if (SelectPicker.openInstance === instance) {
            SelectPicker.openInstance = null;
        }
    },

    _choose(instance, value) {
        instance.select.value = value;
        instance.select.dispatchEvent(new Event('change', { bubbles: true }));
        SelectPicker._close(instance);
    },

    _sync(instance) {
        const selected = instance.select.selectedOptions && instance.select.selectedOptions[0]
            ? instance.select.selectedOptions[0]
            : instance.select.options[instance.select.selectedIndex];
        const label = selected ? String(selected.textContent || '').trim() : '';
        const [mainRaw, ...rest] = label.split(' — ');
        const main = mainRaw || instance.options.searchPlaceholder;
        const sub = rest.join(' — ');

        const mainEl = instance.trigger.querySelector('.select-picker-label-main');
        const subEl = instance.trigger.querySelector('.select-picker-label-sub');
        if (mainEl) mainEl.textContent = main;
        if (subEl) {
            subEl.textContent = sub;
            subEl.style.display = sub ? 'block' : 'none';
        }

        const hasValue = !!String(instance.select.value || '').trim();
        instance.trigger.classList.toggle('placeholder', !hasValue);
        instance.trigger.disabled = !!instance.select.disabled;
        instance.wrapper.classList.toggle('disabled', !!instance.select.disabled);

        if (!instance.panel.hidden) {
            SelectPicker._renderOptions(instance);
        }
    },

    _shouldShowSearch(instance) {
        const optionCount = Array.from(instance.select.options).filter(opt => !opt.disabled).length;
        return optionCount >= instance.options.searchThreshold;
    },

    _renderOptions(instance) {
        const showSearch = SelectPicker._shouldShowSearch(instance);
        instance.searchWrap.style.display = showSearch ? 'block' : 'none';
        const query = showSearch ? instance.query : '';

        const selectedValue = String(instance.select.value || '');
        const options = Array.from(instance.select.options)
            .map((opt) => ({
                value: String(opt.value || ''),
                label: String(opt.textContent || '').trim(),
                disabled: !!opt.disabled,
            }))
            .filter((opt) => {
                if (!query) return true;
                return opt.label.toLowerCase().includes(query);
            });

        if (!options.length) {
            instance.optionsList.innerHTML = `<div class="select-picker-empty">${instance.options.emptyMessage}</div>`;
            return;
        }

        instance.optionsList.innerHTML = options.map((opt) => {
            const isSelected = opt.value === selectedValue;
            const [main, ...rest] = opt.label.split(' — ');
            const sub = rest.join(' — ');
            return `
                <button
                    type="button"
                    class="select-picker-option ${isSelected ? 'selected' : ''}"
                    data-value="${SelectPicker._escapeAttr(opt.value)}"
                    ${opt.disabled ? 'disabled' : ''}
                >
                    <span class="select-picker-option-text">
                        <span class="select-picker-option-main">${SelectPicker._escapeHtml(main || opt.label)}</span>
                        ${sub ? `<span class="select-picker-option-sub">${SelectPicker._escapeHtml(sub)}</span>` : ''}
                    </span>
                    ${isSelected ? '<span class="select-picker-option-check">✓</span>' : ''}
                </button>
            `;
        }).join('');
    },

    _escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },

    _escapeAttr(value) {
        return SelectPicker._escapeHtml(value).replace(/`/g, '&#96;');
    },
};
