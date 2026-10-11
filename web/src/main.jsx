import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const PAGE = 30
const clock = (value) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const duration = (seconds) => seconds >= 3600 ? `${(seconds / 3600).toFixed(1)} 小时` : `${Math.round(seconds / 60)} 分钟`
async function api(path, signal) {
  const response = await fetch(path, { signal })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : '无法获取数据，请稍后重试')
  }
  return response.json()
}
function App() {
  const [draft, setDraft] = useState({ text: '', start: '', end: '' })
  const [filters, setFilters] = useState(draft)
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)
  const [selected, setSelected] = useState(null)
  const [audioError, setAudioError] = useState('')
  const audio = useRef(null)
  useEffect(() => {
    if (!auto) return
    const timer = setInterval(() => setRevision(v => v + 1), 60000)
    return () => clearInterval(timer)
  }, [auto])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ text: filters.text, limit: PAGE, offset })
    if (filters.start) params.set('start', new Date(filters.start).toISOString())
    if (filters.end) params.set('end', new Date(filters.end).toISOString())
    setLoading(true)
    setError('')
    Promise.all([api(`/api/transcripts?${params}`, controller.signal), api('/api/stats', controller.signal)])
      .then(([result, summary]) => { setRows(result.items); setTotal(result.total); setStats(summary) })
      .catch(err => { if (err.name !== 'AbortError') setError(err.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [filters, offset, revision])
  function search(event) {
    event.preventDefault()
    if (draft.start && draft.end && draft.start >= draft.end) {
      setError('开始时间必须早于结束时间'); return
    }
    setOffset(0); setFilters({ ...draft }); setRevision(v => v + 1)
  }
  function select(row) {
    if (audio.current) audio.current.pause()
    setAudioError(''); setSelected(row)
  }
  return <div className="layout">
    <aside className="sidebar">
      <a className="brand" href="/"><span className="brand-mark">m<span>ı</span></span> mic spy</a>
      <div className="workspace"><span className="dot"/> 声音档案工作台</div>
      <div className="nav-title">工作空间</div>
      <div className="nav-active"><span>▤</span> 识别记录 <span className="nav-count">{stats?.transcripts ?? '—'}</span></div>
      <div className="sidebar-note"><span className="tiny-wave">▂ ▅ █ ▃ ▆ ▂</span><h3>声音，留下线索。</h3><p>通过文字找到那一刻，<br/>再听一次现场的声音。</p></div>
      <div className="sidebar-footer">MIC SPY / AUDIO ARCHIVE<br/><span>本地录音 · 文字检索</span></div>
    </aside>
    <main>
      <header><div className="breadcrumb">工作空间 <span>/</span> 声音档案</div><div className="update-status"><span className={`dot ${error ? 'offline' : ''}`}/>{error ? '连接需要检查' : loading ? '正在同步' : '已同步'}</div></header>
      <section className="heading"><div><div className="eyebrow">AUDIO ARCHIVE</div><h1>每一段声音，都有迹可循<span>。</span></h1><p>检索识别文字，回到录音发生的时刻。</p></div><button className="refresh" onClick={() => setRevision(v => v + 1)} disabled={loading}>↻ 刷新记录</button></section>
      <section className="stats" aria-label="档案统计">
        <div><span>识别句段</span><strong>{stats?.transcripts?.toLocaleString() ?? '—'}<small>条</small></strong><p>已保存的文字记录</p></div>
        <div><span>录音时长</span><strong>{stats ? duration(stats.audio_seconds) : '—'}</strong><p>{stats?.segments ?? '—'} 个音频分段</p></div>
        <div><span>最近记录</span><strong className="last-time">{stats?.newest_time ? new Date(stats.newest_time).toLocaleTimeString('zh-CN', {hour12: false}) : '—'}</strong><p>{stats?.newest_time ? new Date(stats.newest_time).toLocaleDateString('zh-CN') : '等待第一段声音'}</p></div>
      </section>
      <section className="archive-panel">
        <div className="panel-title"><h2>识别记录 <span>{total}</span></h2><label className="auto"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)}/> 每分钟刷新</label></div>
        <form onSubmit={search} className="filters">
          <label className="search-field"><span>关键词</span><input placeholder="搜索录音中的文字…" value={draft.text} onChange={e => setDraft({ ...draft, text: e.target.value })}/></label>
          <label><span>开始时间</span><input type="datetime-local" value={draft.start} onChange={e => setDraft({ ...draft, start: e.target.value })}/></label>
          <label><span>结束时间</span><input type="datetime-local" value={draft.end} onChange={e => setDraft({ ...draft, end: e.target.value })}/></label>
          <button className="primary" type="submit">查找记录 <span>→</span></button>
        </form>
        <div className="table-meta"><span>时间以当前浏览器时区显示</span><span>录音每分钟保存，最新文字可能稍有延迟</span></div>
        {error ? <div className="state error" role="alert"><strong>暂时无法读取档案</strong><p>{error}</p><button onClick={() => setRevision(v => v + 1)}>重新连接</button></div>
          : loading && !rows.length ? <div className="state" role="status">正在读取声音档案…</div>
          : !rows.length ? <div className="state"><span className="empty-icon">≋</span><strong>这里还没有匹配的声音</strong><p>调整关键词或时间范围；新录音会在保存后出现。</p></div>
          : <div className="table-wrap"><table><thead><tr><th>录音时间</th><th>识别文字</th><th>时长</th><th>回放</th></tr></thead><tbody>{rows.map(row => <tr key={row.id} className={selected?.id === row.id ? 'selected' : ''}>
            <td className="time-cell"><strong>{clock(row.start_time)}</strong><span>会话 {row.session_id.slice(0, 8)}</span></td>
            <td className="text-cell">{row.text}</td><td className="seconds">{row.duration_seconds.toFixed(1)}s</td>
            <td><button className="play" aria-label={`回放 ${row.text}`} onClick={() => select(row)}><span>▶</span> 回放</button></td>
          </tr>)}</tbody></table></div>}
        <footer className="pagination"><span>{total ? `第 ${Math.floor(offset / PAGE) + 1} 页 · 共 ${total} 条` : '共 0 条'}</span><div><button disabled={offset === 0 || loading} onClick={() => setOffset(v => Math.max(0, v - PAGE))}>← 上一页</button><button disabled={offset + PAGE >= total || loading} onClick={() => setOffset(v => v + PAGE)}>下一页 →</button></div></footer>
      </section>
      <div className="footnote">句段包含前后静音。网络采集时间以处理端接收为基准。</div>
      {selected && <section className="player" aria-label="录音回放"><div className="player-icon">▶</div><div className="player-copy"><span>{clock(selected.start_time)} · {selected.duration_seconds.toFixed(1)} 秒</span><strong>{selected.text}</strong>{audioError && <p role="alert">{audioError}</p>}</div><audio ref={audio} key={selected.id} controls autoPlay src={selected.audio_url} onError={() => setAudioError('录音无法播放，可能已过期清理。请刷新记录。')}/><a className="download" href={selected.audio_url} download={`recording-${selected.id}.wav`}>下载</a><button className="close" aria-label="关闭回放" onClick={() => { audio.current?.pause(); setSelected(null) }}>×</button></section>}
    </main>
  </div>
}
createRoot(document.getElementById('root')).render(<App/> )
