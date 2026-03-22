export type BannerItemType = 'session' | 'ranked_lobby' | 'tournament'

export interface UserSummary {
  id: number
  username: string
  email?: string
  name?: string
  bio?: string
  photo_url?: string
  play_style?: string
  preferred_times?: string
  skill_level?: number | null
  wins?: number
  losses?: number
  games_played?: number
  elo_rating?: number
  total_checkins?: number
  upcoming_games?: number
  friends_count?: number
  is_self?: boolean
  is_friend?: boolean
  can_challenge?: boolean
  looking_for_game?: boolean
  checked_in_at?: string | null
  court_id?: number
}

export interface CourtSummary {
  id: number
  name: string
  city?: string
  state?: string
  county_slug?: string
  address?: string
  photo_url?: string
  latitude: number
  longitude: number
  num_courts?: number
  indoor?: boolean
  lighted?: boolean
  court_type?: string
  surface_type?: string
  fees?: string
  active_players?: number
  open_sessions?: number
  distance?: number
}

export interface DaySummary {
  day_key: string
  label: string
  date_label: string
  count: number
}

export interface BannerItem {
  id: string
  reference_id: number
  item_type: BannerItemType
  title: string
  subtitle: string
  court_id: number
  court_name: string
  county_slug?: string
  state?: string
  start_time: string | null
  end_time?: string | null
  visibility?: string
  game_type?: string
  status?: string
  is_mine?: boolean
  participant_count?: number
  acceptance_status?: string | null
  viewer_status?: 'creator' | 'joined' | 'invited' | 'waitlisted' | 'participant' | 'none' | null
  creator_name?: string
  max_players?: number | null
  spots_taken?: number
  spots_remaining?: number
  is_friend_only?: boolean
  source?: string
}

export interface ScheduleBannerData {
  items: BannerItem[]
  days: DaySummary[]
  context: {
    court_id?: number | null
    county_slug?: string | null
    user_only: boolean
  }
}

export interface BootstrapData {
  authenticated: boolean
  user: UserSummary | null
  friend_ids: number[]
  friends: UserSummary[]
  unread_counts: {
    notifications: number
    inbox: number
    total: number
  }
  location: {
    selected_state_abbr: string
    selected_county_slug: string
    default_county_slug: string
    states: Array<{ abbr: string; name: string; court_count: number }>
    counties: Array<{
      slug: string
      name: string
      state: string
      state_name: string
      court_count: number
      has_courts: boolean
    }>
  }
  schedule_banner: ScheduleBannerData
  presence: PresenceStatus | null
}

export interface MatchPlayerSummary {
  user_id: number
  confirmed?: boolean
  team?: number
  user?: UserSummary
}

export interface MatchSummary {
  id: number
  court_id: number
  match_type: string
  status: string
  team1_score?: number | null
  team2_score?: number | null
  winner_team?: number | null
  confirmed_count?: number
  total_players?: number
  completed_at?: string | null
  court?: CourtSummary | null
  team1?: MatchPlayerSummary[]
  team2?: MatchPlayerSummary[]
  players: MatchPlayerSummary[]
}

export interface LeaderboardEntry {
  rank: number
  user_id: number
  name: string
  username: string
  elo_rating: number
  wins: number
  losses: number
  games_played: number
  win_rate: number
}

export interface RankedLeaderboardResponse {
  leaderboard: LeaderboardEntry[]
  court_id?: number | null
  county_slug?: string | null
}

export interface RankedHistoryResponse {
  matches: MatchSummary[]
}

export interface LobbySummary {
  id: number
  court_id: number
  match_type: string
  source?: string
  status: string
  scheduled_for?: string | null
  players: Array<{
    user_id: number
    acceptance_status: string
    team?: number
    user?: UserSummary
  }>
}

export interface QueueEntry {
  id: number
  court_id: number
  user_id: number
  match_type: 'singles' | 'doubles'
  joined_at: string
  position?: number
  match_type_position?: number
  user: UserSummary
  is_self?: boolean
  is_friend?: boolean
}

export interface ActionButtonPayload {
  kind: string
  label: string
  court_id?: number | null
  match_id?: number
  lobby_id?: number
  target_user_id?: number
  default_match_type?: 'singles' | 'doubles'
}

export interface QueueReadyCohort {
  ready_at: string
  match_type: 'singles' | 'doubles'
  entries: QueueEntry[]
  team1_user_ids: number[]
  team2_user_ids: number[]
  current_user_in_cohort: boolean
}

export interface ActionCenterData {
  type:
    | 'confirm_score'
    | 'enter_score'
    | 'respond_invite'
    | 'start_ready_game'
    | 'start_next_queue_game'
    | 'check_in'
    | 'quick_challenge'
    | 'queue_status'
    | 'join_queue'
    | 'schedule_ranked'
  primary_action: ActionButtonPayload
  secondary_action?: ActionButtonPayload | null
  match?: MatchSummary
  lobby?: LobbySummary
  challengeable_players?: UserSummary[]
  queue_entry?: QueueEntry
  queue_ready_cohort?: QueueReadyCohort
}

export interface CourtHubData {
  court_id: number
  header: {
    court: CourtSummary & Record<string, unknown>
    address_line: string
    active_players: number
    friend_count: number
    checked_in_here: boolean
    pending_updates_count: number
  }
  schedule_banner: ScheduleBannerData
  action_center: ActionCenterData
  live_snapshot: {
    checked_in_count: number
    looking_to_play_count: number
    friend_presence: UserSummary[]
    checked_in_players: UserSummary[]
    next_scheduled_session?: BannerItem | Record<string, unknown> | null
    active_sessions: Array<Record<string, unknown>>
    queue_count: number
    active_match_count: number
  }
  ranked: {
    queue: QueueEntry[]
    ready_lobbies: LobbySummary[]
    scheduled_lobbies: LobbySummary[]
    pending_lobbies: LobbySummary[]
    active_matches: MatchSummary[]
    challengeable_players: UserSummary[]
    queue_ready_cohort?: QueueReadyCohort | null
  }
  competitive_history: {
    leaderboard: LeaderboardEntry[]
    recent_matches: MatchSummary[]
  }
  details: {
    court: CourtSummary & Record<string, unknown>
    description?: string
    amenities: string[]
    community_info: Record<string, unknown>
    images: Array<Record<string, unknown>>
    upcoming_events: Array<Record<string, unknown>>
    pending_updates_count: number
  }
  chat_preview: {
    can_chat: boolean
    messages: ChatMessage[]
  }
}

export interface NotificationItem {
  id: number
  notif_type: string
  content: string
  reference_id?: number | null
  target_path?: string
  target_label?: string
  read: boolean
  created_at: string
}

export interface PresenceStatus {
  checked_in: boolean
  court_id?: number | null
  court_name?: string
  court_photo_url?: string
  looking_for_game?: boolean
  checked_in_at?: string | null
  last_presence_ping_at?: string | null
}

export interface PlaySessionParticipant {
  id: number
  session_id: number
  user_id: number
  status: 'joined' | 'invited' | 'waitlisted'
  joined_at?: string | null
  user?: UserSummary
}

export interface PlaySessionData {
  id: number
  creator_id: number
  court_id: number
  session_type: 'now' | 'scheduled'
  start_time: string | null
  end_time?: string | null
  game_type: 'open' | 'singles' | 'doubles'
  skill_level?: string
  max_players: number
  visibility: 'all' | 'friends'
  notes: string
  status: string
  creator?: UserSummary | null
  court?: CourtSummary | null
  players: PlaySessionParticipant[]
  created_at?: string | null
  series?: {
    id: number
    sequence: number
    occurrences: number
    interval_weeks: number
    recurrence: string
  } | null
}

export interface InboxThread {
  thread_type: 'direct' | 'session'
  thread_ref_id: number
  name: string
  subtitle?: string
  last_message_preview: string
  last_message_at?: string | null
  unread_count: number
  session_id?: number
  user?: UserSummary
}

export interface ChatMessage {
  id: number
  sender_id: number
  court_id?: number | null
  session_id?: number | null
  recipient_id?: number | null
  content: string
  msg_type: 'court' | 'session' | 'direct' | 'game'
  created_at: string
  sender?: UserSummary
}
