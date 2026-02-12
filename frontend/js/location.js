/**
 * Location service — auto check-in when near a court via geolocation.
 */
const LocationService = {
    watchId: null,
    refreshIntervalId: null,
    lastCheckInCourtId: null,
    checkedInCourtSnapshot: null, // { id, latitude, longitude, name }
    lastPosition: null, // { lat, lng }
    courts: [],
    CHECKIN_RADIUS_METERS: 100, // Auto check-in within 100 meters
    CHECKOUT_RADIUS_METERS: 140, // Hysteresis to avoid rapid check-out flapping
    AUTO_CHECKOUT_CONFIRM_MS: 120000, // Require sustained out-of-range fixes
    AUTO_CHECKOUT_MIN_READS: 3,
    AUTO_CHECKOUT_ACCURACY_BUFFER_MAX_METERS: 60,
    MIN_AUTO_ACTION_INTERVAL_MS: 45000,
    lastAutoActionAt: 0,
    checkoutOutsideSince: null,
    checkoutOutsideReads: 0,
    checkinRequestInFlight: false,
    checkoutRequestInFlight: false,
    heartbeatIntervalId: null,
    heartbeatRequestInFlight: false,
    lifecycleListenersBound: false,
    HEARTBEAT_INTERVAL_MS: 60000,

    start() {
        LocationService._bindLifecycleListeners();
        LocationService.syncWithServerStatus();

        if (LocationService.watchId !== null) {
            LocationService._startHeartbeatLoop();
            return;
        }
        if (!navigator.geolocation) {
            console.log('Geolocation not supported');
            return;
        }

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
        LocationService._stopHeartbeatLoop();
    },

    async _refreshCourts() {
        try {
            const courtsUrl = (typeof App !== 'undefined' && typeof App.buildCourtsQuery === 'function')
                ? App.buildCourtsQuery()
                : '/api/courts';
            const res = await API.get(courtsUrl);
            LocationService.courts = res.courts || [];
            if (LocationService.lastCheckInCourtId !== null) {
                const activeCourt = LocationService.courts.find(
                    c => c.id === LocationService.lastCheckInCourtId
                );
                if (activeCourt) {
                    LocationService._setCheckedInCourtSnapshot(activeCourt);
                    LocationService._showLocationBanner(activeCourt);
                }
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

        const now = Date.now();
        const canAutoAction = (now - LocationService.lastAutoActionAt)
            >= LocationService.MIN_AUTO_ACTION_INTERVAL_MS;
        const adaptiveCheckoutRadius = LocationService._adaptiveCheckoutRadius(accuracyMeters);
        const checkedCourtDistance = LocationService._distanceToCheckedCourt(lat, lng);
        const hasCheckedCourtDistance = Number.isFinite(checkedCourtDistance);

        if (nearestCourt && nearestDist <= LocationService.CHECKIN_RADIUS_METERS) {
            // We're at a court! Auto check-in if not already
            if (LocationService.lastCheckInCourtId !== nearestCourt.id && canAutoAction) {
                LocationService._resetCheckoutDebounce();
                LocationService._autoCheckIn(nearestCourt);
                return;
            }
            if (LocationService.lastCheckInCourtId === nearestCourt.id) {
                LocationService._resetCheckoutDebounce();
            }
            return;
        }

        if (LocationService.lastCheckInCourtId !== null) {
            // Not near any court — auto check-out if we were checked in
            const outsideCheckedCourt = hasCheckedCourtDistance
                ? checkedCourtDistance > adaptiveCheckoutRadius
                : (!nearestCourt || nearestDist > adaptiveCheckoutRadius);

            if (!outsideCheckedCourt) {
                LocationService._resetCheckoutDebounce();
                return;
            }

            LocationService._markOutsideReading(now);
            if (canAutoAction && LocationService._isCheckoutDebounceSatisfied(now)) {
                const distance = hasCheckedCourtDistance ? checkedCourtDistance : nearestDist;
                LocationService._autoCheckOut('geofence', {
                    distanceMeters: Number.isFinite(distance) ? Math.round(distance) : null,
                });
            }
        }
    },

    async _autoCheckIn(court) {
        if (LocationService.checkinRequestInFlight) return;
        LocationService.checkinRequestInFlight = true;
        try {
            await API.post('/api/presence/checkin', { court_id: court.id });
            LocationService.lastAutoActionAt = Date.now();
            LocationService.setCheckedInCourt(court.id, court);
            App.toast(`📍 Auto checked in at ${court.name}.`);
            LocationService._showLocationBanner(court);
            await LocationService._refreshPresenceUI();
        } catch (err) {
            console.log('Auto check-in failed:', err);
        } finally {
            LocationService.checkinRequestInFlight = false;
        }
    },

    async _autoCheckOut(reason = 'geofence', meta = {}) {
        if (LocationService.checkoutRequestInFlight) return;
        LocationService.checkoutRequestInFlight = true;
        try {
            await API.post('/api/presence/checkout', {});
            LocationService.lastAutoActionAt = Date.now();
            LocationService.clearCheckedInCourt();
            if (reason === 'manual') {
                App.toast('Checked out from this court.');
            } else {
                const distText = Number.isFinite(meta.distanceMeters)
                    ? ` (about ${meta.distanceMeters}m away)`
                    : '';
                App.toast(`Auto checked out because you moved away from the court${distText}. Any active Looking to Play now session was ended.`);
            }
            await LocationService._refreshPresenceUI();
        } catch (err) {
            const message = err?.message || 'Unable to auto check out';
            App.toast(message, 'error');
        } finally {
            LocationService.checkoutRequestInFlight = false;
        }
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

    _startHeartbeatLoop() {
        const token = localStorage.getItem('token');
        if (!token || LocationService.lastCheckInCourtId === null) {
            LocationService._stopHeartbeatLoop();
            return;
        }
        if (!LocationService.heartbeatIntervalId) {
            LocationService.heartbeatIntervalId = setInterval(() => {
                LocationService._heartbeat({ reason: 'interval' });
            }, LocationService.HEARTBEAT_INTERVAL_MS);
        }
        LocationService._heartbeat({ reason: 'sync' });
    },

    _stopHeartbeatLoop() {
        if (!LocationService.heartbeatIntervalId) return;
        clearInterval(LocationService.heartbeatIntervalId);
        LocationService.heartbeatIntervalId = null;
    },

    _bindLifecycleListeners() {
        if (LocationService.lifecycleListenersBound) return;
        LocationService.lifecycleListenersBound = true;

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') {
                LocationService._heartbeat({ reason: 'hidden', keepalive: true });
            } else if (document.visibilityState === 'visible') {
                LocationService._heartbeat({ reason: 'visible' });
            }
        });

        window.addEventListener('pagehide', () => {
            LocationService._heartbeat({ reason: 'pagehide', keepalive: true });
        });

        window.addEventListener('beforeunload', () => {
            LocationService._heartbeat({ reason: 'beforeunload', keepalive: true });
        });
    },

    async _heartbeat({ reason = 'interval', keepalive = false } = {}) {
        const token = localStorage.getItem('token');
        if (!token || LocationService.lastCheckInCourtId === null) return;

        if (keepalive && typeof fetch === 'function') {
            const payload = {
                court_id: LocationService.lastCheckInCourtId,
                source: reason,
            };
            fetch(`${API.baseUrl}/api/presence/ping`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`,
                },
                body: JSON.stringify(payload),
                keepalive: true,
            }).catch(() => {});
            return;
        }

        if (LocationService.heartbeatRequestInFlight) return;
        LocationService.heartbeatRequestInFlight = true;
        try {
            const payload = {
                court_id: LocationService.lastCheckInCourtId,
                source: reason,
            };
            const res = await API.post('/api/presence/ping', payload);
            if (!res?.checked_in) {
                LocationService.clearCheckedInCourt();
                await LocationService._refreshPresenceUI();
                return;
            }

            if (Number(res.court_id) !== LocationService.lastCheckInCourtId) {
                LocationService.lastCheckInCourtId = Number(res.court_id);
                LocationService._hydrateCheckedInCourtSnapshot(LocationService.lastCheckInCourtId);
            }
        } catch {}
        finally {
            LocationService.heartbeatRequestInFlight = false;
        }
    },

    setCheckedInCourt(courtId, courtData) {
        const parsed = Number(courtId);
        LocationService.lastCheckInCourtId = Number.isFinite(parsed) ? parsed : null;
        LocationService._resetCheckoutDebounce();
        if (LocationService.lastCheckInCourtId === null) {
            LocationService.checkedInCourtSnapshot = null;
            LocationService._stopHeartbeatLoop();
            return;
        }

        LocationService._startHeartbeatLoop();

        if (courtData && Number(courtData.id) === parsed) {
            LocationService._setCheckedInCourtSnapshot(courtData);
            return;
        }

        const fromLoaded = LocationService.courts.find(c => c.id === parsed);
        if (fromLoaded) {
            LocationService._setCheckedInCourtSnapshot(fromLoaded);
            return;
        }

        // Fallback: hydrate coordinates even if the selected county list excludes this court.
        LocationService._hydrateCheckedInCourtSnapshot(parsed);
    },

    clearCheckedInCourt() {
        LocationService.lastCheckInCourtId = null;
        LocationService.checkedInCourtSnapshot = null;
        LocationService._resetCheckoutDebounce();
        LocationService._stopHeartbeatLoop();
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
                <button onclick="LocationService._autoCheckOut('manual')" class="location-banner-close">&times;</button>
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
            const countyName = (typeof App !== 'undefined' && typeof App.getSelectedCountyName === 'function')
                ? App.getSelectedCountyName()
                : 'Humboldt';
            regionEl.textContent = `${countyName} County`;
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

    _adaptiveCheckoutRadius(accuracyMeters = 0) {
        const buffer = Math.min(
            Math.max(Number(accuracyMeters) || 0, 0),
            LocationService.AUTO_CHECKOUT_ACCURACY_BUFFER_MAX_METERS
        );
        return LocationService.CHECKOUT_RADIUS_METERS + buffer;
    },

    _markOutsideReading(now) {
        if (!LocationService.checkoutOutsideSince) {
            LocationService.checkoutOutsideSince = now;
        }
        LocationService.checkoutOutsideReads += 1;
    },

    _resetCheckoutDebounce() {
        LocationService.checkoutOutsideSince = null;
        LocationService.checkoutOutsideReads = 0;
    },

    _isCheckoutDebounceSatisfied(now) {
        if (!LocationService.checkoutOutsideSince) return false;
        const elapsedMs = now - LocationService.checkoutOutsideSince;
        return elapsedMs >= LocationService.AUTO_CHECKOUT_CONFIRM_MS
            && LocationService.checkoutOutsideReads >= LocationService.AUTO_CHECKOUT_MIN_READS;
    },

    _setCheckedInCourtSnapshot(court) {
        if (!court || court.id === undefined || court.id === null) return;
        const latitude = Number(court.latitude);
        const longitude = Number(court.longitude);
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
        LocationService.checkedInCourtSnapshot = {
            id: Number(court.id),
            latitude,
            longitude,
            name: court.name || '',
        };
    },

    _distanceToCheckedCourt(lat, lng) {
        const snapshot = LocationService.checkedInCourtSnapshot;
        if (!snapshot) return null;
        if (snapshot.id !== LocationService.lastCheckInCourtId) return null;
        if (!Number.isFinite(snapshot.latitude) || !Number.isFinite(snapshot.longitude)) return null;
        return LocationService._distanceMeters(lat, lng, snapshot.latitude, snapshot.longitude);
    },

    async _hydrateCheckedInCourtSnapshot(courtId) {
        try {
            const res = await API.get(`/api/courts/${courtId}`);
            const court = res?.court;
            if (!court) return;
            if (LocationService.lastCheckInCourtId !== Number(courtId)) return;
            LocationService._setCheckedInCourtSnapshot(court);
        } catch {}
    },

    async _refreshPresenceUI() {
        if (typeof MapView !== 'undefined') {
            if (typeof MapView.refreshMyStatus === 'function') {
                await MapView.refreshMyStatus();
            }
            if (typeof MapView.loadCourts === 'function') {
                MapView.loadCourts();
            }

            const currentCourtId = MapView.currentCourtId;
            const panel = document.getElementById('court-panel');
            const panelOpen = !!(panel && panel.style.display !== 'none' && currentCourtId);
            const fullPageOpen = typeof App !== 'undefined'
                && App.currentView === 'court-detail'
                && currentCourtId;

            if (fullPageOpen && typeof MapView._refreshFullPage === 'function') {
                await MapView._refreshFullPage(currentCourtId);
            } else if (panelOpen && typeof MapView.openCourtDetail === 'function') {
                MapView.openCourtDetail(currentCourtId);
            }
        }

        if (typeof App !== 'undefined'
            && App.currentView === 'sessions'
            && typeof Sessions !== 'undefined'
            && typeof Sessions.load === 'function'
        ) {
            Sessions.load();
        }
    },
};
