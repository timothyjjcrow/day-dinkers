import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import type { ChatMessage, InboxThread, UserSummary } from '../types'

interface InboxPageProps {
  currentUser: UserSummary | null
  onRequireAuth: () => void
}

function formatTime(value: string | null | undefined) {
  if (!value) return 'Now'
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function InboxPage({ currentUser, onRequireAuth }: InboxPageProps) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<'all' | 'direct' | 'sessions'>('all')
  const [activeThreadId, setActiveThreadId] = useState('')
  const [message, setMessage] = useState('')

  const inboxQuery = useQuery({
    queryKey: ['inbox', filter],
    queryFn: () => api.get<{ threads: InboxThread[] }>(`/api/chat/inbox?filter=${filter}`),
    enabled: Boolean(currentUser),
  })

  const threads = inboxQuery.data?.threads || []
  const activeThread = threads.find((thread) => `${thread.thread_type}-${thread.thread_ref_id}` === activeThreadId) || null

  useEffect(() => {
    if (!threads.length) {
      setActiveThreadId('')
      return
    }
    if (!activeThreadId) {
      setActiveThreadId(`${threads[0].thread_type}-${threads[0].thread_ref_id}`)
    }
  }, [activeThreadId, threads])

  const messagesQuery = useQuery({
    queryKey: ['inbox-thread', activeThread?.thread_type, activeThread?.thread_ref_id],
    queryFn: async () => {
      if (!activeThread) {
        return { messages: [] as ChatMessage[] }
      }
      const response = activeThread.thread_type === 'direct'
        ? await api.get<{ messages: ChatMessage[] }>(`/api/chat/direct/${activeThread.thread_ref_id}`)
        : await api.get<{ messages: ChatMessage[] }>(`/api/chat/session/${activeThread.thread_ref_id}`)

      await api.post('/api/chat/inbox/read', {
        thread_type: activeThread.thread_type,
        thread_ref_id: activeThread.thread_ref_id,
      })
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ['inbox'] }),
        queryClient.invalidateQueries({ queryKey: ['bootstrap'] }),
      ])
      return response
    },
    enabled: Boolean(currentUser && activeThread),
  })

  async function sendMessage() {
    if (!currentUser || !activeThread || !message.trim()) return
    try {
      await api.post('/api/chat/send', activeThread.thread_type === 'direct'
        ? {
            msg_type: 'direct',
            recipient_id: activeThread.thread_ref_id,
            content: message.trim(),
          }
        : {
            msg_type: 'session',
            session_id: activeThread.thread_ref_id,
            content: message.trim(),
          })
      setMessage('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['inbox'] }),
        queryClient.invalidateQueries({ queryKey: ['inbox-thread'] }),
        queryClient.invalidateQueries({ queryKey: ['bootstrap'] }),
      ])
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to send message')
    }
  }

  if (!currentUser) {
    return (
      <div className="page inbox-page">
        <div className="empty-card large">
          Sign in to see your messages, ranked invites, and session chats.
          <button type="button" className="primary-btn" onClick={onRequireAuth}>
            Sign In
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="page inbox-page">
      <section className="inbox-summary-card">
        <div className="section-kicker">Inbox</div>
        <strong>{activeThread ? activeThread.name : 'Messages'}</strong>
        <p>{threads.length ? 'Open a thread, reply fast, and jump back into play.' : 'New direct and session threads will land here.'}</p>
      </section>

      <div className="tab-row">
        {[
          ['all', 'All'],
          ['direct', 'Direct'],
          ['sessions', 'Sessions'],
        ].map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={filter === value ? 'active' : ''}
            onClick={() => setFilter(value as 'all' | 'direct' | 'sessions')}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="inbox-layout">
        <div className="thread-list">
          {threads.length ? (
            threads.map((thread) => {
              const threadKey = `${thread.thread_type}-${thread.thread_ref_id}`
              return (
                <button
                  key={threadKey}
                  type="button"
                  className={activeThreadId === threadKey ? 'thread-card active' : 'thread-card'}
                  onClick={() => setActiveThreadId(threadKey)}
                >
                  <div>
                    <strong>{thread.name}</strong>
                    <p>{thread.subtitle || thread.last_message_preview}</p>
                  </div>
                  <div className="thread-meta">
                    <span>{formatTime(thread.last_message_at)}</span>
                    {thread.unread_count ? <strong>{thread.unread_count}</strong> : null}
                  </div>
                </button>
              )
            })
          ) : (
            <div className="empty-card">No active threads yet.</div>
          )}
        </div>

        <section className="message-panel">
          {activeThread ? (
            <>
              <div className="message-panel-header">
                <div>
                  <strong>{activeThread.name}</strong>
                  <p>{activeThread.subtitle || 'Reply directly from the mobile shell.'}</p>
                </div>
              </div>

              <div className="message-list">
                {(messagesQuery.data?.messages || []).map((row) => (
                  <div
                    key={row.id}
                    className={row.sender_id === currentUser.id ? 'message-row mine' : 'message-row'}
                  >
                    <article className="message-bubble">
                      <strong>{row.sender?.name || row.sender?.username || 'Player'}</strong>
                      <p>{row.content}</p>
                      <span>{formatTime(row.created_at)}</span>
                    </article>
                  </div>
                ))}
              </div>

              <div className="composer-row">
                <input
                  type="text"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="Reply in a sentence..."
                />
                <button type="button" className="primary-btn" onClick={sendMessage}>
                  Send
                </button>
              </div>
            </>
          ) : (
            <div className="empty-card">Pick a thread to open the conversation.</div>
          )}
        </section>
      </div>
    </div>
  )
}
