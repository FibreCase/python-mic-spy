import React, { useEffect, useState } from 'react'
const localDate = (value = new Date()) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
const time = value => new Date(value).toLocaleTimeString('zh-CN', { hour12: false })
async function load(params, signal) {
  const response = await fetch(`/api/recordings?${params}`, { signal })
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '无法读取录音')
  return body
}
export default function Recordings({ revision, onPlay }) {
  const [day, setDay] = useState(localDate())
  const [position, setPosition] = useState('')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState({ items: [], total: 0 })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [locating, setLocating] = useState(false)
  useEffect(() => {
    if (!day) return
    const controller = new AbortController()
    const start = new Date(`${day}T00:00:00`)
    const end = new Date(start); end.setDate(end.getDate() + 1)
    const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString(), limit: 12, offset })
    setLoading(true); setError('')
    load(params, controller.signal).then(setData)
      .catch(err => { if (err.name !== 'AbortError') setError(err.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [day, offset, revision])
  function play(row) {
    onPlay({ ...row, id: `recording-${row.id}-${row.seek_seconds}`, text: '完整录音 · 包含静音与背景声音', seek_seconds: row.seek_seconds || 0 })
  }
  async function locate(event) {
    event.preventDefault()
    if (!position) return
    setLocating(true); setNotice('')
    try {
      const result = await load(new URLSearchParams({ at: new Date(position).toISOString(), limit: 1 }))
      if (!result.items.length) setNotice('这个时刻没有已保存的录音，可能尚未写盘、采集中断或已过期。')
      else { play(result.items[0]); setDay(position.slice(0, 10)); setOffset(0) }
    } catch (err) { setNotice(err.message) }
    finally { setLocating(false) }
  }
  return <section className="archive-panel recording-panel" aria-label="完整录音">
    <div className="panel-title"><div><h2>完整录音 <span>{data.total}</span></h2><p className="recording-description">不依赖识别文字，静音和背景声音也可以回放。</p></div></div>
    <div className="recording-tools">
      <label><span>浏览日期</span><input type="date" required value={day} onChange={e => { if (e.target.value) { setDay(e.target.value); setOffset(0) } }}/></label>
      <form onSubmit={locate}><label><span>跳到指定时刻</span><input type="datetime-local" step="1" required value={position} onChange={e => setPosition(e.target.value)}/></label><button className="primary" disabled={locating} type="submit">{locating ? '定位中…' : '定位并播放 →'}</button></form>
    </div>
    {notice && <p className="recording-notice" role="status">{notice}</p>}
    <div className="table-meta"><span>选择分段后，可拖动播放器到任意位置</span><span>显示已写盘的录音 · 浏览器本地时区</span></div>
    {error ? <div className="state error" role="alert">{error}</div> : loading ? <div className="state">正在读取录音…</div>
      : !data.items.length ? <div className="state"><strong>这一天没有已保存的录音</strong><p>请选择其他日期，或等待下一次批量保存。</p></div>
      : <div className="recording-grid">{data.items.map(row => <button className="recording-card" key={row.id} onClick={() => play(row)}>
        <span className="recording-card-top"><span className="tiny-wave">▂ ▅ █ ▃ ▆ ▂</span><span>{row.closed ? '已保存' : '录制中 · 已保存部分'}</span></span>
        <strong>{time(row.start_time)} — {time(row.end_time)}</strong>
        <span>{(row.duration_seconds / 60).toFixed(1)} 分钟 · {row.sample_rate / 1000} kHz <span className="recording-play">▶ 回放</span></span>
      </button>)}</div>}
    <footer className="pagination"><span>共 {data.total} 段{data.total > 0 && ` · 第 ${Math.floor(offset / 12) + 1} 页`}</span><div><button disabled={!offset || loading} onClick={() => setOffset(v => Math.max(0, v - 12))}>← 上一页</button><button disabled={offset + 12 >= data.total || loading} onClick={() => setOffset(v => v + 12)}>下一页 →</button></div></footer>
  </section>
}
