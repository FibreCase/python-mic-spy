# mic-spy

持续采集麦克风声音，实时识别中文，将录音与文字关联保存，并通过 Web 面板检索、回放。

支持 Armbian 本机运行，也支持将麦克风采集与识别存储部署到两台设备。默认 **48 kHz 单声道采集、每分钟集中写盘、保留 7 天**。前端使用 React + Vite，HTTP API 使用 FastAPI，识别使用 sherpa-onnx Zipformer2 CTC。

## 1. 安装与准备

在项目根目录运行：

```sh
sudo apt-get install alsa-utils ffmpeg
uv sync --locked --no-dev
```

需要 Python 3.11+、uv 和支持设备架构的 sherpa-onnx wheel。使用 Web 时还需 Node.js 20.19+ 或 22.12+ 及 npm；采集者无需加载模型，但目前各角色共用同一套 Python 依赖。

检查麦克风及 Opus 编码器：

```sh
arecord -l
ffmpeg -hide_banner -h encoder=libopus
```

当前设备使用 `plughw:0,0`。识别端需要已有模型目录：

```text
sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01/
├── model.int8.onnx
└── tokens.txt
```

将目录放到项目根目录，程序即可使用默认路径。放在其他位置时，在 `run`、`processor` 或 `full` 命令后加：

```sh
--model /模型目录/model.int8.onnx --tokens /模型目录/tokens.txt
```

以下命令均在项目根目录运行；相对路径以当前工作目录为准。全局参数 `--data` 必须放在子命令之前。

## 2. 快速启动

### 同一台设备：全部功能

```sh
uv run mic-spy --data ./data full \
  --device plughw:0,0 --host 0.0.0.0
```

这会启动本机录音、识别、存储、Web 和 API。浏览器访问 `http://设备IP:8000`。前端自动安装依赖并构建，无需手动运行 npm。

### 两台设备：采集者 + 处理者

先在处理设备放好模型并启动：

```sh
uv run mic-spy --data ./data full --source udp \
  --listen udp://0.0.0.0:5000 --host 0.0.0.0
```

再在 Armbian 采集设备启动，将示例 IP 替换成处理设备地址：

```sh
uv run mic-spy collector \
  --device plughw:0,0 --target udp://192.168.1.100:5000
```

采集端不需要模型。处理端保存音频、识别文字并提供 Web。网络路径为：

```text
麦克风 → 48 kHz 采集 → Opus / MPEG-TS → UDP
                                        ↓
                      解码 → 识别 → WAV + SQLite → Web/API
```

处理端需允许 UDP 5000 和 Web 的 TCP 8000。每个采集端使用独立监听端口、数据目录和处理进程。

## 3. 单功能启动

| 命令 | 功能 | 需要模型 | 自动构建 Web |
| --- | --- | --- | --- |
| `collector` | 采集、Opus 编码、UDP 发送 | 否 | 否 |
| `processor` | UDP 接收、识别、存储 | 是 | 否 |
| `run` | 本机采集、识别、存储 | 是 | 否 |
| `serve` | Web 面板、HTTP API | 否 | 是 |
| `full` | 本机或 UDP 音频处理，以及 Web/API | 是 | 是 |
| `query` / `export` | 命令行检索 / 回放导出 | 否 | 否 |

```sh
# 本机录音与识别，不启动 Web
uv run mic-spy --data ./data run --device plughw:0,0

# 只接收、识别和存储
uv run mic-spy --data ./data processor --listen udp://0.0.0.0:5000

# 只启动 Web/API
uv run mic-spy --data ./data serve --host 0.0.0.0
```

分开运行处理和 Web 时，两者的 `--data` 必须指向同一目录。`serve` 不创建数据库或录音，尚无数据库时页面会提示无法读取档案。

同一数据目录只能有一个录音处理实例。运行 `full` 时，不要再针对该目录启动 `run` 或 `processor`，也不要在同一 Web 端口启动另一个 `serve`。

## 4. 配置参数

### 采集与处理

| 参数 | 默认值 | 适用命令 / 说明 |
| --- | --- | --- |
| `--data` | `./data` | 全局参数，放在子命令前 |
| `--sample-rate` | `48000` | `collector`、`run`、`processor`、`full`；支持 8000、12000、16000、24000、48000 |
| `--device` | `plughw:0,0` | `collector`、`run`、`full --source local` |
| `--target` | 必填 | `collector`；例如 `udp://192.168.1.100:5000` |
| `--bitrate` | `24000` | `collector`；Opus 码率，单位 bit/s |
| `--listen` | `udp://0.0.0.0:5000` | `processor`、`full --source udp` |
| `--receive-timeout` | `15` | UDP 接收无数据超时，单位秒 |
| `--source` | `local` | `full`；可选 `local`、`udp` |
| `--model` / `--tokens` | 上述模型目录内的文件 | `run`、`processor`、`full` |
| `--threads` | `2` | 识别推理线程数 |
| `--max-utterance` | `20` | 最长识别句段，单位秒 |
| `--segment-seconds` | `300` | WAV 文件分段长度，单位秒 |
| `--flush-seconds` | `60` | 音频与文字批量写入间隔，单位秒 |
| `--retention-days` | `7` | 录音及关联文字保留天数 |

识别、存储参数适用于 `run`、`processor`、`full`。网络两端建议设置相同采样率，处理端会将收到的音频转换为指定录音采样率。识别模型的特征采样率保持 16 kHz，由 sherpa-onnx 内部重采样；文字定位使用录音的采样率。旧的 16 kHz 录音仍可直接回放。

### Web 与自动构建

以下参数适用于 `serve`、`full`：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | `0.0.0.0` 允许局域网访问 |
| `--port` | `8000` | HTTP 端口 |
| `--web-source` | `web` | 前端源目录 |
| `--web-dist` | `web/dist` | 前端构建输出 / 静态文件目录 |
| `--no-build` | 不启用 | 使用已有构建结果，或仅运行 API |

默认启动前执行 `npm run build`。首次运行、依赖配置变化或依赖缺失时，先执行 `npm ci --include=dev`。安装或构建失败会退出，不继续启动服务；`full` 只构建一次。构建完成后由 Python 提供静态页面，无需 Node.js 常驻。

预先构建部署示例：

```sh
npm --prefix web ci
npm --prefix web run build
uv run mic-spy --data ./data serve --no-build --host 0.0.0.0
```

## 5. 查询与回放

Web 支持关键词、时间范围、分页、档案统计、音频回放及下载，每分钟自动刷新。时间显示为浏览器本地时区。

“完整录音”区可按日期浏览所有录音，包括静音及没有识别文字的部分，也可输入精确到秒的时间，定位到包含该时刻的分段并从对应位置播放。播放器支持拖动到分段内任意位置及下载；其他分段通过日期列表选择。指定时刻不存在录音时会提示，不自动跳过缺口。正在录制的分段只能回放已批量写盘部分。

命令行查询输出 JSON 行，`export` 按文字 ID 导出句段，可跨越 WAV 文件边界：

```sh
uv run mic-spy --data ./data query --text 测试 --limit 20
uv run mic-spy --data ./data export 1 ./clip.wav
aplay ./clip.wav
```

HTTP API 文档在 `http://设备IP:8000/docs`：

| 接口 | 功能 |
| --- | --- |
| `GET /api/health` | 服务状态及数据库是否存在 |
| `GET /api/stats` | 句段数量、录音时长、最早与最新记录 |
| `GET /api/transcripts` | 检索；支持 `text`、`start`、`end`、`session_id`、`limit`（1–100）、`offset` |
| `GET /api/recordings` | 所有录音分段；支持 `start`、`end`、`at`、`limit`、`offset` |
| `GET /api/recordings/{id}/audio` | 完整 WAV 分段，支持字节范围与任意位置回放 |
| `GET /api/transcripts/{id}/audio` | WAV 回放，支持字节范围请求 |

`start`、`end` 使用带时区的 ISO 8601 时间，匹配与指定范围重叠的句段。纳秒字段以字符串输出，避免 JavaScript 精度损失。API 使用只读数据库连接，文字句段回放在内存中生成，完整分段直接读取已有 WAV，不新增磁盘文件。缺失文字返回 404，缺失或不完整音频返回 410；文字句段单次回放上限 10 分钟，超出时使用命令行导出；完整录音分段不受该限制。

## 6. 录音管理与时间定位

### 每分钟写盘

音频和文字先缓存在内存中，每 60 秒集中写入 WAV，并通过一个 SQLite 事务提交文字及录音索引。正常停止或捕获异常退出时立即保存剩余缓存；初始化、会话创建及过期清理仍会写盘。

48 kHz 下每分钟音频缓存约 5.76 MB。查询只能看到已提交的数据，最新文字可能延迟约一分钟。WAV 默认每 5 分钟分段，与写盘间隔独立。突然断电或强制结束进程可能丢失最近一分钟的缓存；退出时尚未处理的采集队列数据也可能丢失。

### 自动保留 7 天

启动时及运行中每小时检查一次。分段结束时间严格早于“当前 UTC 时间减去保留天数”时，删除已关闭 WAV 及与其重叠的文字；跨分段句子也会清理。正在写入的文件及跨越截止时间的分段暂时保留。

空的历史会话和目录会清理。文件删除失败会记录日志，下一次重试。SQLite 空间可重复利用，但数据库文件不会立即缩小。单独导出的回放文件不在清理范围内。只有录音处理进程执行保留策略，单独运行 `serve` 不会清理。

48 kHz 单声道 16 位 PCM 每天约 **8.29 GB**，7 天约 **58.06 GB**，需额外预留数据库与运行空间。磁盘满会报错重试。

### 时间与网络边界

文字对应识别句段，包含前后静音，不是逐字时间戳。会话内区间左闭右开，以采样位置关联音频；UTC 时间由会话起点加采样时长计算，建议设备开启 NTP。

本机起点包含设备启动及缓冲延迟。UDP 模式以处理端收到第一块解码音频的时间为起点，不是采集设备的精确时间。Opus 为有损编码，保存的是解码后的音频。

UDP 不含确认、采集端缓存或断网补传；丢包和短暂中断可能造成音频缺失及时间压缩。无包超时或接收缓冲溢出会结束会话，5 秒后重试。采集端进程正常不代表处理端已收到音频。当前没有传输加密或 Web 登录认证，适用于受信任的局域网。

启动时可恢复未关闭 WAV 的数据库结束位置。音频文件与 SQLite 不构成原子事务，硬断电造成的损坏可能需要人工修复。

## 7. systemd 部署

将项目放到 `/opt/mic-spy`，模型放在项目默认目录或在服务中指定绝对路径。先确认前台模式正常，再选择所需服务：

| 服务文件 | 用途 |
| --- | --- |
| `deploy/mic-spy-full.service` | 本机全功能；可改为 `--source udp` |
| `deploy/mic-spy-collector.service` | 采集发送；需修改处理端 IP |
| `deploy/mic-spy-processor.service` | UDP 识别存储 |
| `deploy/mic-spy-web.service` | 独立 Web/API |
| `deploy/mic-spy.service` | 本机录音识别，不含 Web |

全功能部署示例：

```sh
cd /opt/mic-spy
uv sync --locked --no-dev
sudo useradd --system --no-create-home --groups audio micspy
sudo cp deploy/mic-spy-full.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mic-spy-full
sudo journalctl -u mic-spy-full -f
```

已有 `micspy` 用户时跳过创建步骤。服务用户需要读取项目、虚拟环境和模型。录音服务由 systemd 创建并授权 `/var/lib/mic-spy`；独立 Web 服务需要读取该目录、SQLite WAL 及音频。

默认 Web 启动会自动构建，服务用户需要可用的 npm 及 `web/node_modules`、`web/dist` 写权限。若预先构建，在 service 的 `ExecStart` 末尾加 `--no-build`，即可避免运行时安装依赖。服务直接使用虚拟环境，不执行 uv 安装。

Ctrl+C/SIGTERM 会保存已处理的缓存并关闭子进程。`full` 的 Web 异常退出会停止全功能模式并返回失败，供 systemd 重启。

## 8. 开发与项目结构

```text
src/mic_spy/
├── cli.py          # 命令与组件组装
├── contracts.py    # 音频来源、识别器、存储接口
├── source.py       # ALSA 来源
├── transport.py    # FFmpeg Opus/UDP 发送与接收
├── recognition.py  # sherpa-onnx 流式识别
├── runtime.py      # 采集队列与生命周期
├── storage.py      # 内存缓存、WAV、SQLite
├── retention.py    # 过期清理
├── query.py        # 只读查询、跨文件回放导出
├── api.py          # HTTP API 与静态页面
└── web_build.py    # 前端自动安装与构建
web/                # React + Vite 前端
```

默认数据目录结构：

```text
data/
├── archive.sqlite3
├── capture.lock
└── audio/<session_id>/<start_sample>.wav
```

SQLite 使用 WAL；`sessions` 保存会话与采样率，`recordings` 保存音频路径及采样区间，`transcripts` 保存文字与时间。读取与识别解耦，替换声音来源或识别器时实现 `contracts.py` 接口即可。

安装开发依赖并测试：

```sh
uv sync --locked
uv run pytest
```

前端开发：先启动后端 `serve`，再执行 `npm --prefix web run dev`。Vite 将 `/api` 代理到 `http://127.0.0.1:8000`。自动化测试覆盖存储、保留策略、批量写盘、HTTP API、启动模式及 FFmpeg UDP 链路；实际麦克风采集需在目标 Armbian 上验证。

## 9. 常见问题

- **Missing file: model.int8.onnx**：模型不在默认目录。用 `--model`、`--tokens` 指定实际路径；仅发送音频时使用 `collector`。
- **npm 或前端构建失败**：检查 Node.js 版本、依赖下载网络和目录写权限。预先构建后可用 `--no-build`。
- **页面没有新记录**：确认处理进程正在运行、Web 与处理使用同一数据目录，并等待一分钟批量写入。
- **UDP 接收超时**：检查采集端目标 IP、UDP 端口、防火墙及 FFmpeg 日志。
- **数据目录被占用 / Web 端口被占用**：检查是否同时启动了前台、systemd 或另一个 `full` 实例。
