/**
 * Location service — auto check-in when near a court via geolocation.
 */
const LocationService = {
    watchId: null,
    refreshIntervalId: null,
    lastCheckInCourtId: null,
    lastPosition: null, // { lat, lng }
    courts: [],
    CHECKIN_RADIUS_METERS: 100, // Auto check-in within 100 meters
    CHECKOUT_RADIUS_METERS: 140, // Hysteresis to avoid rapid check-out flapping
    MIN_AUTO_ACTION_INTERVAL_MS: 45000,
    lastAutoActionAt: 0,

    start() {
        if (LocationService.watchId !== null) return;
        if (!navigator.geolocation) {
            console.log('Geolocation not supported');
            return;
        }

        LocationService.syncWithServerStatus();

        // Start watching position
        LocationService.watchId = navigator.geolocation.watchPosition(
            LocationService._onPosition,
            LocationService._onError,
            { enableHighAccuracy: true, maximumAge: 30000, timeout: 10000 }
        );

        // Load courts for proximity checking
        LocationService._refreshCourts();
        // Refresh courts periodically
        if (!LocationService.refreshIntervalId) {
            LocationService.refreshIntervalId = setInterval(LocationService._refreshCourts, 60000);
        }
    },

    stop() {
        if (LocationService.watchId !== null) {
            navigator.geolocation.clearWatch(LocationService.watchId);
            LocationService.watchId = null;
        }
        if (LocationService.refreshIntervalId) {
            clearInterval(LocationService.refreshIntervalId);
            LocationService.refreshIntervalId = null;
        }
    },

    async _refreshCourts() {
        try {
            const res = await API.get('/api/courts');
            LocationService.courts = res.courts || [];
            if (LocationService.lastCheckInCourtId !== null) {
                const activeCourt = LocationService.courts.find(
                    c => c.id === LocationService.lastCheckInCourtId
                );
                if (activeCourt) LocationService._showLocationBanner(activeCourt);
            }
        } catch {}
    },

    _onPosition(position) {
        const { latitude, longitude, accuracy } = position.coords;
        LocationService.lastPosition = { lat: latitude, lng: longitude, accuracy: accuracy || null };
        LocationService._updateStatusBar(latitude, longitude);
        LocationService._checkProximity(latitude, longitude, accuracy || 0);
    },

    _onError(err) {
        console.log('Geolocation error:', err.message);
    },

    _checkProximity(lat, lng, accuracyMeters = 0) {
        const token = localStorage.getItem('token');
        if (!token) return; // Only auto-check-in for logged-in users
        if (accuracyMeters > 120) return; // Skip noisy position fixes

        let nearestCourt = null;
        let nearestDist = Infinity;

        for (const court of LocationService.courts) {
            const dist = LocationService._distanceMeters(lat, lng, court.latitude, court.longitude);
            if (dist < nearestDist) {
                nearestDist = dist;
                nearestCourt = court;
            }
        }

        const canAutoAction = (Date.now() - LocationService.lastAutoActionAt)
            >= LocationService.MIN_AUTO_ACTION_INTERVAL_MS;

        if (nearestCourt && nearestDist <= LocationService.CHECKIN_RADIUS_METERS) {
            // We're at a court! Auto check-in if not already
            if (LocationService.lastCheckInCourtId !== nearestCourt.id && canAutoAction) {
                LocationService._autoCheckIn(nearestCourt);
            }
        } else if (LocationService.lastCheckInCourtId !== null) {
            // Not near any court — auto check-out if we were checked in
            if (
                (!nearestCourt || nearestDist > LocationService.CHECKOUT_RADIUS_METERS)
                && canAutoAction
            ) {
                LocationService._autoCheckOut();
            }
        }
    },

    async _autoCheckIn(court) {
        try {
            await API.post('/api/presence/checkin', { court_id: court.id });
            LocationService.lastAutoActionAt = Date.now();
            LocationService.setCheckedInCourt(court.id);
            App.toast(`📍 Checked in at ${court.name}!`);
            LocationService._showLocationBanner(court);
            MapView.loadCourts(); // Refresh markers
        } catch (err) {
            console.log('Auto check-in failed:', err);
        }
    },

    async _autoCheckOut() {
        try {
            await API.post('/api/presence/checkout', {});
            LocationService.lastAutoActionAt = Date.now();
            LocationService.clearCheckedInCourt();
            MapView.loadCourts();
        } catch {}
    },

    async syncWithServerStatus() {
        const token = localStorage.getItem('token');
        if (!token) {
            LocationService.clearCheckedInCourt();
            return;
        }
        try {
            const status = await API.get('/api/presence/status');
            if (status?.checked_in && status.court_id) {
                LocationService.setCheckedInCourt(status.court_id);
                const court = LocationService.courts.find(c => c.id === status.court_id);
                if (court) LocationService._showLocationBanner(court);
            } else {
                LocationService.clearCheckedInCourt();
            }
        } catch {
            // Keep existing local state if the status endpoint is temporarily unavailable.
        }
    },

    setCheckedInCourt(courtId) {
        const parsed = Number(courtId);
        LocationService.lastCheckInCourtId = Number.isFinite(parsed) ? parsed : null;
    },

    clearCheckedInCourt() {
        LocationService.lastCheckInCourtId = null;
        LocationService._hideLocationBanner();
    },

    _showLocationBanner(court) {
        let banner = document.getElementById('location-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'location-banner';
            document.body.appendChild(banner);
        }
        banner.innerHTML = `
            <div class="location-banner-content">
                <span class="location-banner-dot"></span>
                <span>You're at <strong>${court.name}</strong></span>
                <button onclick="LocationService._hideLocationBanner(); LocationService._autoCheckOut();" class="location-banner-close">&times;</button>
            </div>
        `;
        banner.style.display = 'block';
        if (typeof App !== 'undefined' && typeof App.updateTopLayoutOffset === 'function') {
            App.updateTopLayoutOffset();
        }
    },

    _hideLocationBanner() {
        const banner = document.getElementById('location-banner');
        if (banner) banner.style.display = 'none';
        if (typeof App !== 'undefined' && typeof App.updateTopLayoutOffset === 'function') {
            App.updateTopLayoutOffset();
        }
    },

    _updateStatusBar(lat, lng) {
        // Find nearest court and show distance in the UI
        let nearest = null;
        let nearestDist = Infinity;
        for (const court of LocationService.courts) {
            const dist = LocationService._distanceMeters(lat, lng, court.latitude, court.longitude);
            if (dist < nearestDist) {
                nearestDist = dist;
                nearest = court;
            }
        }

        const regionEl = document.querySelector('.nav-region');
        if (nearest && nearestDist < 5000) { // Within 5km
            const distStr = nearestDist < 1000
                ? `${Math.round(nearestDist)}m`
                : `${(nearestDist / 1609).toFixed(1)}mi`;
            regionEl.textContent = `📍 ${distStr} from ${nearest.name}`;
            regionEl.classList.add('near-court');
        } else {
            regionEl.textContent = 'Humboldt County';
            regionEl.classList.remove('near-court');
        }
    },

    _distanceMeters(lat1, lon1, lat2, lon2) {
        const R = 6371000; // Earth radius in meters
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 +
                  Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
                  Math.sin(dLon / 2) ** 2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    },
};
