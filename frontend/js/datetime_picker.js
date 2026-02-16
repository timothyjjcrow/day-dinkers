/**
 * Global datetime picker enhancer for scheduling inputs.
 * Uses flatpickr when available and gracefully falls back to native inputs.
 */
const DateTimePicker = {
    instances: new WeakMap(),

    isAvailable() {
        return typeof flatpickr === 'function';
    },

    init(root = document) {
        DateTimePicker.enhanceWithin(root);
    },

    enhanceWithin(root = document) {
        if (!root || typeof root.querySelectorAll !== 'function') return;
        const inputs = root.querySelectorAll('input[type="datetime-local"]');
        inputs.forEach((input) => {
            DateTimePicker.enhanceInput(input);
        });
    },

    enhanceInput(input) {
        if (!input) return null;
        const existing = DateTimePicker.instances.get(input);
        if (existing) {
            DateTimePicker._syncLimitsFromInput(input, existing);
            return existing;
        }

        if (!DateTimePicker.isAvailable()) return null;

        const instance = flatpickr(input, {
            enableTime: true,
            dateFormat: 'Y-m-d\\TH:i',
            altInput: true,
            altFormat: 'D, M j • h:i K',
            minuteIncrement: 15,
            time_24hr: false,
            allowInput: false,
            disableMobile: true,
            defaultDate: input.value || null,
            minDate: input.min || null,
            maxDate: input.max || null,
        });

        DateTimePicker.instances.set(input, instance);
        input.dataset.datetimeEnhanced = 'true';
        return instance;
    },

    setValue(inputOrId, value) {
        const input = DateTimePicker._resolveInput(inputOrId);
        if (!input) return;
        const nextValue = value || '';
        const instance = DateTimePicker.instances.get(input);
        if (instance) {
            if (!nextValue) {
                instance.clear(false);
            } else {
                instance.setDate(nextValue, false, 'Y-m-d\\TH:i');
            }
        } else {
            input.value = nextValue;
        }
    },

    setMin(inputOrId, minValue) {
        const input = DateTimePicker._resolveInput(inputOrId);
        if (!input) return;
        input.min = minValue || '';
        const instance = DateTimePicker.instances.get(input);
        if (instance) {
            instance.set('minDate', minValue || null);
        }
    },

    setMax(inputOrId, maxValue) {
        const input = DateTimePicker._resolveInput(inputOrId);
        if (!input) return;
        input.max = maxValue || '';
        const instance = DateTimePicker.instances.get(input);
        if (instance) {
            instance.set('maxDate', maxValue || null);
        }
    },

    focus(inputOrId) {
        const input = DateTimePicker._resolveInput(inputOrId);
        if (!input) return;
        const instance = DateTimePicker.instances.get(input);
        if (!instance) {
            input.focus();
            return;
        }
        if (instance.altInput) {
            instance.altInput.focus();
        } else {
            instance.input.focus();
        }
        instance.open();
    },

    _resolveInput(inputOrId) {
        if (!inputOrId) return null;
        if (typeof inputOrId === 'string') return document.getElementById(inputOrId);
        return inputOrId;
    },

    _syncLimitsFromInput(input, instance) {
        instance.set('minDate', input.min || null);
        instance.set('maxDate', input.max || null);
    },
};
