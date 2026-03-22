import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api, getStoredToken, setSession } from '../lib/api'
import { BottomSheet } from '../components/BottomSheet'
import { CompetitiveHistoryPanel } from '../components/CompetitiveHistoryPanel'
import type {
  LeaderboardEntry,
  MatchSummary,
  PresenceStatus,
  RankedHistoryResponse,
  RankedLeaderboardResponse,
  UserSummary,
} from '../types'

interface ProfilePageProps {
  currentUser: UserSummary | null
  currentPresence: PresenceStatus | null
  selectedCounty: string
  selectedCountyName: string
  onRequireAuth: () => void
  onLogout: () => Promise<void> | void
  onProfileUpdated: () => Promise<void> | void
}

export function ProfilePage({
  currentUser,
  currentPresence,
  selectedCounty,
  selectedCountyName,
  onRequireAuth,
  onLogout,
  onProfileUpdated,
}: ProfilePageProps) {
  const queryClient = useQueryClient()
  const profileQuery = useQuery({
    queryKey: ['profile'],
    queryFn: () => api.get<{ user: UserSummary }>('/api/auth/profile'),
    enabled: Boolean(currentUser),
  })
  const leaderboardQuery = useQuery({
    queryKey: ['profile-leaderboard', selectedCounty],
    queryFn: () =>
      api.get<RankedLeaderboardResponse>(
        `/api/ranked/leaderboard?county_slug=${encodeURIComponent(selectedCounty)}&limit=25`,
      ),
    enabled: Boolean(currentUser && selectedCounty),
  })
  const historyQuery = useQuery({
    queryKey: ['profile-history', currentUser?.id],
    queryFn: () =>
      api.get<RankedHistoryResponse>(`/api/ranked/history?user_id=${currentUser?.id}&limit=8`),
    enabled: Boolean(currentUser?.id),
  })

  const profile = profileQuery.data?.user || currentUser
  const leaderboard = (leaderboardQuery.data?.leaderboard || []) as LeaderboardEntry[]
  const recentMatches = (historyQuery.data?.matches || []) as MatchSummary[]
  const countyStanding = leaderboard.find((entry) => entry.user_id === currentUser?.id) || null
  const winRate = profile?.games_played
    ? Math.round(((profile.wins || 0) / profile.games_played) * 100)
    : 0
  const recentForm = recentMatches.slice(0, 5).map((match) => {
    const me = match.players.find((player) => player.user_id === currentUser?.id)
    if (!me?.team || !match.winner_team) return 'P'
    return me.team === match.winner_team ? 'W' : 'L'
  })
  const [pending, setPending] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [form, setForm] = useState({
    name: '',
    bio: '',
    photo_url: '',
    play_style: '',
    preferred_times: '',
    skill_level: '',
  })

  useEffect(() => {
    if (!profile) return
    setForm({
      name: profile.name || '',
      bio: profile.bio || '',
      photo_url: profile.photo_url || '',
      play_style: profile.play_style || '',
      preferred_times: profile.preferred_times || '',
      skill_level: profile.skill_level ? String(profile.skill_level) : '',
    })
  }, [profile])

  async function saveProfile() {
    if (!currentUser) {
      onRequireAuth()
      return false
    }

    setPending(true)
    try {
      const response = await api.put<{ user: UserSummary }>('/api/auth/profile', {
        ...form,
        skill_level: form.skill_level ? Number(form.skill_level) : null,
      })
      const token = getStoredToken()
      if (token) {
        setSession(token, response.user)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['profile'] }),
        queryClient.invalidateQueries({ queryKey: ['bootstrap'] }),
        queryClient.invalidateQueries({ queryKey: ['profile-leaderboard'] }),
        queryClient.invalidateQueries({ queryKey: ['profile-history'] }),
      ])
      await onProfileUpdated()
      return true
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to save profile')
      return false
    } finally {
      setPending(false)
    }
  }

  if (!currentUser) {
    return (
      <div className="page profile-page">
        <div className="page-header">
          <div>
            <div className="section-kicker">Profile</div>
            <h1>Build a quick player card</h1>
            <p>Keep your ranked profile clean so challenge, schedule, and chat all feel more personal.</p>
          </div>
        </div>
        <div className="empty-card large">
          Sign in to track your court presence, ranked history, and friends list.
          <button type="button" className="primary-btn" onClick={onRequireAuth}>
            Sign In
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="page profile-page">
      <section className="profile-hero-card">
        <div className="profile-hero-main">
          <div className="profile-avatar-shell">
            {profile?.photo_url ? (
              <img
                className="profile-avatar-image"
                src={profile.photo_url}
                alt={profile?.name || profile?.username || 'Profile'}
              />
            ) : (
              <span className="profile-avatar-fallback">{(profile?.name || profile?.username || '?').charAt(0).toUpperCase()}</span>
            )}
          </div>
          <div className="profile-hero-copy">
            <div className="section-kicker">Profile</div>
            <h1>{profile?.name || profile?.username}</h1>
            <p>@{profile?.username}</p>
            <div className="profile-inline-pills">
              {profile?.skill_level ? <span className="queue-pill">Skill {profile.skill_level}</span> : null}
              {profile?.play_style ? <span className="queue-pill">{profile.play_style}</span> : null}
              {countyStanding ? <span className="queue-pill">#{countyStanding.rank} in {selectedCountyName}</span> : null}
              {currentPresence?.checked_in ? (
                <span className="queue-pill success-pill">At {currentPresence.court_name}</span>
              ) : null}
            </div>
          </div>
        </div>
        <div className="profile-hero-actions">
          <button type="button" className="primary-btn" onClick={() => setEditOpen(true)}>
            Edit Profile
          </button>
          <button type="button" className="secondary-btn" onClick={onLogout}>
            Log Out
          </button>
        </div>
      </section>

      <div className="profile-stats">
        <div>
          <strong>{profile?.elo_rating || 1000}</strong>
          <span>ELO</span>
        </div>
        <div>
          <strong>{profile?.games_played || 0}</strong>
          <span>Ranked games</span>
        </div>
        <div>
          <strong>{winRate}%</strong>
          <span>Win rate</span>
        </div>
        <div>
          <strong>{profile?.friends_count || 0}</strong>
          <span>Friends</span>
        </div>
        <div>
          <strong>{profile?.upcoming_games || 0}</strong>
          <span>Upcoming</span>
        </div>
      </div>

      <section className="profile-card compact-profile-card profile-form-card">
        <div className="profile-form-head">
          <div>
            <div className="section-kicker">Ranked form</div>
            <strong>{countyStanding ? `Holding #${countyStanding.rank} in ${selectedCountyName}` : 'Start your first ranked run'}</strong>
          </div>
          <span className="queue-pill">{recentMatches.length} saved matches</span>
        </div>
        {recentForm.length ? (
          <div className="profile-form-strip">
            {recentForm.map((result, index) => (
              <span key={`${result}-${index}`} className={`profile-form-chip ${result === 'W' ? 'win' : result === 'L' ? 'loss' : 'pending'}`}>
                {result}
              </span>
            ))}
          </div>
        ) : (
          <div className="inline-note compact">Play one ranked game and your recent form appears here.</div>
        )}
      </section>

      <section className="profile-card compact-profile-card">
        <CompetitiveHistoryPanel
          scopeLabel={`${selectedCountyName} leaderboard`}
          leaderboard={leaderboard}
          matches={recentMatches}
          currentUserId={currentUser?.id}
          defaultTab="recent"
        />
      </section>

      <section className="profile-card compact-profile-card">
        <div className="section-kicker">Player Snapshot</div>
        <div className="detail-grid compact-detail-grid">
          <div><span>Check-ins</span><strong>{profile?.total_checkins || 0}</strong></div>
          <div><span>Preferred</span><strong>{profile?.preferred_times || 'Not set'}</strong></div>
          <div><span>Bio</span><strong>{profile?.bio || 'Add a short player note'}</strong></div>
          <div><span>Photo</span><strong>{profile?.photo_url ? 'Custom photo set' : 'Using initial avatar'}</strong></div>
        </div>
      </section>

      <BottomSheet
        open={editOpen}
        title="Edit Profile"
        eyebrow="Player Card"
        subtitle="Keep your profile ready for invites, rankings, and quick court intros."
        onClose={() => setEditOpen(false)}
        variant="action"
        footer={
          <button
            type="button"
            className="primary-btn full-width"
            onClick={async () => {
              const saved = await saveProfile()
              if (saved) {
                setEditOpen(false)
              }
            }}
            disabled={pending}
          >
            {pending ? 'Saving...' : 'Save Profile'}
          </button>
        }
      >
        <div className="sheet-grid">
          <label className="form-field">
            <span>Name</span>
            <input
              type="text"
              value={form.name}
              onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            />
          </label>
          <label className="form-field">
            <span>Photo URL</span>
            <input
              type="url"
              value={form.photo_url}
              onChange={(event) => setForm((current) => ({ ...current, photo_url: event.target.value }))}
            />
          </label>
          <label className="form-field">
            <span>Skill level</span>
            <input
              type="number"
              min="1"
              max="6"
              step="0.1"
              value={form.skill_level}
              onChange={(event) => setForm((current) => ({ ...current, skill_level: event.target.value }))}
            />
          </label>
          <label className="form-field">
            <span>Play style</span>
            <input
              type="text"
              value={form.play_style}
              onChange={(event) => setForm((current) => ({ ...current, play_style: event.target.value }))}
              placeholder="Aggressive kitchen, steady doubles, fast singles..."
            />
          </label>
          <label className="form-field">
            <span>Preferred times</span>
            <input
              type="text"
              value={form.preferred_times}
              onChange={(event) => setForm((current) => ({ ...current, preferred_times: event.target.value }))}
              placeholder="Weekday mornings, Friday lights..."
            />
          </label>
          <label className="form-field">
            <span>Bio</span>
            <textarea
              rows={4}
              value={form.bio}
              onChange={(event) => setForm((current) => ({ ...current, bio: event.target.value }))}
              placeholder="Short note so challenges and chats feel human."
            />
          </label>
        </div>
      </BottomSheet>
    </div>
  )
}
